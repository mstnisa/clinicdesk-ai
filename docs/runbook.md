# Runbook (for the clinic team)

**Every morning (5 min):** open the dashboard → **Escalations**. Call or message each open red-flag patient first, then press *Mark resolved*.

**When a clinician updates advice:** edit the page in `agent/app/knowledge/pages/`, raise `version`, set `status: Approved` and fill `approved_by` / `approved_on`. Then reload knowledge (see deploy-vps.md). In Docker the `agent/app/knowledge` folder is mounted into the container, so no rebuild is needed. Cached answers refresh automatically.

**Adding a new topic:** copy an existing page, write the patient-friendly sections (`## heading`, English text, then a `> বাংলা:` line), and keep `status: In review` until a clinician approves it.

**Outreach (sales):** run the GTM pipeline weekly, then review drafts in **Outreach**: approve the good ones and reject the rest. n8n sends approved drafts on weekdays.

**Handover:** while a conversation has an open escalation, the bot only sends a short "a staff member will reply" message. When you mark the escalation resolved, the bot answers that patient again.

**If the AI provider is down:** patients get a polite fallback and the chat is escalated, so staff see it in **Escalations**. Switch provider by changing `LLM_PROVIDER` in `.env` and running `docker compose up -d`.

**Costs:** the daily email shows AI cost and cost per conversation. If it rises, check **Conversations** for spam (rate limit is `RATE_LIMIT_PER_MINUTE`).
