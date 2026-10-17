# Deploy on a VPS (Docker + HTTPS)

Recommended: Ubuntu 22.04/24.04, 2 vCPU, 2–4 GB RAM.

1. **DNS:** point two subdomains at the server, e.g. `desk.example.com` (agent) and `n8n.example.com`.
2. **Install Docker:**
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER && newgrp docker
   ```
3. **Get the code and configure:**
   ```bash
   git clone https://github.com/mstnisa/clinicdesk-ai.git && cd clinicdesk-ai
   cp .env.example .env && nano .env
   ```
   Set `LLM_PROVIDER` and its key, strong `ADMIN_TOKEN` / `POSTGRES_PASSWORD` / `N8N_ENCRYPTION_KEY`, and:
   ```
   AGENT_DOMAIN=desk.example.com
   N8N_DOMAIN=n8n.example.com
   N8N_HOST=n8n.example.com
   N8N_PUBLIC_URL=https://n8n.example.com/
   ```
4. **Start with HTTPS:** Caddy gets the certificates automatically.
   ```bash
   docker compose --profile https up -d --build
   ```
5. **Firewall:** allow only 22, 80 and 443.
   ```bash
   sudo ufw allow 22,80,443/tcp && sudo ufw enable
   ```
   Keep `BIND_ADDRESS=127.0.0.1` in `.env`: Docker-published ports bypass ufw, so the agent and n8n ports must not be bound to `0.0.0.0`.
6. **Check:** `https://desk.example.com/health` returns `{"ok": true, ...}`.

## Operations

| Task | Command |
| --- | --- |
| Logs | `docker compose logs -f agent` |
| Update | `git pull && docker compose up -d --build` |
| Backup database | `docker compose exec db pg_dump -U clinicdesk clinicdesk > backup-$(date +%F).sql` |
| Reload knowledge after a clinician edits a page | `curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" https://desk.example.com/api/admin/knowledge/reload` |
| Run tests in the container | `docker compose exec agent pytest` |
