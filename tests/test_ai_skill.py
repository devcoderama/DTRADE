"""Uji AI SKILL (core/AI_SKILL.md): pengetahuan analisa trading yang
disuntikkan ke prompt sistem semua provider AI mode server."""

import json

from core.ai_client import AI_SKILL_TEXT, SYSTEM_PROMPT, ai_skill_info
from core.ai_providers import BaseAIProvider


def test_skill_file_loaded_into_system_prompt():
    assert AI_SKILL_TEXT, "AI_SKILL.md harus terbaca"
    # Skema JSON dari prompt dasar tetap ada dan TIDAK tertimpa skill.
    assert '"signal":"LONG|SHORT|NEUTRAL"' in SYSTEM_PROMPT
    # Pengetahuan kunci dari skill ikut terbawa.
    for marker in [
        "Kalibrasi confidence",
        "Daftar veto",
        "funding",
        "Multi-timeframe",
        "recent_candles",
        "btc_context",
    ]:
        assert marker.lower() in SYSTEM_PROMPT.lower(), marker


def test_skill_info_reports_loaded():
    info = ai_skill_info()
    assert info["loaded"] is True
    assert info["chars"] == len(AI_SKILL_TEXT)
    assert info["path"].endswith("AI_SKILL.md")


class _FakeProvider(BaseAIProvider):
    name = "fake"
    model = "fake-1"

    def __init__(self):
        self.captured = None

    def is_configured(self) -> bool:
        return True

    def chat(self, messages):
        self.captured = messages
        return json.dumps({
            "signal": "LONG", "confidence": 72,
            "entry": 100.0, "stop_loss": 98.5, "take_profit": 103.7,
            "reasoning": "Tren naik selaras MTF.", "risks": ["funding mulai ramai"],
        }), "fake-1"


def test_provider_sends_skill_in_system_message():
    provider = _FakeProvider()
    result = provider.analyze_market("BTCUSDT", {"price": 100.0, "trend": "UPTREND"})
    assert result.signal == "LONG" and result.confidence == 72
    system = provider.captured[0]
    assert system["role"] == "system"
    assert "PENGETAHUAN ANALISA" in system["content"]
    assert "Kalibrasi confidence" in system["content"]
