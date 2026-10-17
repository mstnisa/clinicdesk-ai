"""Offline, deterministic stand-in for an LLM.

It lets anyone run the full stack, the tests and the eval suite with no API
key and no cost. It follows the same contract as a real model: it reads the
transcript, decides which tool to call, then writes a reply from the tool
result. The routing here is rule-based, so replies are simpler than a real
model's, but every code path (tools, bookings, caching, costs) is exercised.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

from .base import LLMResult, Provider, ToolCall, Usage

_BN = re.compile(r"[ঀ-৿]")
_PHONE = re.compile(r"(?:\+?88)?01[3-9]\d{8}")
_SLOT_LINE = re.compile(r"(\d)\)\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")

BOOK_WORDS = r"book|appointment|slot|schedule|visit|serial|অ্যাপয়েন্টমেন্ট|অ্যাপয়েন্টমেন্ট|বুক|সিরিয়াল|দেখাতে চাই|সময় নিতে"
INFO_WORDS = r"open|hour|time|fee|price|cost|charge|address|where|location|payment|bkash|cancel|কখন|খোলা|ফি|খরচ|দাম|ঠিকানা|কোথায়|টাকা"
HUMAN_WORDS = r"human|real person|staff|talk to (a )?(doctor|someone)|মানুষ|স্টাফ|কারো সাথে কথা"
GREET_WORDS = r"^(hi|hello|hey|salam|assalamu|good (morning|evening)|হ্যালো|হাই|আসসালামু|সালাম|নমস্কার)\b"


def _lang(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    return "bn" if letters and sum(bool(_BN.match(c)) for c in letters) / len(letters) > 0.3 else "en"


def _strip_context(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.startswith("[Context]")).strip()


def _intent(text: str, history: str) -> str:
    t = text.lower()
    if re.search(HUMAN_WORDS, t):
        return "human_request"
    if re.search(GREET_WORDS, t.strip()) and len(t) < 40:
        return "greeting"
    offered_slots = bool(_SLOT_LINE.search(history))
    if re.search(BOOK_WORDS, t) or (offered_slots and (re.fullmatch(r"\s*[1-6]\s*", t) or _PHONE.search(t)
                                                        or re.search(r"name|নাম", t))):
        return "booking"
    if re.search(INFO_WORDS, t) and not re.search(r"pain|ব্যথা|exercise|ব্যায়াম|x-?ray|mri", t):
        return "clinic_info"
    return "health_question"


def _usage(system: str, messages: list[dict], out: str) -> Usage:
    chars = len(system) + sum(len(str(m.get("content") or "")) for m in messages)
    return Usage(input_tokens=max(1, chars // 4), output_tokens=max(1, len(out) // 4))


class MockProvider(Provider):
    name = "mock"

    def complete(self, model, system, messages, tools=None, max_tokens=600, temperature=0.2, json_mode=False):
        last_user_i = max(i for i, m in enumerate(messages) if m["role"] == "user")
        raw_user = messages[last_user_i]["content"]
        user = _strip_context(raw_user)
        lang = _lang(user)
        history = "\n".join(str(m.get("content") or "") for m in messages[:last_user_i] if m["role"] == "assistant")

        if json_mode:  # triage
            intent = _intent(user, history)
            reply = ""
            if intent == "greeting":
                reply = ("আসসালামু আলাইকুম! আমি ক্লিনিকের ভার্চুয়াল সহকারী। ব্যথা নিয়ে প্রশ্ন, ফি বা অ্যাপয়েন্টমেন্ট — কীভাবে সাহায্য করতে পারি?"
                         if lang == "bn" else
                         "Hello! I'm the clinic's virtual assistant. I can answer questions, share fees and hours, "
                         "or book an appointment. How can I help?")
            out = json.dumps({"intent": intent, "reply": reply}, ensure_ascii=False)
            return LLMResult(out, model, [], _usage(system, messages, out))

        tool_msgs = [m for m in messages[last_user_i + 1:] if m["role"] == "tool"]
        if not tool_msgs:
            call = self._choose_tool(user, raw_user, history)
            if call:
                return LLMResult("", model, [call], _usage(system, messages, json.dumps(call.args)))
            out = self._ask_details(lang)
            return LLMResult(out, model, [], _usage(system, messages, out))

        last = tool_msgs[-1]
        out = self._write(last["name"], json.loads(last["content"]), lang)
        return LLMResult(out, model, [], _usage(system, messages, out))

    # ---------------------------------------------------------- decide
    def _choose_tool(self, user: str, raw_user: str, history: str) -> ToolCall | None:
        intent = _intent(user, history)
        cid = f"call_{uuid.uuid4().hex[:8]}"
        if intent == "human_request":
            return ToolCall(cid, "escalate_to_human", {"reason": "Patient asked for a person"})
        if intent == "clinic_info":
            return ToolCall(cid, "get_clinic_info", {})
        if intent == "booking":
            slots = dict(_SLOT_LINE.findall(history))
            choice = re.search(r"\b([1-6])\b", user)
            phone = _PHONE.search(re.sub(r"[\s-]", "", raw_user))
            name = re.search(r"(?:my name is|name:|i am|i'm|আমার নাম)\s*([A-Za-zঀ-৿ .]{2,40}?)(?:[,.\n]|$| and| phone| মোবাইল| নম্বর)", user, re.I)
            if slots and choice and phone and name:
                return ToolCall(cid, "book_appointment", {
                    "name": name.group(1).strip(), "phone": phone.group(0),
                    "slot_start": slots.get(choice.group(1), next(iter(slots.values()))),
                    "service": "assessment", "reason": "Booked via chat"})
            if not slots:
                return ToolCall(cid, "check_availability", {"service": "assessment"})
            return None  # slots offered but details missing -> ask
        return ToolCall(cid, "search_knowledge", {"query": user})

    @staticmethod
    def _ask_details(lang: str) -> str:
        return ("বুক করতে স্লট নম্বর, আপনার পুরো নাম আর মোবাইল নম্বর একসাথে লিখুন। যেমন: 1, আমার নাম রহিম, 01712345678"
                if lang == "bn" else
                "To book, send the slot number, your full name and mobile number together, "
                "e.g. \"1, my name is Rahim, 01712345678\".")

    # ----------------------------------------------------------- write
    @staticmethod
    def _write(tool: str, data: dict, lang: str) -> str:
        bn = lang == "bn"
        if tool == "search_knowledge":
            if not data.get("results"):
                return ("এ বিষয়ে আমাদের ক্লিনিকের অনুমোদিত তথ্য এখনো নেই। একজন ফিজিওথেরাপিস্ট অ্যাসেসমেন্টে দেখে বলতে পারবেন — বুক করে দেব?"
                        if bn else
                        "We don't have clinician-approved information on that yet. A physiotherapist can "
                        "check it at an assessment. Shall I book one for you?")
            top = data["results"][0]
            body = re.sub(r"\s*\[[^\]]+\]", "", top["text"])  # already in the patient's language
            src = top["citation"].split(":")[0]
            tail = "\nঅ্যাপয়েন্টমেন্ট লাগলে বলুন।" if bn else "\nWant me to book an assessment?"
            return f"{body}\n(Source: {src}){tail}"
        if tool == "get_clinic_info":
            hours = data["hours"]
            days = ", ".join(f"{d[:3]} {h}" for d, h in hours.items())
            fees = "; ".join(f"{(s['name'])}: ৳{s['fee_bdt']}" for s in data["services"])
            if bn:
                return (f"{data['name']}\nঠিকানা: {data['address']}\nসময়: {days} (শুক্রবার বন্ধ)\n"
                        f"ফি: {fees}\n{data['payment']}")
            return f"{data['name']}\nAddress: {data['address']}\nHours: {days} (Friday closed)\nFees: {fees}\n{data['payment']}"
        if tool == "check_availability":
            lines = []
            for i, s in enumerate(data["slots"][:4], 1):
                d = datetime.strptime(s["slot_start"], "%Y-%m-%d %H:%M")
                lines.append(f"{i}) {s['slot_start']} ({d.strftime('%A')})")
            head = "এই সময়গুলো খালি আছে:" if bn else "These slots are free:"
            ask = ("স্লট নম্বর, আপনার পুরো নাম আর মোবাইল নম্বর পাঠান।" if bn
                   else "Reply with the slot number, your full name and mobile number.")
            return "\n".join([head, *lines, ask])
        if tool == "book_appointment":
            if data.get("error"):
                return ("দুঃখিত, বুকিং হয়নি: " if bn else "Sorry, the booking didn't go through: ") + data["error"]
            d = datetime.strptime(data["slot_start"], "%Y-%m-%d %H:%M")
            if bn:
                return (f"✅ বুকিং নিশ্চিত, {data['name']}!\n{d.strftime('%A %d %b, %H:%M')} — {data['service']} (৳{data['fee_bdt']})\n"
                        f"ঠিকানা: {data['address']}\nএকদিন আগে মনে করিয়ে দেব।")
            return (f"✅ Booked, {data['name']}!\n{d.strftime('%A %d %b, %H:%M')} — {data['service']} (৳{data['fee_bdt']})\n"
                    f"Address: {data['address']}\nWe'll remind you the day before.")
        if tool == "escalate_to_human":
            return ("ঠিক আছে, একজন স্টাফকে জানিয়েছি। ক্লিনিকের সময়ের মধ্যে তিনি এখানেই উত্তর দেবেন।"
                    if bn else "Done — I've asked a staff member to take over. They'll reply here during clinic hours.")
        if tool == "save_lead":
            return "ধন্যবাদ! আমাদের টিম শীঘ্রই যোগাযোগ করবে।" if bn else "Thanks! Our team will contact you soon."
        return "OK."
