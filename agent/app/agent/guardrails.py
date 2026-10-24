"""Deterministic safety checks that run BEFORE any model is called.

Rules, not prompts, decide the safety-critical paths:
  * red-flag symptoms  -> escalate to a human, fixed safe reply, no LLM
  * prompt injection   -> logged as a security event, fixed reply, no LLM
  * PII                -> redacted before anything is written to logs
These paths cost nothing, cannot be talked around, and are fully unit-tested.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass

# ---------------------------------------------------------------- language
_BN = re.compile(r"[ঀ-৿]")


def detect_language(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    bn = sum(1 for c in letters if _BN.match(c))
    return "bn" if bn / len(letters) > 0.3 else "en"


# --------------------------------------------------------------- red flags
# Warning signs drawn from the approved knowledge pages. Each entry is
# (label, compiled pattern). Bangla and common Banglish spellings included.
_RED_FLAGS: list[tuple[str, re.Pattern]] = [
    ("bladder/bowel control", re.compile(
        r"(can'?t|cannot|unable to|lost|loss of|no) (control|hold)\w* (my )?(bladder|bowel|urine|pee|stool)"
        r"|incontinen|প্রস্রাব.{0,20}(আটকে|নিয়ন্ত্রণ|ধরে রাখতে|বের হয়ে)|পায়খানা.{0,20}(নিয়ন্ত্রণ|ধরে রাখতে)", re.I)),
    ("saddle numbness", re.compile(
        r"numb\w*.{0,30}(groin|genital|buttock|saddle|private)|(groin|genital|buttock|saddle).{0,30}numb"
        r"|(যৌনাঙ্গ|নিতম্ব|পায়ুপথ).{0,25}অবশ|অবশ.{0,25}(যৌনাঙ্গ|নিতম্ব|পায়ুপথ)", re.I)),
    ("progressive leg weakness", re.compile(
        r"(leg|legs|foot).{0,25}(getting weaker|weakness getting worse|giving way|can'?t lift)"
        r"|can'?t walk|cannot walk|unable to walk|হাঁটতে পারছি না|পা.{0,15}(দুর্বল হয়ে যাচ্ছে|অবশ হয়ে যাচ্ছে)", re.I)),
    ("chest pain", re.compile(r"chest pain|pain in (my )?chest|বুকে ব্যথা|বুক ব্যথা", re.I)),
    ("fever with pain", re.compile(r"fever|high temperature|জ্বর", re.I)),
    ("serious trauma", re.compile(
        r"(after|since) (a |an )?(bad |serious )?(fall|accident|crash)|fell (down|from)|road accident"
        r"|পড়ে গিয়ে|দুর্ঘটনা|এক্সিডেন্ট|accident e", re.I)),
    ("possible fracture", re.compile(r"broken bone|fracture|হাড় ভেঙে|হাড় ভাঙা", re.I)),
    ("cancer history / weight loss", re.compile(
        r"history of cancer|had cancer|unexplained weight loss|losing weight without|ক্যান্সার|ওজন কমে যাচ্ছে", re.I)),
    ("self-harm", re.compile(r"kill myself|suicid|end my life|আত্মহত্যা|মরে যেতে চাই", re.I)),
]


def red_flags(text: str) -> list[str]:
    return [label for label, pat in _RED_FLAGS if pat.search(text)]


# --------------------------------------------------------- prompt injection
_INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above) (instructions|rules|prompts?)"
    r"|disregard (your|the|all) (rules|instructions)"
    r"|(reveal|show|print|repeat) (me )?(your|the) (system )?(prompt|instructions)"
    r"|you are now|act as (an? )?(unrestricted|dan|developer)|developer mode|jailbreak"
    r"|pretend (you are|to be) (a )?(doctor|admin)"
    r"|আগের (সব )?নির্দেশ(না)? (ভুলে যাও|বাদ দাও)|সিস্টেম প্রম্পট",
    re.I,
)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION.search(text))


# ----------------------------------------------------------- human request
# A real model said "I'm transferring you" without calling the handover tool,
# so nobody was told. Asking for a person is now a rule, like red flags.
_HUMAN = re.compile(
    r"\b(real|actual|live) (person|human)\b|\bhuman (agent|being)\b"
    r"|\b(talk|speak|chat) (to|with) (a |an |the |your )?(human|person|staff|someone|receptionist|front desk)\b"
    r"|(মানুষের|মানুষ|স্টাফের|স্টাফ|কর্মীর|রিসেপশনের|কারো|কারও) (সাথে|সঙ্গে) কথা",
    re.I,
)


def wants_human(text: str) -> bool:
    return bool(_HUMAN.search(text))


# ----------------------------------------------------------------- PII
_PII = [
    (re.compile(r"(?:\+?88)?01[3-9]\d{8}"), "[PHONE]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"\b\d{10}\b|\b\d{13}\b|\b\d{17}\b"), "[ID-NUMBER]"),  # BD NID lengths
]


def redact(text: str) -> str:
    for pat, repl in _PII:
        text = pat.sub(repl, text)
    return text


def mask_sender(sender: str) -> str:
    return sender[:4] + "•••" + sender[-3:] if len(sender) > 7 else "•••"


# ----------------------------------------------------------- rate limiting
@dataclass
class RateLimiter:
    per_minute: int
    _hits: dict = None

    def __post_init__(self):
        self._hits = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True
