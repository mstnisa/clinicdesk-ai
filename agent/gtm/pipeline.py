"""GTM pipeline: find → enrich → score (ICP) → personalise → queue for approval.

    python -m gtm.pipeline                 # sample data, offline (no keys)
    python -m gtm.pipeline --source apify  # live Google Maps data via Apify

Nothing is ever sent from here. Drafts land in the `outreach` table with
status `pending_approval`; a human approves them in the dashboard, and the
n8n workflow sends only approved ones (the API refuses to mark anything else
as sent).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
import yaml

from app import db
from app.llm.base import LLMError
from app.llm.router import Router

HERE = Path(__file__).parent
ICP = yaml.safe_load((HERE / "icp.yaml").read_text(encoding="utf-8"))
APIFY_ACTOR = "compass~crawler-google-places"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[a-z.]{2,}", re.I)
_WHATSAPP = re.compile(r"wa\.me/|api\.whatsapp\.com|whatsapp", re.I)
_BOOKING = re.compile(r"calendly|setmore|simplybook|book (an )?(appointment|now|online)|online booking|booking widget", re.I)


@dataclass
class Place:
    name: str
    category: str = ""
    city: str = ""
    address: str = ""
    website: str = ""
    phone: str = ""
    rating: float = 0.0
    reviews: int = 0
    closed: bool = False
    notes: str = ""
    email: str = ""
    signals: dict = field(default_factory=dict)
    score: int = 0
    reasons: list = field(default_factory=list)


# ------------------------------------------------------------------ find
def from_sample(path: Path = HERE / "sample_places.csv") -> list[Place]:
    with path.open(encoding="utf-8") as f:
        return [Place(r["name"], r["category"], r["city"], r["address"], r["website"], r["phone"],
                      float(r["rating"] or 0), int(r["reviews"] or 0), r["status"] != "OPERATIONAL", r["notes"])
                for r in csv.DictReader(f)]


def from_apify(token: str) -> list[Place]:
    """Google Maps places via the Apify Google Maps Scraper actor (sync run)."""
    r = httpx.post(
        f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/run-sync-get-dataset-items",
        headers={"Authorization": f"Bearer {token}"}, timeout=300,  # token in a header, not the URL
        json={"searchStringsArray": ICP["search_queries"], "locationQuery": ICP["location"],
              "maxCrawledPlacesPerSearch": ICP["max_places_per_query"], "language": "en"},
    )
    r.raise_for_status()
    seen, out = set(), []
    for p in r.json():
        key = (p.get("title"), p.get("address"))
        if key in seen:
            continue
        seen.add(key)
        out.append(Place(p.get("title", ""), p.get("categoryName", ""), p.get("city", ""), p.get("address", ""),
                         p.get("website") or "", p.get("phone") or "", float(p.get("totalScore") or 0),
                         int(p.get("reviewsCount") or 0), bool(p.get("permanentlyClosed"))))
    return out


# ---------------------------------------------------------------- enrich
def enrich(p: Place) -> Place:
    text = p.notes
    if p.website and not p.website.endswith(".example.com"):
        try:
            html = httpx.get(p.website, timeout=12, follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 (ClinicDesk research bot)"}).text[:300_000]
            text += " " + html
            emails = [e for e in _EMAIL.findall(html) if not e.lower().endswith((".png", ".jpg", ".webp"))]
            p.email = emails[0] if emails else ""
        except httpx.HTTPError:
            pass
    cat = f"{p.category} {p.name}".lower()
    category = p.category.lower()
    # Found on live Google Maps data: "Rehabilitation center" also covers drug-addiction centres, and many
    # small physio clinics call themselves "hospital". Judge hospitals by the listed category first.
    physio_category = bool(re.search(r"physio|physical therap|chiropract", category))
    p.signals = {
        "is_physio_or_rehab": bool(re.search(r"physio|rehab|spine|sports", cat)),
        "in_target_city": "dhaka" in f"{p.city} {p.address}".lower(),
        "has_website": bool(p.website),
        "lists_whatsapp": bool(_WHATSAPP.search(text)),
        "no_online_booking": not _BOOKING.search(text),
        "good_rating": p.rating >= 4.0,
        "enough_reviews": 20 <= p.reviews <= 500,
        "is_hospital": "hospital" in category or ("hospital" in cat and not physio_category),
        "is_addiction_centre": bool(re.search(r"addiction|drug|de-?addiction|মাদক|নেশা", cat)),
        "permanently_closed": p.closed,
        "outside_target_city": "dhaka" not in f"{p.city} {p.address}".lower(),
    }
    return p


# ----------------------------------------------------------------- score
def score(p: Place) -> Place:
    if any(p.signals.get(d) for d in ICP["disqualify"]):
        p.score, p.reasons = 0, [f"Disqualified: {d}" for d in ICP["disqualify"] if p.signals.get(d)]
        return p
    pts, why = 0, []
    for name, rule in ICP["signals"].items():
        if p.signals.get(name):
            pts += rule["points"]
            why.append(rule["why"])
    p.score, p.reasons = min(pts, 100), why
    return p


# ------------------------------------------------------------ personalise
EMAIL_SYSTEM = """You write short, honest B2B cold emails for ClinicDesk AI, a WhatsApp + website
AI receptionist for physiotherapy clinics in Bangladesh (answers patients 24/7 from clinician-approved
information, books appointments, flags urgent symptoms to staff).
Rules: under 110 words, plain English, one specific observation about THIS clinic from the facts given,
one clear benefit, a soft call to action (a 15-minute demo). No hype, no invented facts or numbers,
no fake familiarity. Return JSON: {"subject": "...", "body": "..."}"""


def personalise(p: Place, router: Router) -> dict:
    facts = {"clinic": p.name, "area": p.address, "rating": p.rating, "reviews": p.reviews,
             "why_fit": p.reasons, "notes": p.notes}
    try:
        res = router.call(purpose="gtm", tier="small", system=EMAIL_SYSTEM, json_mode=True, max_tokens=500,
                          messages=[{"role": "user", "content": json.dumps(facts, ensure_ascii=False)}])
        found = re.search(r"\{.*\}", res.text, re.S)  # tolerate ```json fences
        data = json.loads(found.group(0)) if found else {}
        if data.get("subject") and data.get("body"):
            return data
    except (LLMError, json.JSONDecodeError):
        pass
    return template_email(p)


def template_email(p: Place) -> dict:
    """Deterministic fallback (and the offline/mock-mode writer)."""
    hook = ("I noticed patients can reach you on WhatsApp but there's no way to book online"
            if p.signals.get("lists_whatsapp") and p.signals.get("no_online_booking")
            else "I noticed most of your patient enquiries seem to come in by phone and Facebook")
    area = p.address.split(",")[0]
    body = (f"Hi {p.name.replace(' (fictional)', '')} team,\n\n{hook}. With {p.reviews} reviews in {area}, "
            "your front desk must answer the same questions many times a day.\n\n"
            "ClinicDesk AI answers patients on WhatsApp in Bangla and English, using only information your "
            "physiotherapists approve, books appointments into your calendar, and alerts your staff straight "
            "away if someone describes urgent symptoms.\n\nWould a 15-minute demo next week be useful?\n\n"
            "Best regards,\nMst. Awalunnisa")
    return {"subject": f"WhatsApp bookings for {p.name.replace(' (fictional)', '')}", "body": body}


# ------------------------------------------------------------------- run
def run(source: str = "sample", limit: int = 50, dry_run: bool = False) -> list[Place]:
    db.init_db()
    places = from_apify(os.environ["APIFY_TOKEN"]) if source == "apify" else from_sample()
    places = [score(enrich(p)) for p in places[:limit]]
    router = Router()
    already = {(r["company"], r["website"]) for r in db.rows(db.select(db.outreach.c.company, db.outreach.c.website))}
    queued = []
    for p in sorted(places, key=lambda x: x.score, reverse=True):
        if p.score < ICP["min_score_to_contact"] or (p.name, p.website) in already:
            continue  # below ICP threshold, or already contacted / queued (re-runs are safe)
        mail = personalise(p, router) if router.provider.name != "mock" else template_email(p)
        if not dry_run:
            db.insert_row(db.outreach, company=p.name, website=p.website, city=p.city, email=p.email,
                          icp_score=p.score, icp_reasons="; ".join(p.reasons), subject=mail["subject"],
                          body=mail["body"], status="pending_approval")
        queued.append(p)
    return places


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["sample", "apify"], default="sample")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--dry-run", action="store_true", help="score only, do not queue drafts")
    a = ap.parse_args()
    places = run(a.source, a.limit, a.dry_run)
    print(f"{'Score':>5}  {'Contact?':8}  Clinic")
    for p in sorted(places, key=lambda x: x.score, reverse=True):
        ok = "yes" if p.score >= ICP["min_score_to_contact"] else "no"
        print(f"{p.score:>5}  {ok:8}  {p.name}  —  {'; '.join(p.reasons)[:90]}")
    if not a.dry_run:
        print("\nDrafts queued with status pending_approval. Approve them in the dashboard (Outreach tab).")


if __name__ == "__main__":
    main()
