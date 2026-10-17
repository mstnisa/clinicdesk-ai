"""WhatsApp Business Cloud API (Meta Graph API) adapter."""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Iterator

import httpx

from ..config import settings

log = logging.getLogger("clinicdesk.whatsapp")


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """Meta signs every webhook with the app secret (X-Hub-Signature-256).
    Local demo (no WhatsApp token) skips the check; once a token is configured the
    app secret is mandatory, so unsigned requests can never trigger real messages."""
    if not settings.whatsapp_app_secret:
        return not settings.whatsapp_token
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(settings.whatsapp_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def incoming_text_messages(payload: dict[str, Any]) -> Iterator[tuple[str, str, str]]:
    """Yield (message_id, sender_phone, text) for each text message in a webhook payload."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []) or []:
                if msg.get("type") == "text":
                    yield msg["id"], msg["from"], msg["text"]["body"]
                elif msg.get("type") == "interactive":
                    reply = msg["interactive"].get("button_reply") or msg["interactive"].get("list_reply") or {}
                    if reply.get("title"):
                        yield msg["id"], msg["from"], reply["title"]
                elif msg.get("type") == "button" and msg.get("button", {}).get("text"):
                    yield msg["id"], msg["from"], msg["button"]["text"]  # template quick-reply


def send_text(to: str, body: str) -> bool:
    if not (settings.whatsapp_token and settings.whatsapp_phone_id):
        log.info("[whatsapp:demo] to=%s… body=%s", to[:5], body[:80])
        return False
    url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_id}/messages"
    r = httpx.post(url, timeout=15, headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
                   json={"messaging_product": "whatsapp", "to": to, "type": "text",
                         "text": {"preview_url": False, "body": body[:4096]}})
    if r.status_code >= 400:
        log.warning("WhatsApp send failed %s: %s", r.status_code, r.text[:200])
        return False
    return True
