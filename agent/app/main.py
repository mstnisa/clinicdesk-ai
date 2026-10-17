"""ClinicDesk AI — HTTP API.

Public:   /api/chat (website widget), /webhooks/whatsapp, /widget, /health
Staff:    /dashboard and /api/admin/* (Bearer ADMIN_TOKEN)
n8n:      /api/admin/bookings/upcoming, /api/admin/outreach/* (same token)
"""
from __future__ import annotations

import hmac
import logging
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from . import db
from .agent import guardrails as g
from .agent.orchestrator import conversation_cost_bdt, handle_message
from .agent.tools import KB, local_now
from .channels import whatsapp
from .config import BASE_DIR, settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    weak = settings.admin_token in ("", "change-me") or len(settings.admin_token) < 16
    if weak and settings.llm_provider != "mock" and not settings.demo_mode:
        # Real patient data is about to flow: refuse to run with the demo admin token.
        raise RuntimeError("Set a strong ADMIN_TOKEN (16+ random characters) before using a real model provider.")
    db.init_db()
    yield


app = FastAPI(title="ClinicDesk AI", version="1.0.0", lifespan=lifespan)
STATIC = BASE_DIR / "static"
_seen_wa_ids: deque[str] = deque(maxlen=2000)  # WhatsApp may deliver the same message twice


def admin(authorization: str = Header(default="")) -> None:
    if not hmac.compare_digest(authorization.encode(), f"Bearer {settings.admin_token}".encode()):
        raise HTTPException(401, "Missing or wrong admin token")


# ------------------------------------------------------------------ public
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/dashboard")


@app.get("/health")
def health():
    return {"ok": True, "provider": settings.llm_provider, "knowledge_version": KB.version,
            "approved_sections": len(KB.sections), "excluded_pages": KB.excluded}


class ChatIn(BaseModel):
    sender_id: str = Field(..., min_length=3, max_length=64)
    message: str = Field(..., min_length=1, max_length=2000)


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    # sender_id comes from the browser, so also rate-limit by client IP
    r = handle_message("web", body.sender_id, body.message, client_key=request.client.host if request.client else "")
    return {"reply": r.text, "route": r.route, "language": r.language, "tools": r.tools_used,
            "conversation_id": r.conversation_id, "conversation_cost_bdt": r.cost_bdt}


@app.get("/webhooks/whatsapp", response_class=PlainTextResponse)
def whatsapp_verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == settings.whatsapp_verify_token:
        return q.get("hub.challenge", "")
    raise HTTPException(403, "Verification failed")


def _process_whatsapp(sender: str, text: str) -> None:
    try:
        reply = handle_message("whatsapp", sender, text)
        whatsapp.send_text(sender, reply.text)
    except Exception:  # never fail silently on a patient message
        logging.getLogger("clinicdesk").exception("WhatsApp message from %s failed", g.mask_sender(sender))
        whatsapp.send_text(sender, "Sorry, something went wrong. A staff member will reply here soon.")


@app.post("/webhooks/whatsapp")
async def whatsapp_receive(request: Request, tasks: BackgroundTasks):
    raw = await request.body()
    if not whatsapp.verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        db.insert_row(db.security_events, kind="bad_signature", detail="WhatsApp webhook signature mismatch")
        raise HTTPException(401, "Bad signature")
    payload = await request.json()
    for msg_id, sender, text in whatsapp.incoming_text_messages(payload):
        if msg_id in _seen_wa_ids:
            continue
        _seen_wa_ids.append(msg_id)
        tasks.add_task(_process_whatsapp, sender, text)  # reply after returning 200 to Meta
    return {"ok": True}


