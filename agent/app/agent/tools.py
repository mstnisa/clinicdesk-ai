"""Tools the agent can call. Each tool validates its own input: the model
proposes, the code decides. Tool results are plain JSON strings."""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
import yaml
from sqlalchemy.exc import IntegrityError

from .. import db
from ..config import settings
from ..knowledge.retriever import KnowledgeBase

KB = KnowledgeBase(settings.knowledge_dir)
_PHONE = re.compile(r"^(?:\+?88)?01[3-9]\d{8}$")


@lru_cache(maxsize=1)
def clinic() -> dict[str, Any]:
    return yaml.safe_load(settings.clinic_file.read_text(encoding="utf-8"))


def local_now() -> datetime:
    return datetime.now(ZoneInfo(clinic()["timezone"]))


@dataclass
class ToolContext:
    conversation_id: int
    channel: str
    sender_id: str
    language: str
    used: list[str] = None

    def __post_init__(self):
        self.used = []


# --------------------------------------------------------------- n8n events
def notify(event: str, payload: dict[str, Any]) -> None:
    """Fire-and-forget event to n8n (Sheets/CRM sync, staff alerts, reminders)."""
    if not settings.n8n_webhook_url:
        return

    def _send():
        try:
            headers = {"X-Webhook-Secret": settings.n8n_webhook_secret} if settings.n8n_webhook_secret else {}
            httpx.post(settings.n8n_webhook_url, json={"event": event, **payload}, headers=headers, timeout=10)
        except httpx.HTTPError:
            pass  # n8n down must never break a patient conversation

    threading.Thread(target=_send, daemon=True).start()


# ------------------------------------------------------------------ tools
def search_knowledge(ctx: ToolContext, query: str) -> dict:
    hits = KB.search(query, k=3)
    if not hits:
        return {"results": [], "instruction": "No approved clinic information matches. Do NOT answer from "
                "general knowledge. Say the clinic has no approved information on this yet and offer an "
                "assessment booking or a call from a physiotherapist."}
    return {"results": [{"citation": s.citation, "text": s.text_en, "text_bn": s.text_bn, "score": sc}
                        for s, sc in hits]}


def get_clinic_info(ctx: ToolContext) -> dict:
    c = clinic()
    return {k: c[k] for k in ("name", "address", "phone", "hours", "services", "payment", "cancellation")}


def _slots(days_ahead: int = 7, service: str = "assessment") -> list[datetime]:
    c = clinic()
    tz = ZoneInfo(c["timezone"])
    step = timedelta(minutes=c["slot_minutes"])
    taken = {r["slot_start"] for r in db.rows(
        db.select(db.bookings.c.slot_start).where(db.bookings.c.status == "confirmed"))}
    out, start = [], local_now() + timedelta(hours=2)
    for d in range(days_ahead + 1):
        day = (start + timedelta(days=d)).date()
        hours = c["hours"].get(day.strftime("%A"))
        if not hours:
            continue
        t = datetime.combine(day, datetime.strptime(hours[0], "%H:%M").time(), tz)
        end = datetime.combine(day, datetime.strptime(hours[1], "%H:%M").time(), tz)
        while t + step <= end:
            if t >= start and t.strftime("%Y-%m-%d %H:%M") not in taken:
                out.append(t)
            t += step
    return out


def check_availability(ctx: ToolContext, day: str = "", service: str = "assessment") -> dict:
    slots = _slots()
    if day:
        day = day.strip().lower()
        if day in ("today", "আজ"):
            day = local_now().strftime("%Y-%m-%d")
        elif day in ("tomorrow", "আগামীকাল", "কাল"):
            day = (local_now() + timedelta(days=1)).strftime("%Y-%m-%d")
        filtered = [s for s in slots if s.strftime("%Y-%m-%d") == day or s.strftime("%A").lower() == day]
        slots = filtered or slots
    # Spread suggestions across days instead of six back-to-back slots.
    picked, seen_days = [], {}
    for s in slots:
        key = s.date()
        if seen_days.get(key, 0) < 2:
            picked.append(s)
            seen_days[key] = seen_days.get(key, 0) + 1
        if len(picked) == 6:
            break
    return {"service": service,
            "slots": [{"slot_start": s.strftime("%Y-%m-%d %H:%M"), "weekday": s.strftime("%A")} for s in picked],
            "note": "Ask for the patient's name and mobile number before booking."}


def _qualify(condition: str, urgency: str, has_phone: bool, wants_booking: bool) -> int:
    score = 20
    score += 25 if has_phone else 0
    score += 20 if condition else 0
    score += {"urgent": 20, "soon": 15}.get(urgency, 5)
    score += 15 if wants_booking else 0
    return min(score, 100)


def save_lead(ctx: ToolContext, name: str = "", phone: str = "", condition: str = "",
              urgency: str = "routine", notes: str = "") -> dict:
    phone = re.sub(r"[\s-]", "", phone or (ctx.sender_id if ctx.channel == "whatsapp" else ""))
    score = _qualify(condition, urgency, bool(_PHONE.match(phone)), False)
    lead_id = db.insert_row(db.leads, conversation_id=ctx.conversation_id, name=name[:120], phone=phone,
                            condition=condition[:200], urgency=urgency, score=score,
                            stage="qualified" if score >= 60 else "new", source=ctx.channel, notes=notes[:500])
    notify("lead.created", {"lead_id": lead_id, "name": name, "phone": phone, "condition": condition,
                            "urgency": urgency, "score": score, "channel": ctx.channel})
    return {"lead_id": lead_id, "score": score}


