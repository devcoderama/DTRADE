"""Lapisan multi-provider AI untuk analisis pasar.

Semua provider mengembalikan objek `AIAnalysis` yang sama (dari
`core.ai_client`), sehingga `strategy.py` tidak peduli provider mana
yang aktif. Provider dipilih lewat `.env`:

    AI_PROVIDER=bangunweb | openai | anthropic | deepseek | grok | nous | custom

| Provider   | Base URL bawaan                              | Kunci (.env)            | Model bawaan        |
|------------|----------------------------------------------|-------------------------|---------------------|
| bangunweb  | https://router.bangunweb.com/v1              | BANGUNWEB_API_KEY       | bw-focus            |
| openai     | https://api.openai.com/v1                    | OPENAI_API_KEY          | gpt-4o              |
| anthropic  | https://api.anthropic.com                    | ANTHROPIC_API_KEY       | claude-sonnet-4-5   |
| deepseek   | https://api.deepseek.com/v1                  | DEEPSEEK_API_KEY        | deepseek-chat       |
| grok (xAI) | https://api.x.ai/v1                          | XAI_API_KEY             | grok-4              |
| nous       | https://inference-api.nousresearch.com/v1    | NOUS_API_KEY            | Hermes-4-70B        |
| custom     | AI_CUSTOM_BASE_URL (gateway OpenAI-compatible)| AI_CUSTOM_API_KEY      | AI_CUSTOM_MODEL     |

Semua provider kecuali Anthropic berbicara format OpenAI Chat Completions.
Anthropic memakai Messages API aslinya (header `x-api-key`, system terpisah).
Prompt sistem & skema JSON keputusan terpusat di `core.ai_client` agar
semua provider dinilai dengan standar yang sama.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import requests

from config.settings import Settings
from core.ai_client import (
    SYSTEM_PROMPT,
    AIAnalysis,
    AIClientError,
    BangunWebAIClient,
    _extract_json,
    _to_float,
)
from utils.logger import get_logger

log = get_logger(__name__)


class BaseAIProvider:
    """Antarmuka bersama semua provider."""

    name: str = "base"
    model: str = ""

    def is_configured(self) -> bool:
        raise NotImplementedError

    def chat(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        """Kirim percakapan, kembalikan (teks_balasan, model_yang_menjawab)."""
        raise NotImplementedError

    def analyze_market(self, symbol: str, snapshot: dict[str, Any]) -> AIAnalysis:
        user_prompt = (
            f"Simbol: {symbol}\n"
            f"Snapshot teknikal (JSON):\n{json.dumps(snapshot, ensure_ascii=False, indent=2)}\n\n"
            "Nilai snapshot ini dan kembalikan keputusan JSON sesuai skema."
        )
        content, model_used = self.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )
        return _parse_analysis(symbol, content, model_used, self.name)


def _parse_analysis(symbol: str, content: str, model_used: str, provider: str) -> AIAnalysis:
    parsed = _extract_json(content)
    if parsed is None:
        log.warning("Provider %s mengembalikan JSON tidak valid untuk %s", provider, symbol)
        return AIAnalysis(
            symbol=symbol,
            signal="NEUTRAL",
            confidence=0,
            reasoning="Balasan AI tidak dapat diparse; dianggap NEUTRAL demi keamanan.",
            model_used=f"{provider}:{model_used}",
        )
    signal = str(parsed.get("signal", "NEUTRAL")).upper()
    if signal not in {"LONG", "SHORT", "NEUTRAL"}:
        signal = "NEUTRAL"
    try:
        confidence = max(0, min(100, int(parsed.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0
    return AIAnalysis(
        symbol=symbol,
        signal=signal,
        confidence=confidence,
        entry=_to_float(parsed.get("entry")),
        stop_loss=_to_float(parsed.get("stop_loss")),
        take_profit=_to_float(parsed.get("take_profit")),
        reasoning=str(parsed.get("reasoning", "")),
        risks=[str(r) for r in parsed.get("risks", []) if r],
        model_used=f"{provider}:{model_used}",
        raw=parsed,
    )


# ----------------------------------------------------------------------
class OpenAICompatibleProvider(BaseAIProvider):
    """Provider generik format OpenAI Chat Completions (Bearer token)."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
    ):
        self.name = name
        self.model = model
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._session = requests.Session()

    def is_configured(self) -> bool:
        return bool(self._api_key and self.model)

    def chat(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        if not self.is_configured():
            raise AIClientError(f"Provider {self.name} belum dikonfigurasi di .env")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        resp = self._session.post(
            f"{self._base}/chat/completions", headers=headers, json=payload, timeout=self._timeout
        )
        if resp.status_code == 400:
            # Sebagian gateway menolak response_format — coba lagi tanpanya.
            payload.pop("response_format", None)
            resp = self._session.post(
                f"{self._base}/chat/completions", headers=headers, json=payload, timeout=self._timeout
            )
        if resp.status_code >= 400:
            raise AIClientError(
                f"[{self.name}] POST /chat/completions gagal: HTTP {resp.status_code} {resp.text[:300]}"
            )
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIClientError(f"[{self.name}] Bentuk balasan tidak dikenal: {str(data)[:300]}") from exc
        return content, str(data.get("model", self.model))


# ----------------------------------------------------------------------
class AnthropicProvider(BaseAIProvider):
    """Provider Anthropic asli (Messages API, bukan format OpenAI)."""

    name = "anthropic"

    def __init__(self, settings: Settings, timeout: float = 60.0):
        self.model = settings.anthropic_model
        self._base = settings.anthropic_base_url.rstrip("/")
        self._api_key = settings.anthropic_api_key
        self._timeout = timeout
        self._session = requests.Session()

    def is_configured(self) -> bool:
        return bool(self._api_key and self.model)

    def chat(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        if not self.is_configured():
            raise AIClientError("Provider anthropic belum dikonfigurasi di .env")
        system = ""
        convo: list[dict[str, str]] = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                convo.append({"role": m["role"], "content": m["content"]})
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1024,
            "temperature": 0.2,
            "messages": convo,
        }
        if system:
            payload["system"] = system
        resp = self._session.post(
            f"{self._base}/v1/messages",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            raise AIClientError(
                f"[anthropic] POST /v1/messages gagal: HTTP {resp.status_code} {resp.text[:300]}"
            )
        data = resp.json()
        try:
            text = "".join(
                block.get("text", "") for block in data["content"] if block.get("type") == "text"
            )
        except (KeyError, TypeError) as exc:
            raise AIClientError(f"[anthropic] Bentuk balasan tidak dikenal: {str(data)[:300]}") from exc
        return text, str(data.get("model", self.model))


# ----------------------------------------------------------------------
class BangunWebProvider(BaseAIProvider):
    """Adapter untuk client BangunWeb yang sudah ada (core.ai_client)."""

    name = "bangunweb"

    def __init__(self, settings: Settings):
        self.model = settings.bangunweb_model
        self._settings = settings
        self._client = BangunWebAIClient(settings)

    def is_configured(self) -> bool:
        return self._settings.has_ai_key

    def chat(self, messages: list[dict[str, str]]) -> tuple[str, str]:
        return self._client.chat(messages)

    def analyze_market(self, symbol: str, snapshot: dict[str, Any]) -> AIAnalysis:
        result = self._client.analyze_market(symbol, snapshot)
        if result.model_used and not result.model_used.startswith("bangunweb:"):
            result.model_used = f"bangunweb:{result.model_used}"
        return result

    def account(self) -> dict:
        return self._client.account()


# ----------------------------------------------------------------------
def create_ai_provider(settings: Settings) -> Optional[BaseAIProvider]:
    """Bangun provider sesuai AI_PROVIDER di .env.

    Mengembalikan None bila provider tidak dikenal — aplikasi tetap
    berjalan dengan analisis teknikal murni.
    """
    kind = (settings.ai_provider or "bangunweb").strip().lower()

    if kind == "bangunweb":
        return BangunWebProvider(settings)
    if kind == "openai":
        return OpenAICompatibleProvider(
            "openai", settings.openai_base_url, settings.openai_api_key, settings.openai_model
        )
    if kind == "anthropic":
        return AnthropicProvider(settings)
    if kind == "deepseek":
        return OpenAICompatibleProvider(
            "deepseek", settings.deepseek_base_url, settings.deepseek_api_key, settings.deepseek_model
        )
    if kind in {"grok", "xai"}:
        return OpenAICompatibleProvider(
            "grok", settings.xai_base_url, settings.xai_api_key, settings.xai_model
        )
    if kind in {"nous", "nousportal", "nous-portal"}:
        return OpenAICompatibleProvider(
            "nous", settings.nous_base_url, settings.nous_api_key, settings.nous_model
        )
    if kind == "custom":
        return OpenAICompatibleProvider(
            "custom", settings.ai_custom_base_url, settings.ai_custom_api_key, settings.ai_custom_model
        )

    log.warning("AI_PROVIDER tidak dikenal: %s — analisis AI dinonaktifkan.", kind)
    return None


def describe_providers(settings: Settings) -> list[dict[str, Any]]:
    """Status konfigurasi semua provider (untuk CLI/Web, tanpa secret)."""
    rows = [
        ("bangunweb", settings.bangunweb_model, settings.has_ai_key),
        ("openai", settings.openai_model, bool(settings.openai_api_key)),
        ("anthropic", settings.anthropic_model, bool(settings.anthropic_api_key)),
        ("deepseek", settings.deepseek_model, bool(settings.deepseek_api_key)),
        ("grok", settings.xai_model, bool(settings.xai_api_key)),
        ("nous", settings.nous_model, bool(settings.nous_api_key)),
        ("custom", settings.ai_custom_model, bool(settings.ai_custom_api_key and settings.ai_custom_base_url)),
    ]
    active = (settings.ai_provider or "bangunweb").strip().lower()
    return [
        {"provider": name, "model": model, "configured": cfg, "active": name == active}
        for name, model, cfg in rows
    ]