@app.get("/widget", include_in_schema=False)
def widget():
    return FileResponse(STATIC / "widget.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "dashboard.html")


# ------------------------------------------------------------------- staff
def _since(days: int):
    return db.now() - timedelta(days=days)


@app.get("/api/admin/stats", dependencies=[Depends(admin)])
def stats(days: int = 30):
    since = _since(days)
    c = db.conversations.c
    convs = db.rows(db.select(c.id).where(c.created_at >= since))
    cost = db.rows(db.select(db.func.coalesce(db.func.sum(db.llm_calls.c.cost_usd), 0.0).label("usd"),
                             db.func.count().label("calls"),
                             db.func.coalesce(db.func.sum(db.llm_calls.c.input_tokens), 0).label("inp"),
                             db.func.coalesce(db.func.sum(db.llm_calls.c.cached_tokens), 0).label("cached"),
                             db.func.coalesce(db.func.sum(db.llm_calls.c.output_tokens), 0).label("out"))
                   .where(db.llm_calls.c.created_at >= since))[0]
    routes = db.rows(db.select(db.messages.c.route, db.func.count().label("n"))
                     .where((db.messages.c.role == "assistant") & (db.messages.c.created_at >= since))
                     .group_by(db.messages.c.route))
    n_conv = len(convs)
    usd = float(cost["usd"])
    count = lambda t, *w: db.rows(db.select(db.func.count().label("n")).select_from(t).where(*w))[0]["n"]
    return {
        "days": days,
        "conversations": n_conv,
        "leads": count(db.leads, db.leads.c.created_at >= since),
        "bookings": count(db.bookings, db.bookings.c.created_at >= since),
        "open_escalations": count(db.escalations, db.escalations.c.status == "open"),
        "security_events": count(db.security_events, db.security_events.c.created_at >= since),
        "llm_calls": cost["calls"],
        "tokens": {"input": cost["inp"], "cached": cost["cached"], "output": cost["out"]},
        "cost_usd": round(usd, 4),
        "cost_bdt": round(usd * settings.usd_to_bdt, 2),
        "cost_per_conversation_bdt": round(usd * settings.usd_to_bdt / n_conv, 3) if n_conv else 0,
        "replies_by_route": {r["route"] or "agent": r["n"] for r in routes},
        "provider": settings.llm_provider,
        "knowledge_version": KB.version,
    }


@app.get("/api/admin/conversations", dependencies=[Depends(admin)])
def list_conversations(limit: int = 50):
    c = db.conversations.c
    out = db.rows(db.select(c.id, c.channel, c.sender_masked, c.language, c.status, c.updated_at)
                  .order_by(c.updated_at.desc()).limit(limit))
    for row in out:
        row["cost_bdt"] = conversation_cost_bdt(row["id"])
    return out


@app.get("/api/admin/conversations/{cid}", dependencies=[Depends(admin)])
def get_conversation(cid: int):
    m = db.messages.c
    return db.rows(db.select(m.role, m.content, m.route, m.created_at)
                   .where(m.conversation_id == cid).order_by(m.id))


def _table(t, limit=100, *where):
    stmt = db.select(t).order_by(t.c.id.desc()).limit(limit)
    for w in where:
        stmt = stmt.where(w)
    return db.rows(stmt)


@app.get("/api/admin/leads", dependencies=[Depends(admin)])
def list_leads():
    return _table(db.leads)


@app.get("/api/admin/bookings", dependencies=[Depends(admin)])
def list_bookings():
    return _table(db.bookings)


@app.get("/api/admin/bookings/upcoming", dependencies=[Depends(admin)])
def upcoming_bookings(hours: int = 24):
    """For the n8n reminder workflow: confirmed bookings starting in the next N hours."""
    now = local_now()
    lo, hi = now.strftime("%Y-%m-%d %H:%M"), (now + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    b = db.bookings.c
    return db.rows(db.select(db.bookings).where((b.status == "confirmed") & (b.slot_start >= lo) & (b.slot_start <= hi)))


@app.get("/api/admin/escalations", dependencies=[Depends(admin)])
def list_escalations():
    return _table(db.escalations)


@app.post("/api/admin/escalations/{eid}/resolve", dependencies=[Depends(admin)])
def resolve_escalation(eid: int):
    """Staff finished the handover: the bot may answer this patient again."""
    row = db.rows(db.select(db.escalations.c.conversation_id).where(db.escalations.c.id == eid))
    if not row:
        raise HTTPException(404, "Not found")
    db.execute(db.update(db.escalations).where(db.escalations.c.id == eid).values(status="resolved"))
    cid = row[0]["conversation_id"]
    still_open = db.rows(db.select(db.escalations.c.id).where(
        (db.escalations.c.conversation_id == cid) & (db.escalations.c.status == "open")))
    if not still_open:
        db.execute(db.update(db.conversations).where(db.conversations.c.id == cid).values(status="open"))
    return {"ok": True}


@app.get("/api/admin/security", dependencies=[Depends(admin)])
def list_security():
    return _table(db.security_events)


class OutreachIn(BaseModel):
    company: str
    website: str = ""
    city: str = ""
    email: str = ""
    icp_score: int = 0
    icp_reasons: str = ""
    subject: str
    body: str


@app.post("/api/admin/outreach", dependencies=[Depends(admin)])
def create_outreach(item: OutreachIn):
    oid = db.insert_row(db.outreach, **item.model_dump(), status="pending_approval")
    return {"id": oid, "status": "pending_approval"}


@app.get("/api/admin/outreach", dependencies=[Depends(admin)])
def list_outreach(status: str = ""):
    return _table(db.outreach, 200, *( [db.outreach.c.status == status] if status else []))


@app.post("/api/admin/outreach/{oid}/{action}", dependencies=[Depends(admin)])
def outreach_action(oid: int, action: str):
    """approve / reject happen in the dashboard (the human step); `sent` is set by n8n after sending."""
    new = {"approve": "approved", "reject": "rejected", "sent": "sent", "followed_up": "followed_up"}.get(action)
    if not new:
        raise HTTPException(400, "action must be approve, reject, sent or followed_up")
    current = db.rows(db.select(db.outreach.c.status).where(db.outreach.c.id == oid))
    if not current:
        raise HTTPException(404, "Not found")
    if new == "sent" and current[0]["status"] != "approved":
        raise HTTPException(409, "Only approved messages can be sent")  # the approval rule, enforced in code
    if new in ("approved", "rejected") and current[0]["status"] != "pending_approval":
        raise HTTPException(409, "Only drafts waiting for approval can be approved or rejected")
    if new == "followed_up" and current[0]["status"] != "sent":
        raise HTTPException(409, "Only sent messages can be marked followed_up")
    db.execute(db.update(db.outreach).where(db.outreach.c.id == oid).values(status=new, updated_at=db.now()))
    return {"id": oid, "status": new}


@app.post("/api/admin/knowledge/reload", dependencies=[Depends(admin)])
def reload_knowledge():
    KB.reload()
    return {"knowledge_version": KB.version, "approved_sections": len(KB.sections), "excluded_pages": KB.excluded}
