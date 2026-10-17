"""Runtime configuration, read once from environment variables.

Every setting has a safe default so the stack starts with zero secrets
(LLM_PROVIDER=mock). Real providers are switched on by setting a key.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Settings:
    # LLM routing -----------------------------------------------------------
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "mock"))
    small_model: str = field(default_factory=lambda: _env("SMALL_MODEL"))
    main_model: str = field(default_factory=lambda: _env("MAIN_MODEL"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    max_agent_steps: int = field(default_factory=lambda: int(_env("MAX_AGENT_STEPS", "5")))
    usd_to_bdt: float = field(default_factory=lambda: float(_env("USD_TO_BDT", "122")))

    # Storage ---------------------------------------------------------------
    database_url: str = field(
        default_factory=lambda: _env("DATABASE_URL", f"sqlite:///{BASE_DIR.parent / 'clinicdesk.db'}")
    )

    # Channels --------------------------------------------------------------
    whatsapp_token: str = field(default_factory=lambda: _env("WHATSAPP_TOKEN"))
    whatsapp_phone_id: str = field(default_factory=lambda: _env("WHATSAPP_PHONE_NUMBER_ID"))
    whatsapp_verify_token: str = field(default_factory=lambda: _env("WHATSAPP_VERIFY_TOKEN", "clinicdesk-verify"))
    whatsapp_app_secret: str = field(default_factory=lambda: _env("WHATSAPP_APP_SECRET"))
    whatsapp_api_version: str = field(default_factory=lambda: _env("WHATSAPP_API_VERSION", "v21.0"))

    # Integrations ----------------------------------------------------------
    n8n_webhook_url: str = field(default_factory=lambda: _env("N8N_WEBHOOK_URL"))
    n8n_webhook_secret: str = field(default_factory=lambda: _env("N8N_WEBHOOK_SECRET"))
    admin_token: str = field(default_factory=lambda: _env("ADMIN_TOKEN", "change-me"))

    # Safety ----------------------------------------------------------------
    rate_limit_per_minute: int = field(default_factory=lambda: int(_env("RATE_LIMIT_PER_MINUTE", "20")))
    # Hard cap on model calls per day (0 = no cap). Past the cap the offline model answers,
    # so a public demo can never burn through an API quota.
    daily_llm_call_budget: int = field(default_factory=lambda: int(_env("DAILY_LLM_CALL_BUDGET", "0")))
    # Public demo with fake data: allows the shared demo admin token even with a real model.
    demo_mode: bool = field(default_factory=lambda: _env("DEMO_MODE", "false").lower() == "true")

    knowledge_dir: Path = BASE_DIR / "knowledge" / "pages"
    clinic_file: Path = BASE_DIR / "knowledge" / "clinic.yaml"
    pricing_file: Path = BASE_DIR / "llm" / "pricing.yaml"


settings = Settings()
