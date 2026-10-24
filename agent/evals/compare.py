"""Run the same evals on every real model provider that has a key, and compare them.

    python -m evals.compare                 # every provider with a key in ../.env
    python -m evals.compare groq gemini     # only these
Keys are read from the environment or from the repository's .env file (never
committed). Each provider runs in its own process with a fresh database.
Writes evals/COMPARISON.md and one report per provider in evals/results/.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).parent
ROOT = HERE.parent.parent
KEYS = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY"}
# Seconds between messages, to stay under each free tier's requests-per-minute limit.
DELAY = {"groq": 4, "gemini": 8, "anthropic": 1, "openai": 1}


def _dotenv() -> dict[str, str]:
    path = ROOT / ".env"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            out[name.strip()] = value.strip().strip('"').strip("'")
    return out


def run_one(provider: str, env: dict[str, str]) -> dict:
    summary = Path(tempfile.mkdtemp()) / "summary.json"
    env = {**env, "LLM_PROVIDER": provider, "EVAL_SUMMARY": str(summary),
           "EVAL_RESULTS": str(HERE / "results" / f"{provider}.md"),
           "EVAL_DELAY_S": env.get("EVAL_DELAY_S", str(DELAY[provider])),
           "DAILY_LLM_CALL_BUDGET": "0", "PYTHONIOENCODING": "utf-8"}
    subprocess.run([sys.executable, "-m", "evals.run"], cwd=HERE.parent, env=env, check=True)
    return json.loads(summary.read_text(encoding="utf-8"))


def main() -> None:
    env = {**_dotenv(), **os.environ}
    wanted = sys.argv[1:] or [p for p, k in KEYS.items() if env.get(k)]
    if not wanted:
        sys.exit("No API keys found. Add GROQ_API_KEY / GEMINI_API_KEY / ... to the .env file.")
    tiers = yaml.safe_load((HERE.parent / "app" / "llm" / "pricing.yaml").read_text(encoding="utf-8"))["providers"]

    rows = []
    for p in wanted:
        print(f"\n=== {p} ===", flush=True)
        s = run_one(p, env)
        real = [m for m in s["by_model"] if m["provider"] != "mock"]
        if not real:
            print(f"{p}: no real model calls (missing key?), skipped")
            continue
        calls = sum(m["calls"] for m in real)
        avg_ms = round(sum(m["avg_ms"] * m["calls"] for m in real) / calls)
        rows.append((p, s, avg_ms))

    lines = [
        "# Model comparison", "",
        f"Run {datetime.now():%Y-%m-%d %H:%M}. The same {rows[0][1]['total'] if rows else 0} eval cases "
        "([cases.yaml](cases.yaml)) on each provider, each with a fresh database. "
        "Cost is from the token counts and the prices in [pricing.yaml](../app/llm/pricing.yaml), "
        "at list price (on the free tiers the real bill is ৳0).", "",
        "| Provider | Small model (triage) | Main model (agent) | Passed | Model calls | Avg latency | "
        "Cost per conversation | Cost per 1,000 conversations |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p, s, avg_ms in rows:
        per = s["cost_per_conversation_bdt"]
        lines.append(f"| {p} | `{tiers[p]['small']}` | `{tiers[p]['main']}` | **{s['passed']}/{s['total']}** | "
                     f"{s['llm_calls']} | {avg_ms / 1000:.1f} s | ৳{per} | ৳{round(per * 1000)} |")
    lines += ["", "## Failed cases", ""]
    for p, s, _ in rows:
        lines.append(f"**{p}** ([full report](results/{p}.md)): " +
                     ("none" if not s["failed"] else ""))
        lines += [f"- `{f['id']}`: {'; '.join(f['why'])}" for f in s["failed"]]
        lines.append("")
    (HERE / "COMPARISON.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
