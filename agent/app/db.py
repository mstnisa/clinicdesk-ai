"""Database layer (SQLAlchemy Core).

Runs on SQLite for local tests and on Postgres / Supabase in Docker or
production: only DATABASE_URL changes. The same schema is in db/schema.sql
for Supabase, with row-level security notes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (Column, DateTime, Float, ForeignKey, Index, Integer, MetaData, String, Table, Text,
                        create_engine, func, insert, select, text, update)

from .config import settings

metadata = MetaData()


def now() -> datetime:
    return datetime.now(timezone.utc)


conversations = Table(
    "conversations", metadata,
    Column("id", Integer, primary_key=True),
    Column("channel", String(20), nullable=False),          # whatsapp | web
    Column("sender_id", String(64), nullable=False, index=True),
    Column("sender_masked", String(32)),
    Column("contact_phone", String(32)),                    # given by the patient, used for bookings
    Column("language", String(4), default="en"),
    Column("status", String(16), default="open"),           # open | escalated | closed
    Column("created_at", DateTime(timezone=True), default=now),
    Column("updated_at", DateTime(timezone=True), default=now),
)

messages = Table(
    "messages", metadata,
    Column("id", Integer, primary_key=True),
    Column("conversation_id", Integer, ForeignKey("conversations.id"), index=True),
    Column("role", String(12)),                             # user | assistant | tool
    Column("content", Text),                                # PII-redacted
    Column("route", String(24)),                            # red_flag | security | cache | small | agent
    Column("created_at", DateTime(timezone=True), default=now),
)

leads = Table(
    "leads", metadata,
    Column("id", Integer, primary_key=True),
    Column("conversation_id", Integer, ForeignKey("conversations.id"), index=True),
    Column("name", String(120)),
    Column("phone", String(32)),
    Column("condition", String(200)),
    Column("urgency", String(16)),                          # routine | soon | urgent
    Column("score", Integer),
    Column("stage", String(16), default="new"),            # new | qualified | booked | lost
    Column("source", String(20)),
    Column("notes", Text),
    Column("created_at", DateTime(timezone=True), default=now),
)

bookings = Table(
    "bookings", metadata,
    Column("id", Integer, primary_key=True),
    Column("lead_id", Integer, ForeignKey("leads.id"), nullable=True),
    Column("conversation_id", Integer, ForeignKey("conversations.id"), index=True),
    Column("name", String(120)),
    Column("phone", String(32)),
    Column("service", String(40)),
    Column("slot_start", String(32)),                       # ISO local time
    Column("status", String(16), default="confirmed"),
    Column("created_at", DateTime(timezone=True), default=now),
)
# One confirmed booking per slot, enforced by the database (no double-booking under concurrency).
Index("uq_bookings_confirmed_slot", bookings.c.slot_start, unique=True,
      postgresql_where=text("status = 'confirmed'"), sqlite_where=text("status = 'confirmed'"))

escalations = Table(
    "escalations", metadata,
    Column("id", Integer, primary_key=True),
    Column("conversation_id", Integer, ForeignKey("conversations.id"), index=True),
    Column("kind", String(20)),                             # red_flag | human_request | security | out_of_scope
    Column("reason", Text),
    Column("status", String(12), default="open"),
    Column("created_at", DateTime(timezone=True), default=now),
)

llm_calls = Table(
    "llm_calls", metadata,
    Column("id", Integer, primary_key=True),
    Column("conversation_id", Integer, nullable=True, index=True),
    Column("purpose", String(16)),                          # triage | agent | gtm
    Column("provider", String(16)),
    Column("model", String(64)),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("cached_tokens", Integer),
    Column("cost_usd", Float),
    Column("latency_ms", Integer),
    Column("created_at", DateTime(timezone=True), default=now),
)

security_events = Table(
    "security_events", metadata,
    Column("id", Integer, primary_key=True),
    Column("conversation_id", Integer, nullable=True),
    Column("kind", String(24)),                             # prompt_injection | rate_limit | bad_signature
    Column("detail", Text),
    Column("created_at", DateTime(timezone=True), default=now),
)

answer_cache = Table(
    "answer_cache", metadata,
    Column("key", String(64), primary_key=True),
    Column("answer", Text),
    Column("knowledge_version", String(16)),
    Column("hits", Integer, default=0),
    Column("created_at", DateTime(timezone=True), default=now),
)

outreach = Table(
    "outreach", metadata,
    Column("id", Integer, primary_key=True),
    Column("company", String(160)),
    Column("website", String(200)),
    Column("city", String(60)),
    Column("email", String(160)),
    Column("icp_score", Integer),
    Column("icp_reasons", Text),
    Column("subject", String(200)),
    Column("body", Text),
    Column("status", String(20), default="pending_approval"),  # pending_approval | approved | sent | followed_up | rejected
    Column("created_at", DateTime(timezone=True), default=now),
    Column("updated_at", DateTime(timezone=True), default=now),
)

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, future=True, pool_pre_ping=True, connect_args=_connect_args)


def init_db() -> None:
    metadata.create_all(engine)


def execute(stmt) -> Any:
    with engine.begin() as conn:
        return conn.execute(stmt)


def insert_row(table: Table, **values) -> int:
    with engine.begin() as conn:
        res = conn.execute(insert(table).values(**values))
        pk = res.inserted_primary_key[0]
        return int(pk) if isinstance(pk, int) or str(pk).isdigit() else pk


def rows(stmt) -> list[dict]:
    with engine.begin() as conn:
        return [dict(r._mapping) for r in conn.execute(stmt)]


__all__ = [
    "conversations", "messages", "leads", "bookings", "escalations", "llm_calls", "security_events",
    "answer_cache", "outreach", "init_db", "insert_row", "rows", "execute", "select", "update", "func", "now",
]
