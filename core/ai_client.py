"""Client untuk AI Router BangunWeb (OpenAI-compatible).

Dokumentasi resmi: https://bangunweb.com/ai/docs

- Base URL : https://router.bangunweb.com/v1
- Auth     : header `Authorization: Bearer bwr_live_...`
- Chat     : POST /v1/chat/completions  (mendukung response_format JSON)
- Akun     : GET  /v1/account           (sisa token & pemakaian)
- Model    : GET  /v1/models            (mode bw-swift/bw-focus/bw-pro/bw-apex)
- Health   : GET  /health               (tanpa kunci)

Modul ini hanya menerima SNAPSHOT ANGKA (harga + indikator) dan
mengembalikan penilaian terstruktur. Ia tidak pernah menerima kredensial
Binance dan tidak bisa menempatkan order.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from config.settings import Settings
from utils.logger import get_logger

log = get_logger(__name__)


class AIClientError(Exception):
    pass


def is_retryable_status(status: int) -> bool:
    """Kegagalan sementara yang layak dicoba lagi: rate limit & error server."""
    return status == 429 or status >= 500


def post_with_retry(
    session: requests.Session,
    url: str,
    *,
    provider: str,
    max_retries: int = 2,
    backoff: tuple = (1.0, 3.0),
    **kwargs,
):
    """POST dengan retry terbatas untuk kegagalan SEMENTARA saja.

    - Timeout/koneksi putus dan HTTP 429/5xx → dicoba lagi (maks
      `max_retries` kali) dengan jeda bertingkat.
    - HTTP 4xx lain (auth, request buruk) → langsung dikembalikan agar
      pemanggil menanganinya; mengulanginya hanya membuang waktu & token.
    - Bila semua percobaan gagal di level jaringan, RequestException
      terakhir dilempar kembali — tidak pernah disembunyikan.
    """
    attempts = max(0, max_retries) + 1
    for attempt in range(attempts):
        try:
            resp = session.post(url, **kwargs)
        except requests.RequestException as exc:
            if attempt >= attempts - 1:
                raise
            delay = backoff[min(attempt, len(backoff) - 1)]
            log.warning(
                "[%s] POST %s gagal jaringan (%s) — percobaan %d/%d dalam %.0f dtk…",
                provider, url.rsplit("/", 1)[-1], exc, attempt + 2, attempts, delay,
            )
            time.sleep(delay)
            continue
        if is_retryable_status(resp.status_code) and attempt < attempts - 1:
            delay = backoff[min(attempt, len(backoff) - 1)]
            log.warning(
                "[%s] POST %s → HTTP %d — percobaan %d/%d dalam %.0f dtk…",
                provider, url.rsplit("/", 1)[-1], resp.status_code, attempt + 2, attempts, delay,
            )
            time.sleep(delay)
            continue
        return resp
    raise AIClientError(f"[{provider}] POST {url} gagal tanpa respons")  # tak tercapai


@dataclass
class AIAnalysis:
    symbol: str
    signal: str  # LONG | SHORT | NEUTRAL
    confidence: int  # 0..100
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasoning: str = ""
    risks: list[str] = field(default_factory=list)
    model_used: str = ""
    raw: dict = field(default_factory=dict)


SYSTEM_PROMPT = """Anda adalah analis kuantitatif senior untuk pasar Binance USDⓈ-M Futures.
Anda menerima snapshot teknikal satu simbol (harga, perubahan 24 jam, indikator EMA/RSI/MACD/
Bollinger/ATR, funding rate). Tugas Anda: menilai snapshot itu secara disiplin dan skeptis.

Aturan:
- Jawab HANYA dengan JSON valid tanpa teks lain, skema:
  {"signal":"LONG|SHORT|NEUTRAL","confidence":0-100,"entry":number|null,
   "stop_loss":number|null,"take_profit":number|null,
   "reasoning":"maks 3 kalimat, bahasa Indonesia","risks":["...","..."]}
- "confidence" mencerminkan kualitas setup, bukan keberanian. Setup campuran = NEUTRAL.
- stop_loss dan take_profit harus konsisten dengan ATR yang diberikan dan sisi sinyal.
- Untuk NEUTRAL, entry/stop_loss/take_profit boleh null.
- Jangan menjanjikan profit. Sebutkan risiko utama secara eksplisit."""


class BangunWebAIClient:
    def __init__(self, settings: Settings, timeout: float = 60.0):
        self._settings = settings
        self._base = settings.bangunweb_base_url
        self._timeout = timeout
        self._session = requests.Session()

    def _headers(self) -> dict[str, str]:
        if not self._settings.has_ai_key:
            raise AIClientError("BANGUNWEB_API_KEY belum diisi di .env")
        return {
            "Authorization": f"Bearer {self._settings.bangunweb_api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    def health(self) -> bool:
        try:
            resp = self._session.get(
                self._base.replace("/v1", "") + "/health", timeout=10
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def account(self) -> dict:
        resp = self._session.get(
            f"{self._base}/account", headers=self._headers(), timeout=self._timeout
        )
        if resp.status_code >= 400:
            raise AIClientError(f"GET /account gagal: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()

    def list_models(self) -> list[dict]:
        resp = self._session.get(
            f"{self._base}/models", headers=self._headers(), timeout=self._timeout
        )
        if resp.status_code >= 400:
            raise AIClientError(f"GET /models gagal: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json().get("data", [])

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.2,
        json_mode: bool = True,
    ) -> tuple[str, str]:
        """Kirim chat completion. Mengembalikan (isi_teks, model_yang_menjawab)."""
        payload: dict[str, Any] = {
            "model": model or self._settings.bangunweb_model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        resp = post_with_retry(
            self._session,
            f"{self._base}/chat/completions",
            provider="bangunweb",
            headers=self._headers(),
            json=payload,
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            raise AIClientError(
                f"POST /chat/completions gagal: HTTP {resp.status_code} {resp.text[:300]}"
            )
        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIClientError(f"Bentuk balasan AI tidak dikenal: {str(data)[:300]}") from exc
        return content, str(data.get("model", payload["model"]))

    # ------------------------------------------------------------------
    def analyze_market(self, symbol: str, snapshot: dict[str, Any]) -> AIAnalysis:
        """Minta penilaian AI atas snapshot pasar satu simbol."""
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
        parsed = _extract_json(content)
        if parsed is None:
            log.warning("AI mengembalikan JSON tidak valid untuk %s: %s", symbol, content[:200])
            return AIAnalysis(
                symbol=symbol,
                signal="NEUTRAL",
                confidence=0,
                reasoning="Balasan AI tidak dapat diparse; dianggap NEUTRAL demi keamanan.",
                model_used=model_used,
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
            model_used=model_used,
            raw=parsed,
        )


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extract_json(text: str) -> Optional[dict]:
    """Ambil objek JSON dari teks (menoleransi code fence / teks tambahan)."""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group(0))
            return obj if isinstance(obj, dict) else None
        except ValueError:
            return None
    return None
