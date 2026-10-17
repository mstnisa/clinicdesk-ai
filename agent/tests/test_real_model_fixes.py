"""Regression tests for problems found when the agent ran against real Groq models.

The mock model behaves perfectly, so these check what the tools hand to the model:
real models were fine once the tool results stopped leaving room for guesses.
"""
from app import db
from app.agent import tools
from app.agent.orchestrator import _finish
from app.agent.prompts import AGENT_SYSTEM, NOW_NOTE


def _ctx(language="en"):
    cid = db.insert_row(db.conversations, channel="web", sender_id="web-fixes", sender_masked="web-***",
                        language=language)
    return tools.ToolContext(cid, "web", "web-fixes", language)


def test_search_returns_only_the_patients_language():
    # Given both languages, a real model answered an English question in Bangla.
    en = tools.search_knowledge(_ctx("en"), "do I need an MRI for low back pain")["results"][0]
    bn = tools.search_knowledge(_ctx("bn"), "do I need an MRI for low back pain")["results"][0]
    assert "text_bn" not in en and en["text"].startswith("Usually not")
    assert any("ঀ" <= ch <= "৿" for ch in bn["text"])
    assert "{language}" in NOW_NOTE


def test_clinic_info_names_closed_days():
    # With Friday missing from the hours, a real model copied Thursday's hours onto Friday.
    hours = tools.get_clinic_info(_ctx())["hours"]
    assert len(hours) == 7 and hours["Friday"] == "closed"


def test_booking_result_spells_out_the_weekday():
    # Given only "2026-10-18 10:45", a real model called a Sunday "Tuesday".
    slot = tools.check_availability(_ctx())["slots"][-1]["slot_start"]
    result = tools.book_appointment(_ctx(), name="Weekday Test", phone="01799999999", slot_start=slot)
    assert result["confirmed"] and result["when"].split()[0] in {
        "Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}


def test_prompt_asks_for_numbered_slots_and_plain_text():
    # Patients answer "2"; that only works if the slots were numbered.
    assert "numbered list" in AGENT_SYSTEM and "no markdown" in AGENT_SYSTEM


def test_replies_use_plain_hyphens():
    cid = _ctx().conversation_id
    assert _finish(cid, "(Source: KB‑LBP v1.0)", "agent", "en").text == "(Source: KB-LBP v1.0)"


def test_mock_reply_shows_real_opening_hours(chat):
    reply = chat("web-hours", "What are your opening hours?")["reply"]
    assert "Sat 10:00-20:00" in reply and "Fri closed" in reply
