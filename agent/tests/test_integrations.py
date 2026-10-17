import hashlib
import hmac
import json

from conftest import ADMIN

from app.channels import whatsapp
from app.config import settings
from app.llm.base import ToolCall
from app.llm.providers import Anthropic, OpenAICompatible
from app.llm.router import cost_usd
from app.llm.base import Usage


def _wa_payload(text, msg_id="wamid.1", sender="8801712345678"):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"id": msg_id, "from": sender, "type": "text", "text": {"body": text}}]}}]}]}


def test_whatsapp_verification(client):
    ok = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.challenge": "42",
                                                   "hub.verify_token": settings.whatsapp_verify_token})
    assert ok.text == "42"
    bad = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.challenge": "42",
                                                    "hub.verify_token": "wrong"})
    assert bad.status_code == 403


def test_whatsapp_message_is_answered(client, monkeypatch):
    sent = []
    monkeypatch.setattr(whatsapp, "send_text", lambda to, body: sent.append((to, body)) or True)
    r = client.post("/webhooks/whatsapp", json=_wa_payload("What are your opening hours?", "wamid.A"))
    assert r.status_code == 200
    assert sent and sent[0][0] == "8801712345678" and "Hours" in sent[0][1]
    # duplicate delivery of the same message id is ignored
    client.post("/webhooks/whatsapp", json=_wa_payload("What are your opening hours?", "wamid.A"))
    assert len(sent) == 1


def test_whatsapp_signature(monkeypatch):
    monkeypatch.setattr(whatsapp, "settings", type("S", (), {"whatsapp_app_secret": "s3cret"})())
    body = json.dumps(_wa_payload("hi")).encode()
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert whatsapp.verify_signature(body, good)
    assert not whatsapp.verify_signature(body, "sha256=deadbeef")
    assert not whatsapp.verify_signature(body, None)


def test_outreach_cannot_be_sent_without_approval(client):
    oid = client.post("/api/admin/outreach", headers=ADMIN, json={
        "company": "Test Clinic", "subject": "Hi", "body": "Hello"}).json()["id"]
    assert client.post(f"/api/admin/outreach/{oid}/sent", headers=ADMIN).status_code == 409
    assert client.post(f"/api/admin/outreach/{oid}/approve", headers=ADMIN).json()["status"] == "approved"
    assert client.post(f"/api/admin/outreach/{oid}/sent", headers=ADMIN).json()["status"] == "sent"


def test_cost_calculation_uses_cached_price():
    plain = cost_usd("claude-haiku-4-5", Usage(1_000_000, 0, 0))
    cached = cost_usd("claude-haiku-4-5", Usage(0, 0, 1_000_000))
    assert plain == 1.0 and cached == 0.1


def test_openai_wire_format_round_trips_tool_calls():
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [ToolCall("c1", "get_clinic_info", {})]},
            {"role": "tool", "tool_call_id": "c1", "name": "get_clinic_info", "content": "{}"}]
    wire = OpenAICompatible._to_wire("sys", msgs)
    assert wire[0] == {"role": "system", "content": "sys"}
    assert wire[2]["tool_calls"][0]["function"]["name"] == "get_clinic_info"
    assert wire[3] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}


def test_anthropic_wire_format_groups_tool_results():
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [ToolCall("a", "x", {}), ToolCall("b", "y", {})]},
            {"role": "tool", "tool_call_id": "a", "content": "1"},
            {"role": "tool", "tool_call_id": "b", "content": "2"}]
    wire = Anthropic._to_wire(msgs)
    assert [b["type"] for b in wire[1]["content"]] == ["tool_use", "tool_use"]
    assert len(wire) == 3 and [b["tool_use_id"] for b in wire[2]["content"]] == ["a", "b"]


def test_gtm_pipeline_scores_and_queues_for_approval(client):
    from gtm.pipeline import run
    places = run("sample")
    by_name = {p.name: p for p in places}
    assert by_name["Metro General Hospital (fictional)"].score == 0          # disqualified
    assert by_name["Chattogram Physio Care (fictional)"].score == 0          # outside Dhaka
    assert by_name["Lalmatia Spine & Physio (fictional)"].score >= 90
    drafts = client.get("/api/admin/outreach?status=pending_approval", headers=ADMIN).json()
    assert drafts and all(d["status"] == "pending_approval" for d in drafts)


def test_signature_required_once_whatsapp_is_live(monkeypatch):
    monkeypatch.setattr(whatsapp, "settings", type("S", (), {"whatsapp_app_secret": "", "whatsapp_token": "live"})())
    assert not whatsapp.verify_signature(b"{}", None)


def test_sent_outreach_cannot_be_reapproved(client):
    oid = client.post("/api/admin/outreach", headers=ADMIN, json={"company": "X", "subject": "s", "body": "b"}).json()["id"]
    client.post(f"/api/admin/outreach/{oid}/approve", headers=ADMIN)
    client.post(f"/api/admin/outreach/{oid}/sent", headers=ADMIN)
    assert client.post(f"/api/admin/outreach/{oid}/approve", headers=ADMIN).status_code == 409


def test_tool_errors_do_not_crash_the_chat():
    from app.agent.tools import ToolContext, run_tool
    out = json.loads(run_tool(ToolContext(1, "web", "x", "en"), "book_appointment",
                              {"name": 5, "phone": None, "slot_start": "nope"}))
    assert "error" in out


def test_daily_budget_falls_back_to_offline_model(monkeypatch):
    from app import db
    from app.llm import router as router_mod
    from app.llm.base import LLMResult

    class Fake:
        name = "groq"

        def complete(self, *a, **k):
            raise AssertionError("real provider must not be called over budget")

    r = router_mod.Router()
    r.provider = Fake()
    db.insert_row(db.llm_calls, provider="groq", model="x", purpose="agent", cost_usd=0)
    monkeypatch.setattr(router_mod, "settings", type("S", (), {**{k: getattr(router_mod.settings, k) for k in
                        ("pricing_file", "small_model", "main_model")}, "daily_llm_call_budget": 1})())
    res = r.call(purpose="triage", tier="small", system="s", json_mode=True,
                 messages=[{"role": "user", "content": "hello"}])
    assert isinstance(res, LLMResult) and res.model == "mock-small"
