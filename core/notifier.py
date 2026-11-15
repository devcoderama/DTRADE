"""Notifikasi Telegram melalui Bot API resmi.

Konfigurasi lewat `.env`:

    TELEGRAM_BOT_TOKEN=123456:ABC-...        (dari @BotFather)
    TELEGRAM_CHAT_ID=123456789               (user id pribadi, diawali angka)
                        atau -1001234567890  (channel / grup)
    TELEGRAM_NOTIFICATIONS=true

Cara mendapatkan chat id:
- User pribadi: chat dengan bot @userinfobot, atau kirim pesan ke bot Anda
  lalu buka https://api.telegram.org/bot<TOKEN>/getUpdates
- Channel: tambahkan bot sebagai admin channel, lalu pakai id channel
  (biasanya diawali -100).

Notifier TIDAK PERNAH melempar exception ke alur trading — kegagalan kirim
hanya dicatat di log, agar eksekusi tidak pernah gagal gara-gara notifikasi.
"""

from __future__ import annotations

import html
from typing import Any, Optional

import requests

from config.settings import Settings
from utils.logger import get_logger

log = get_logger(__name__)

API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    def __init__(self, settings: Settings, timeout: float = 10.0):
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._enabled_flag = settings.telegram_notifications
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._enabled_flag and self._token and self._chat_id)

    # ------------------------------------------------------------------
    def send(self, text: str) -> bool:
        """Kirim satu pesan HTML. Mengembalikan True bila terkirim."""
        if not self.enabled:
            return False
        try:
            resp = requests.post(
                f"{API_BASE}/bot{self._token}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=self._timeout,
            )
            data = resp.json()
            if resp.status_code >= 400 or not data.get("ok", False):
                log.warning("Telegram menolak pesan: %s", str(data)[:300])
                return False
            return True
        except requests.RequestException as exc:
            log.warning("Gagal mengirim notifikasi Telegram: %s", exc)
            return False

    # ------------------------------------------------------------------
    def notify_execution(self, decision: Any, result: Any, reference_price: float) -> bool:
        mode = "🧪 DRY-RUN (simulasi)" if result.dry_run else "🔴 LIVE"
        lines = [
            f"<b>⚡ binance-ai-future — Eksekusi {html.escape(decision.symbol)}</b>",
            f"Mode: {mode}",
            f"Sisi: <b>{html.escape(decision.side)}</b> | Qty: {decision.quantity} | Leverage: {decision.leverage}x",
            f"Referensi harga: {reference_price}",
            f"Notional: {decision.notional:.2f} USDT | Risiko: {decision.risk_amount:.2f} USDT",
            f"SL: {decision.stop_loss} | TP: {decision.take_profit}",
        ]
        for note in getattr(result, "notes", []):
            lines.append(html.escape(str(note)))
        return self.send("\n".join(lines))

    def notify_signal(self, signal: Any) -> bool:
        if signal.side == "NEUTRAL":
            return False
        lines = [
            f"<b>📈 Sinyal {html.escape(signal.symbol)} — {html.escape(signal.side)}</b>",
            f"Harga: {signal.price} | Keyakinan: {signal.confidence}% | Skor teknikal: {signal.technical_score}",
            f"SL: {signal.stop_loss} | TP: {signal.take_profit}",
        ]
        if getattr(signal, "ai", None) is not None:
            lines.append(f"AI: {html.escape(signal.ai.reasoning)}")
        return self.send("\n".join(lines))

    def notify_test(self) -> bool:
        return self.send(
            "✅ <b>binance-ai-future</b> — notifikasi Telegram terhubung. "
            "Pesan ini adalah uji coba dari perintah <code>notify-test</code>."
        )


def build_notifier(settings: Settings) -> TelegramNotifier:
    return TelegramNotifier(settings)
