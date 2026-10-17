# n8n setup

1. Open n8n (http://localhost:5678, or your n8n domain) and create the owner account.
2. **Import** each file in `/n8n`: Workflows → Import from file. In Docker the files are also mounted at `/workflows`.
3. **Credentials:** connect *Google Sheets*, *Google Calendar* and *Gmail* (OAuth2) and select them on the matching nodes.
4. **Google Sheet:** create a spreadsheet with two tabs.
   - `Leads`: Lead ID, Name, Phone, Condition, Urgency, Score, Channel, Created
   - `Bookings`: Booking ID, Name, Phone, Service, Slot, Fee (BDT), Reason

   Put its ID in `CLINICDESK_SHEET_ID`.
5. Set `CLINICDESK_CALENDAR_ID` (your calendar email) and `CLINICDESK_ALERT_EMAIL` (who gets escalations and the daily report).
6. Optional but recommended: set the same `N8N_WEBHOOK_SECRET` for the agent and n8n. Workflow 01 then drops events without the matching `X-Webhook-Secret` header.
7. **Activate** the workflows. The agent's `N8N_WEBHOOK_URL` defaults to `http://n8n:5678/webhook/clinicdesk-events` inside Docker.

The workflows read settings from environment variables (`$env.CLINICDESK_*`). `docker-compose.yml` passes them in and sets `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`. If you run n8n outside Docker, set the same variables or replace the expressions with fixed values.

| Workflow | Needs |
| --- | --- |
| 01 Agent events | Sheets, Calendar, Gmail credentials; sheet and calendar IDs; alert email |
| 02 Reminders | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, approved `appointment_reminder` template |
| 03 GTM outbound | Gmail credential; drafts approved in the dashboard |
| 04 Daily report | Gmail credential; alert email |
