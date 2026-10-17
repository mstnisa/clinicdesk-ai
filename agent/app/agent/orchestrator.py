"""The conversation pipeline.

    message ─► rate limit ─► red-flag rules ─► injection rules ─► answer cache
            ─► small-model triage ─► (greeting? reply)  ─► main-model agent loop with tools

Cheap, deterministic layers run first; the expensive model runs last and only
when it is actually needed. Every step is recorded for the dashboard.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from .. import db
from ..config import settings
from ..llm.base import LLMError
from ..llm.router import Router
from . import guardrails as g
from .prompts import (AGENT_SYSTEM, EMPTY_REPLY, FALLBACK_REPLY, HANDOVER_REPLY, NOW_NOTE, RATE_LIMIT_REPLY,
                      RED_FLAG_REPLY, SECURITY_REPLY, TRIAGE_SYSTEM)
from .tools import KB, TOOL_SPECS, ToolContext, clinic, local_now, notify, run_tool

_limiter = g.RateLimiter(settings.rate_limit_per_minute)
_router: Router | None = None


def router() -> Router:
    global _router
    if _router is None:
        _router = Router()
    return _router


@dataclass
class Reply:
    text: str
    route: str
    conversation_id: int
    language: str
    tools_used: list[str] = field(default_factory=list)
    cost_bdt: float = 0.0


# ----------------------------------------------------------------- helpers
def _conversation(channel: str, sender_id: str, language: str) -> int:
    found = db.rows(db.select(db.conversations.c.id).where(
        (db.conversations.c.channel == channel) & (db.conversations.c.sender_id == sender_id))
        .order_by(db.conversations.c.id.desc()).limit(1))
    if found:
        cid = found[0]["id"]
        db.execute(db.update(db.conversations).where(db.conversations.c.id == cid)
                   .values(updated_at=db.now(), language=language))
        return cid
    return db.insert_row(db.conversations, channel=channel, sender_id=sender_id,
                         sender_masked=g.mask_sender(sender_id), language=language)


def _log(cid: int, role: str, content: str, route: str = "") -> None:
    db.insert_row(db.messages, conversation_id=cid, role=role, content=g.redact(content), route=route)


def _history(cid: int, limit: int = 10) -> list[dict]:
    # The model sees the redacted transcript; the patient's own phone number
    # (needed for booking) is passed back in only via the current message.
    recent = db.rows(db.select(db.messages.c.role, db.messages.c.content)
                     .where((db.messages.c.conversation_id == cid) & db.messages.c.role.in_(["user", "assistant"]))
                     .order_by(db.messages.c.id.desc()).limit(limit))
    msgs = [{"role": r["role"], "content": r["content"]} for r in reversed(recent)]
    while msgs and msgs[0]["role"] != "user":  # some providers require the first turn to be the user's
        msgs.pop(0)
    return msgs


_PHONE_IN_TEXT = re.compile(r"(?:\+?88)?01[3-9]\d{8}")


def _remember_phone(cid: int, text: str, channel: str, sender_id: str) -> str:
    """Keep the patient's number on the conversation (not in the redacted transcript)."""
    m = _PHONE_IN_TEXT.search(re.sub(r"[\s-]", "", text))
    if m:
        db.execute(db.update(db.conversations).where(db.conversations.c.id == cid).values(contact_phone=m.group(0)))
        return m.group(0)
    row = db.rows(db.select(db.conversations.c.contact_phone).where(db.conversations.c.id == cid))
    if row and row[0]["contact_phone"]:
        return row[0]["contact_phone"]
    return sender_id if channel == "whatsapp" else ""


def conversation_cost_bdt(cid: int) -> float:
    total = db.rows(db.select(db.func.coalesce(db.func.sum(db.llm_calls.c.cost_usd), 0.0).label("c"))
                    .where(db.llm_calls.c.conversation_id == cid))[0]["c"]
    return round(float(total) * settings.usd_to_bdt, 4)


def _cache_key(language: str, text: str) -> str:
    norm = re.sub(r"\s+", " ", re.sub(r"[^\w\sঀ-৿]", "", text.lower())).strip()
    return hashlib.sha256(f"{KB.version}|{language}|{norm}".encode()).hexdigest()


def _finish(cid: int, text: str, route: str, language: str, tools: list[str] | None = None) -> Reply:
    _log(cid, "assistant", text, route)
    return Reply(text, route, cid, language, tools or [], conversation_cost_bdt(cid))


# ----------------------------------------------------------------- main
def _escalate(cid: int, kind: str, reason: str, channel: str, sender_id: str, **extra) -> int:
    esc = db.insert_row(db.escalations, conversation_id=cid, kind=kind, reason=reason[:500])
    db.execute(db.update(db.conversations).where(db.conversations.c.id == cid).values(status="escalated"))
    notify("escalation.created", {"escalation_id": esc, "kind": kind, "reason": reason, "conversation_id": cid,
                                  "channel": channel, "sender": sender_id, **extra})
    return esc


