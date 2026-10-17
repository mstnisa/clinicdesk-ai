# Security and safety

| Threat | Control | Where |
| --- | --- | --- |
| Model gives unapproved medical advice | Retrieval only from `status: Approved` pages; prompt forbids general knowledge, diagnosis, medicines and doses; "no approved info" fallback | `knowledge/retriever.py`, `agent/prompts.py`, `tools.search_knowledge` |
| Urgent symptoms handled by AI | Red-flag rules run before any model; escalation + staff alert + emergency advice | `agent/guardrails.py`, orchestrator step 2 |
| Prompt injection / jailbreak | Pattern filter (EN + BN) before the model; role-lock instruction; security event logged | `guardrails.looks_like_injection` |
| Model invents tools or arguments | Tool allow-list; arguments filtered to the declared schema and to scalar types | `tools.run_tool` |
| Bad booking data | Phone format, service and slot availability validated in code; slot must be free | `tools.book_appointment` |
| Unapproved outbound messages | `sent` only allowed from `approved` (HTTP 409 otherwise); n8n fetches `approved` only | `main.outreach_action` |
| Forged WhatsApp webhooks | `X-Hub-Signature-256` HMAC check with the app secret (mandatory once a WhatsApp token is set); verify-token handshake | `channels/whatsapp.verify_signature` |
| Duplicate webhook delivery | Recent message-id de-duplication | `main.whatsapp_receive` |
| PII in logs and transcripts | Phone, email and NID numbers redacted before storage; senders masked in the dashboard | `guardrails.redact`, `mask_sender` |
| Data exposure via Supabase public key | RLS enabled on every table with no public policies | `db/schema.sql` |
| Spam / cost attacks | Per-sender rate limit; `max_tokens` per purpose; agent step cap | `RATE_LIMIT_PER_MINUTE`, `MAX_AGENT_STEPS` |
| Admin API exposure | Bearer token; set a long random `ADMIN_TOKEN`; HTTPS via Caddy | `.env`, `deploy/Caddyfile` |
| Container breakout impact | Runs as a non-root user | `agent/Dockerfile` |
| Services exposed to the internet | Agent and n8n ports bound to `127.0.0.1`; Caddy is the only public entry (Docker-published ports bypass ufw, so binding matters) | `docker-compose.yml` |
| Weak admin token in production | Agent refuses to start with a real provider unless `ADMIN_TOKEN` is 16+ characters and not the demo value; constant-time comparison | `main.lifespan`, `main.admin` |
| Forged events into n8n | Optional shared secret (`N8N_WEBHOOK_SECRET`) checked by workflow 01 | `tools.notify`, `n8n/01` |
| Rate-limit bypass by rotating `sender_id` | Web chat is limited per sender **and** per client IP (real IP via proxy headers) | `orchestrator.handle_message` |
| Double booking under concurrency | Unique index on confirmed slots; the tool returns "slot just taken" | `db.bookings`, `tools.book_appointment` |

## Before going live

- [ ] Change `ADMIN_TOKEN`, `POSTGRES_PASSWORD` and `N8N_ENCRYPTION_KEY` (`openssl rand -hex 24`)
- [ ] Set `WHATSAPP_APP_SECRET` (required once `WHATSAPP_TOKEN` is set)
- [ ] Set the same `N8N_WEBHOOK_SECRET` for the agent and n8n
- [ ] Put the stack behind HTTPS (`docker compose --profile https up -d`); keep `BIND_ADDRESS=127.0.0.1`
- [ ] Have a clinician review every page in `knowledge/pages/` and the red-flag list
- [ ] Decide data retention: e.g. delete transcripts older than 90 days
