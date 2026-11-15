"""Pilihan provider + model AI yang bisa diganti SAAT RUNTIME.

Motivasi: mengganti model tidak boleh mengharuskan edit `.env` + restart.
Pilihan aktif disimpan di `data/ai_settings.json`:

    {"provider": "bangunweb", "model": "deepseek-v4-flash"}

Aturan prioritas:
1. Bila file state ada dan valid → isinya yang dipakai.
2. Bila tidak → nilai `.env` (AI_PROVIDER + model per provider) menjadi
   pilihan awal.

Web UI dan CLI menulis file yang sama; server web memuat ulang secara
otomatis bila file berubah (mtime), jadi pergantian dari CLI pun langsung
berlaku di server yang sedang berjalan. Kunci API tetap di `.env` — yang
diganti di sini hanya provider + modelnya.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Optional

from config.settings import Settings
from core.ai_providers import BaseAIProvider, create_ai_provider
from utils.logger import get_logger

log = get_logger(__name__)

STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "ai_settings.json"

# Provider → nama field model di Settings.
MODEL_FIELDS = {
    "bangunweb": "bangunweb_model",
    "openai": "openai_model",
    "anthropic": "anthropic_model",
    "deepseek": "deepseek_model",
    "grok": "xai_model",
    "nous": "nous_model",
    "custom": "ai_custom_model",
}
PROVIDERS = tuple(MODEL_FIELDS)

# Saran model per provider untuk dropdown Web UI / CLI. Ini hanya saran —
# model lain apa pun tetap bisa diketik manual (mis. model routed
# BangunWeb seperti "deepseek-v4-flash").
KNOWN_MODELS: dict[str, list[str]] = {
    "bangunweb": ["bw-swift", "bw-focus", "bw-pro", "bw-apex", "bw-unbound"],
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini"],
    "anthropic": ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-1"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "grok": ["grok-4", "grok-3", "grok-3-mini"],
    "nous": ["Hermes-4-70B", "Hermes-4-405B"],
    "custom": [],
}


def normalize_provider(name: str) -> str:
    name = (name or "bangunweb").strip().lower()
    if name in {"xai", "x-ai"}:
        return "grok"
    if name in {"nousportal", "nous-portal"}:
        return "nous"
    return name


def default_model(settings: Settings, provider: str) -> str:
    field = MODEL_FIELDS.get(provider)
    return str(getattr(settings, field, "")) if field else ""


def load_choice(settings: Settings) -> tuple[str, str]:
    """(provider, model) efektif saat ini: file state mengalahkan .env."""
    provider = normalize_provider(settings.ai_provider)
    if provider not in MODEL_FIELDS:
        provider = "bangunweb"
    model = default_model(settings, provider)
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        p = normalize_provider(str(data.get("provider", "")))
        m = str(data.get("model", "")).strip()
        if p in MODEL_FIELDS:
            provider = p
            model = m or default_model(settings, provider)
    except (OSError, ValueError):
        pass  # belum ada file / rusak → pakai .env
    return provider, model


def choice_source() -> str:
    """"file" bila pilihan berasal dari data/ai_settings.json, else "env"."""
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return "file" if normalize_provider(str(data.get("provider", ""))) in MODEL_FIELDS else "env"
    except (OSError, ValueError):
        return "env"


def state_mtime() -> Optional[float]:
    try:
        return STATE_PATH.stat().st_mtime
    except OSError:
        return None


def save_choice(provider: str, model: str) -> None:
    provider = normalize_provider(provider)
    if provider not in MODEL_FIELDS:
        raise ValueError(f"Provider tidak dikenal: {provider}")
    model = (model or "").strip()
    if not model:
        raise ValueError("Model tidak boleh kosong.")
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps({"provider": provider, "model": model}, indent=2) + "\n",
        encoding="utf-8",
    )
    log.info("Pilihan AI disimpan: %s:%s → %s", provider, model, STATE_PATH)


def settings_with_choice(settings: Settings) -> Settings:
    """Salinan Settings dengan provider+model sesuai pilihan efektif."""
    provider, model = load_choice(settings)
    return replace(settings, ai_provider=provider, **{MODEL_FIELDS[provider]: model})


def build_provider(settings: Settings) -> Optional[BaseAIProvider]:
    """Bangun provider AI sesuai pilihan efektif (state > .env)."""
    return create_ai_provider(settings_with_choice(settings))