def _status(cid: int) -> str:
    return db.rows(db.select(db.conversations.c.status).where(db.conversations.c.id == cid))[0]["status"]


def handle_message(channel: str, sender_id: str, text: str, client_key: str = "") -> Reply:
    text = (text or "").strip()[:2000]
    language = g.detect_language(text)
    cid = _conversation(channel, sender_id, language)
    if not text:
        return _finish(cid, EMPTY_REPLY[language], "empty", language)
    _log(cid, "user", text)

    # 1. rate limit, per sender and (for the web widget) per client IP
    keys = [f"{channel}:{sender_id}"] + ([f"ip:{client_key}"] if client_key else [])
    if not all(_limiter.allow(k) for k in keys):
        db.insert_row(db.security_events, conversation_id=cid, kind="rate_limit", detail=g.mask_sender(sender_id))
        return _finish(cid, RATE_LIMIT_REPLY[language], "rate_limit", language)

    # 2. red flags: rules decide, a human takes over
    flags = g.red_flags(text)
    if flags:
        _escalate(cid, "red_flag", ", ".join(flags) + " | " + g.redact(text)[:300], channel, sender_id, flags=flags)
        emergency = clinic()["emergency_line_bn" if language == "bn" else "emergency_line"]
        return _finish(cid, RED_FLAG_REPLY[language].format(emergency=emergency), "red_flag", language)

    # 3. prompt injection
    if g.looks_like_injection(text):
        db.insert_row(db.security_events, conversation_id=cid, kind="prompt_injection", detail=g.redact(text)[:500])
        return _finish(cid, SECURITY_REPLY[language], "security", language)

    # 4. real handover: while staff own the chat, the bot does not answer
    if _status(cid) == "escalated":
        return _finish(cid, HANDOVER_REPLY[language], "handover", language)

    # 5. answer cache: only for a conversation's opening question, so a reply that
    #    depends on earlier context (or names the patient) is never reused for someone else
    history = _history(cid)
    first_question = len(history) == 1
    key = _cache_key(language, text)
    if first_question:
        cached = db.rows(db.select(db.answer_cache).where(db.answer_cache.c.key == key))
        if cached:
            db.execute(db.update(db.answer_cache).where(db.answer_cache.c.key == key)
                       .values(hits=db.answer_cache.c.hits + 1))
            return _finish(cid, cached[0]["answer"], "cache", language)

    r = router()
    ctx = ToolContext(cid, channel, sender_id, language)
    try:
        # 6. triage with the small model
        tri = r.call(purpose="triage", tier="small", system=TRIAGE_SYSTEM, json_mode=True, max_tokens=150,
                     messages=[{"role": "user", "content": text}], conversation_id=cid)
        found = re.search(r"\{.*\}", tri.text or "", re.S)  # tolerate ```json fences or extra words
        try:
            triage = json.loads(found.group(0)) if found else {"intent": "other"}
        except json.JSONDecodeError:
            triage = {"intent": "other"}
        if triage.get("intent") == "greeting" and triage.get("reply"):
            return _finish(cid, triage["reply"], "small", language)

        # 7. agent loop with the main model
        system = AGENT_SYSTEM.format(clinic=clinic()["name"])
        now = local_now()
        msgs = history
        # Give the model the un-redacted current message (it may contain the phone number to book with).
        msgs[-1] = {"role": "user", "content": f"{NOW_NOTE.format(now=now.strftime('%A %d %B %Y, %H:%M'))}\n\n{text}"}
        phone_on_file = _remember_phone(cid, text, channel, sender_id)
        if phone_on_file:
            msgs[-1]["content"] += f"\n[Context] Patient's mobile number on file: {phone_on_file}"
        answer = ""
        for _ in range(settings.max_agent_steps):
            res = r.call(purpose="agent", tier="main", system=system, messages=msgs, tools=TOOL_SPECS,
                         conversation_id=cid, max_tokens=1200)
            if not res.tool_calls:
                answer = res.text.strip()
                break
            msgs.append({"role": "assistant", "content": res.text, "tool_calls": res.tool_calls})
            for call in res.tool_calls:
                out = run_tool(ctx, call.name, call.args)
                msgs.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": out})
        if not answer:
            # step limit reached or empty output: hand over instead of guessing
            _escalate(cid, "out_of_scope", "Agent could not finish the request", channel, sender_id)
            return _finish(cid, FALLBACK_REPLY[language], "fallback", language, ctx.used)
    except LLMError:
        # Provider outage: never leave the patient without a reply.
        _escalate(cid, "out_of_scope", "LLM provider error", channel, sender_id)
        return _finish(cid, FALLBACK_REPLY[language], "fallback", language, ctx.used)

    # cache pure knowledge answers to opening questions (no personal data, no bookings)
    if first_question and ctx.used and set(ctx.used) == {"search_knowledge"}:
        if not db.rows(db.select(db.answer_cache.c.key).where(db.answer_cache.c.key == key)):
            db.insert_row(db.answer_cache, key=key, answer=answer, knowledge_version=KB.version, hits=0)
    return _finish(cid, answer, "agent", language, ctx.used)
