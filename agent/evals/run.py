"""Run the behaviour evals and write evals/RESULTS.md.

    python -m evals.run
Uses a fresh temporary database, so it never touches real data.
Optional env: EVAL_RESULTS (where to write the report), EVAL_SUMMARY (also write
a JSON summary there, used by evals.compare), EVAL_DELAY_S (pause between
messages, to stay under free-tier rate limits).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/evals.db"
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "1000")
os.environ.pop("N8N_WEBHOOK_URL", None)

import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


def check(expect: dict, r: dict) -> list[str]:
    fails = []
    reply = r["reply"].lower()
    if "route" in expect and r["route"] != expect["route"]:
        fails.append(f"route {r['route']} != {expect['route']}")
    for t in expect.get("tools", []):
        if t not in r["tools"]:
            fails.append(f"tool {t} not used (used {r['tools']})")
    for s in expect.get("contains", []):
        if s.lower() not in reply:
            fails.append(f"missing '{s}'")
    for s in expect.get("not_contains", []):
        if s.lower() in reply:
            fails.append(f"should not contain '{s}'")
    if "language" in expect and r["language"] != expect["language"]:
        fails.append(f"language {r['language']} != {expect['language']}")
    return fails


def main() -> None:
    cases = yaml.safe_load((HERE / "cases.yaml").read_text(encoding="utf-8"))
    delay = float(os.getenv("EVAL_DELAY_S", "0"))
    results = []
    with TestClient(app) as c:
        for case in cases:
            sender = f"eval-{case['id']}"
            r = {}
            for turn in case["turns"]:
                r = c.post("/api/chat", json={"sender_id": sender, "message": turn}).json()
                time.sleep(delay)
            fails = check(case["expect"], r)
            results.append((case, r, fails))
        stats = c.get("/api/admin/stats", headers={"Authorization": f"Bearer {settings.admin_token}"}).json()

    passed = sum(1 for *_, f in results if not f)
    by_cat: dict[str, list[int]] = {}
    for case, _, fails in results:
        by_cat.setdefault(case["category"], [0, 0])
        by_cat[case["category"]][0] += not fails
        by_cat[case["category"]][1] += 1

    lines = [
        "# Eval results", "",
        f"- Run: {datetime.now():%Y-%m-%d %H:%M}",
        f"- Provider: `{settings.llm_provider}`" + (" (offline deterministic mock — re-run with a real provider key to "
                                                     "evaluate model behaviour)" if settings.llm_provider == "mock" else ""),
        f"- **Passed {passed}/{len(results)}**",
        f"- Model calls: {stats['llm_calls']} · total cost ৳{stats['cost_bdt']} · "
        f"cost per conversation ৳{stats['cost_per_conversation_bdt']}",
        "", "| Category | Passed |", "| --- | --- |",
        *[f"| {k} | {v[0]}/{v[1]} |" for k, v in by_cat.items()],
        "", "| Case | Route | Tools | Result |", "| --- | --- | --- | --- |",
        *[f"| {c['id']} | {r.get('route')} | {', '.join(r.get('tools', [])) or '—'} | "
          f"{'✅' if not f else '❌ ' + '; '.join(f)} |" for c, r, f in results],
    ]
    out = Path(os.getenv("EVAL_RESULTS", HERE / "RESULTS.md"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:6]))
    if os.getenv("EVAL_SUMMARY"):
        calls = db.llm_calls.c
        by_model = db.rows(db.select(calls.provider, calls.model, db.func.count().label("calls"),
                                     db.func.avg(calls.latency_ms).label("avg_ms"))
                           .group_by(calls.provider, calls.model))
        Path(os.environ["EVAL_SUMMARY"]).write_text(json.dumps({
            "provider": settings.llm_provider, "passed": passed, "total": len(results),
            "by_category": by_cat, "llm_calls": stats["llm_calls"], "cost_bdt": stats["cost_bdt"],
            "cost_per_conversation_bdt": stats["cost_per_conversation_bdt"],
            # Without its API key (or past the daily budget) the router uses the offline model: provider "mock".
            "by_model": [{**m, "avg_ms": round(m["avg_ms"] or 0)} for m in by_model],
            "failed": [{"id": c_["id"], "why": f, "reply": r.get("reply", "")[:200]} for c_, r, f in results if f],
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    for c_, r, f in results:
        if f:
            print(f"FAIL {c_['id']}: {f}  -> {r.get('reply', '')[:120]!r}")


if __name__ == "__main__":
    main()
