"""Launcher for the free Hugging Face Space.

The Space only holds this file, requirements.txt and README.md. On start it
downloads the app from GitHub, loads the demo data and serves it on port 7860.
"""
import io
import os
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = "https://codeload.github.com/mstnisa/clinicdesk-ai/tar.gz/refs/heads/main"
HOME = Path("/tmp/clinicdesk")

# Public demo: fake data, shared dashboard token, strict limits.
# Add GROQ_API_KEY and LLM_PROVIDER=groq as Space secrets to use a real model.
os.environ.setdefault("DEMO_MODE", "true")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/clinicdesk.db")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "12")
os.environ.setdefault("DAILY_LLM_CALL_BUDGET", "400")

if not (HOME / "agent").exists():
    data = urllib.request.urlopen(REPO, timeout=60).read()
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall("/tmp")
    Path("/tmp/clinicdesk-ai-main").rename(HOME)

os.chdir(HOME / "agent")
sys.path.insert(0, str(HOME / "agent"))

try:  # free Spaces run on ZeroGPU, which expects one GPU function; the app itself only needs the CPU
    import spaces

    @spaces.GPU
    def _unused_gpu_hook():
        return None

    from spaces.config import Config

    if Config.zero_gpu:  # normally sent by Gradio's launch(); this app is served by uvicorn instead
        from spaces.zero import client

        client.startup_report()
except ImportError:
    pass

import subprocess  # noqa: E402

# Sample data is written with the free offline model, so restarts never spend real model calls.
subprocess.run([sys.executable, "-m", "scripts.seed_demo"], check=False,
               env={**os.environ, "LLM_PROVIDER": "mock", "PYTHONPATH": str(HOME / "agent")})

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host="0.0.0.0", port=7860, proxy_headers=True, forwarded_allow_ips="*")
