# Architecture

## Request pipeline

Every incoming message, from WhatsApp or the website widget, goes through the same pipeline in `agent/app/agent/orchestrator.py`. Cheap and deterministic steps come first. The expensive model comes last, and only when it is needed.

| # | Step | Model? | Purpose |
| --- | --- | --- | --- |
| 1 | Rate limit (per sender, per minute) | No | Abuse and cost protection |
| 2 | Red-flag rules (`guardrails.py`) | No | Urgent symptoms always reach a human; fixed safe reply with emergency advice |
| 3 | Injection filter | No | Role-change and prompt-extraction attempts are blocked and logged |
| 4 | Answer cache | No | Repeat knowledge questions cost nothing; key = question + language + knowledge version |
| 5 | Triage | Small | Intent; greetings answered directly |
| 6 | Agent loop | Main | Tool calls (max `MAX_AGENT_STEPS`), then the final reply |

Each step writes what it did. `messages.route` records which layer answered, and `llm_calls` records tokens and cost. The dashboard's "how replies were produced" chart and cost-per-conversation figure come from these two tables.

## Why rules for safety

A prompt can be argued with. A regular expression cannot. Red flags and injection attempts are handled before the model sees the message, so:
- they work the same with every provider, including a cheap one;
- they are unit-tested in both languages (`tests/test_guardrails.py`);
- they cost nothing and add no latency.

The model is still told in its system prompt to stay in role. That is a second line of defence, not the first.

## Knowledge and the approval gate

`agent/app/knowledge/pages/*.md` are written and approved by clinicians. Front matter carries `status`, `version`, `approved_by` and `approved_on`. Only `status: Approved` pages are indexed, so a draft (see `neck-pain-draft.md`) is invisible to the agent until a clinician approves it. `POST /api/admin/knowledge/reload` re-indexes without a restart, and the knowledge version changes, which invalidates cached answers.

Retrieval is BM25 over `## sections`. The index is dependency-free and handles Bangla through light stemming and a small Bangla→English bridge vocabulary. A section only matches when a *topic* word matches, so "neck pain exercises" never returns the heel page just because both mention exercise.

## Data model

Postgres in Docker and production, SQLite for tests. See `db/schema.sql`.

`conversations` → `messages` (redacted) · `leads` · `bookings` · `escalations` · `llm_calls` · `security_events` · `answer_cache` · `outreach`

## Integrations

- **Agent → n8n:** fire-and-forget `POST` to `N8N_WEBHOOK_URL` with `lead.created`, `booking.created` and `escalation.created`. If n8n is down, the patient chat still works.
- **n8n → agent:** the admin API, with the bearer token (`/api/admin/bookings/upcoming`, `/api/admin/outreach…`, `/api/admin/stats`).
- **WhatsApp:** Meta Cloud API webhook in, Graph API out (`channels/whatsapp.py`).
- **GTM:** Apify actor `compass~crawler-google-places` (sync run) → `outreach` table.