def book_appointment(ctx: ToolContext, name: str, phone: str, slot_start: str,
                     service: str = "assessment", reason: str = "") -> dict:
    phone = re.sub(r"[\s-]", "", phone or "")
    if not name.strip():
        return {"error": "Patient name is required."}
    if not _PHONE.match(phone):
        return {"error": "A valid Bangladesh mobile number (01XXXXXXXXX) is required."}
    if service not in {s["id"] for s in clinic()["services"]}:
        service = "assessment"
    free = {s.strftime("%Y-%m-%d %H:%M") for s in _slots()}
    if slot_start not in free:
        return {"error": "That slot is not available. Call check_availability and offer free slots."}
    score = _qualify(reason, "soon", True, True)
    try:
        booking_id = db.insert_row(db.bookings, conversation_id=ctx.conversation_id, name=name[:120],
                                   phone=phone, service=service, slot_start=slot_start)
    except IntegrityError:  # someone else took the slot a moment ago (unique index)
        return {"error": "That slot was just taken. Call check_availability and offer other slots."}
    lead_id = db.insert_row(db.leads, conversation_id=ctx.conversation_id, name=name[:120], phone=phone,
                            condition=reason[:200], urgency="soon", score=score, stage="booked",
                            source=ctx.channel)
    db.execute(db.update(db.bookings).where(db.bookings.c.id == booking_id).values(lead_id=lead_id))
    notify("lead.created", {"lead_id": lead_id, "name": name, "phone": phone, "condition": reason,
                            "urgency": "soon", "score": score, "channel": ctx.channel, "stage": "booked"})
    svc = next(s for s in clinic()["services"] if s["id"] == service)
    payload = {"booking_id": booking_id, "lead_id": lead_id, "name": name, "phone": phone,
               "service": svc["name"], "fee_bdt": svc["fee_bdt"], "slot_start": slot_start,
               "timezone": clinic()["timezone"], "channel": ctx.channel, "reason": reason}
    notify("booking.created", payload)
    return {"confirmed": True, **payload, "address": clinic()["address"]}


def escalate_to_human(ctx: ToolContext, reason: str) -> dict:
    esc_id = db.insert_row(db.escalations, conversation_id=ctx.conversation_id, kind="human_request",
                           reason=reason[:500])
    db.execute(db.update(db.conversations).where(db.conversations.c.id == ctx.conversation_id)
               .values(status="escalated"))
    notify("escalation.created", {"escalation_id": esc_id, "kind": "human_request", "reason": reason,
                                  "conversation_id": ctx.conversation_id})
    return {"escalated": True, "message": "A staff member will reply in this chat during clinic hours."}


# ------------------------------------------------------------ registry
TOOLS: dict[str, tuple[Callable, dict]] = {
    "search_knowledge": (search_knowledge, {
        "name": "search_knowledge",
        "description": "Search the clinic's clinician-APPROVED patient information. Use for ANY health, "
                       "symptom, treatment or exercise question. Answer only from the results.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "The patient's question in English keywords"}},
            "required": ["query"]}}),
    "get_clinic_info": (get_clinic_info, {
        "name": "get_clinic_info",
        "description": "Clinic name, address, opening hours, services with fees, payment and cancellation policy.",
        "parameters": {"type": "object", "properties": {}}}),
    "check_availability": (check_availability, {
        "name": "check_availability",
        "description": "List free appointment slots in the next 7 days.",
        "parameters": {"type": "object", "properties": {
            "day": {"type": "string", "description": "Optional: YYYY-MM-DD, weekday name, 'today' or 'tomorrow'"},
            "service": {"type": "string", "enum": ["assessment", "follow_up", "home_visit"]}}}}),
    "book_appointment": (book_appointment, {
        "name": "book_appointment",
        "description": "Book a slot returned by check_availability. Needs the patient's name and BD mobile number.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "phone": {"type": "string"},
            "slot_start": {"type": "string", "description": "Exactly as returned, e.g. 2026-10-18 10:00"},
            "service": {"type": "string", "enum": ["assessment", "follow_up", "home_visit"]},
            "reason": {"type": "string", "description": "Short reason for visit, e.g. 'low back pain 2 weeks'"}},
            "required": ["name", "phone", "slot_start"]}}),
    "save_lead": (save_lead, {
        "name": "save_lead",
        "description": "Save an interested patient who is not booking yet, so staff can follow up.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "phone": {"type": "string"}, "condition": {"type": "string"},
            "urgency": {"type": "string", "enum": ["routine", "soon", "urgent"]}, "notes": {"type": "string"}},
            "required": ["condition"]}}),
    "escalate_to_human": (escalate_to_human, {
        "name": "escalate_to_human",
        "description": "Hand the chat to a staff member: the patient asks for a person, is upset, or the "
                       "request is outside what you can do.",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}}),
}

TOOL_SPECS = [spec for _, spec in TOOLS.values()]


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> str:
    if name not in TOOLS:  # allow-list: the model cannot invent tools
        return json.dumps({"error": f"Unknown tool {name}"})
    fn, spec = TOOLS[name]
    allowed = set(spec["parameters"].get("properties", {}))
    clean = {k: v for k, v in (args or {}).items() if k in allowed and isinstance(v, (str, int, float))}
    ctx.used.append(name)
    try:
        return json.dumps(fn(ctx, **{k: str(v) for k, v in clean.items()}), ensure_ascii=False, default=str)
    except TypeError as exc:
        return json.dumps({"error": f"Bad arguments: {exc}"})
    except Exception as exc:  # a tool failure must never crash the patient conversation
        logging.getLogger("clinicdesk.tools").exception("tool %s failed", name)
        return json.dumps({"error": f"Tool failed: {type(exc).__name__}. Apologise and offer staff follow-up."})
