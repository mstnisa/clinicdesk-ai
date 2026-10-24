"""Regression tests for problems found when the agent ran against real Groq models.

The mock model behaves perfectly, so these check what the tools hand to the model:
real models were fine once the tool results stopped leaving room for guesses.
"""
from datetime import datetime

from app import db
from app.agent import tools
from app.agent.orchestrator import _TOOL_NAMES, _finish
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


def test_triage_runs_only_on_the_opening_message(chat):
    # Seen alone, the mid-booking reply "2, আমার নাম ..." looked like a greeting to the small model.
    cid = chat("web-triage", "I want to book an appointment")["conversation_id"]
    chat("web-triage", "2, my name is Triage Test, 01812345679")
    calls = db.rows(db.select(db.llm_calls.c.purpose).where(db.llm_calls.c.conversation_id == cid))
    assert [c["purpose"] for c in calls].count("triage") == 1


def test_bangla_dates_are_written_by_the_code():
    # Translating "Sunday 25 Oct" itself, a real model wrote "সোমবার" (Monday).
    assert tools.say_when(datetime(2026, 10, 25, 12, 15), "bn") == "রবিবার ২৫ অক্টোবর, ১২:১৫"
    assert tools.say_when(datetime(2026, 10, 25, 12, 15), "en", year=True) == "Sunday 25 October 2026, 12:15"
    slot = tools.check_availability(_ctx("bn"))["slots"][0]
    assert slot["label"].split()[0] in tools._BN_DAYS.values()
    booked = tools.book_appointment(_ctx("bn"), name="তারিখ টেস্ট", phone="01799999998", slot_start=slot["slot_start"])
    assert booked["say_when"].endswith(slot["label"].split(", ")[1]) and booked["phone"] == "01799999998"
    assert "say_when exactly" in AGENT_SYSTEM and "English digits" in AGENT_SYSTEM


def test_bangla_hours_list_every_day():
    # Summarising the days itself, a real model wrote "সোম-শুক্র" although Friday is closed.
    lines = tools.get_clinic_info(_ctx("bn"))["hours_text"].splitlines()
    assert len(lines) == 7 and "শুক্রবার: বন্ধ" in lines and "শনিবার: ১০:০০-২০:০০" in lines
    assert "hours_text" in AGENT_SYSTEM


def test_asking_for_a_person_always_reaches_staff(client, chat):
    # A real model replied "সংযুক্ত করছি" without calling the handover tool, so no one was told.
    r = chat("web-staff-bn", "আমি ক্লিনিকের একজন স্টাফের সাথে কথা বলতে চাই")
    assert r["tools"] == ["escalate_to_human"] and r["route"] == "handover" and "স্টাফ" in r["reply"]
    esc = db.rows(db.select(db.escalations.c.kind).where(db.escalations.c.conversation_id == r["conversation_id"]))
    assert esc == [{"kind": "human_request"}]


def test_tool_names_are_removed_from_replies():
    assert _TOOL_NAMES.sub("", "আমি আপনাকে স্টাফের সঙ্গে সংযুক্ত করছি। (escalate_to_human)") == \
        "আমি আপনাকে স্টাফের সঙ্গে সংযুক্ত করছি।"
