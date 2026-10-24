# Deploy the public demo on Render (free) with a custom domain

The live demo runs at **https://clinicdesk.awalunnisa.com** on a free Render web
service, defined in [`render.yaml`](../render.yaml). It is the same setup as the
Hugging Face Space: fake demo data in SQLite, reloaded on every start.

## 1. Create the service

1. Sign in at [render.com](https://render.com) with GitHub.
2. **New → Blueprint**, pick the `mstnisa/clinicdesk-ai` repository. Render reads `render.yaml`.
3. When asked, paste `GROQ_API_KEY` (the only secret). Click **Apply**.
4. Wait for the first deploy, then open `https://clinicdesk-ai.onrender.com/health`
   (Render shows the exact `onrender.com` address). It should return `{"ok": true, ...}`.

## 2. Point the subdomain at Render

1. In Render: the service → **Settings → Custom Domains**. `clinicdesk.awalunnisa.com` is
   already listed from `render.yaml`; Render shows the CNAME target (the `onrender.com` host).
2. In Hostinger: **Domains → awalunnisa.com → DNS / Nameservers → Manage DNS records**, add:

   | Type | Name | Target | TTL |
   | --- | --- | --- | --- |
   | CNAME | `clinicdesk` | `clinicdesk-ai.onrender.com` (the target Render shows) | default |

3. Back in Render, click **Verify**. The HTTPS certificate is issued automatically,
   usually within minutes (DNS can take up to a few hours).
4. Check `https://clinicdesk.awalunnisa.com/health`, `/widget` and `/dashboard`.

## Free-tier notes

| Limit | Effect | What to do |
| --- | --- | --- |
| Sleeps after ~15 min idle | First request takes ~30–60 s | Before a demo or interview, open `/health` once. A free uptime monitor (e.g. UptimeRobot) pinging `/health` every 10 min keeps it awake. |
| Disk is not persistent | Demo data resets on every restart or deploy | Intended: the demo reseeds itself on start. |
| `DAILY_LLM_CALL_BUDGET=400` | Past the cap the offline model answers | Keeps the Groq quota (and the bill) safe. |
