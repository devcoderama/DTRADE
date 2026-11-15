"""Siaran sinyal ke Telegram: pindai → saring → AI → gambar → kirim.

Alur satu putaran (`run_broadcast`):

1. Pindai semua simbol (skor teknikal, cepat — lihat SignalEngine.scan).
2. Saring kandidat "sinyal bagus": |skor teknikal| >= min_score.
3. Kandidat teratas dianalisis penuh dengan AI; hanya yang lolos
   penggabungan sebagai LONG/SHORT (bukan NEUTRAL) yang disiarkan.
4. Untuk tiap sinyal lolos: gambar grafik candlestick + indikator
   (core.chart_image) lalu kirim ke Telegram sebagai foto + caption.

Mode loop (CLI `broadcast --loop`) memakai cache jeda (cooldown) agar
simbol+sisi yang sama tidak disiarkan berulang dalam waktu singkat.
"""

from __future__ import annotations

import html
import time
from typing import Optional

from config.settings import Settings
from core.binance_client import BinanceFuturesClient
from core.chart_image import render_signal_chart
from core.notifier import TelegramNotifier
from core.strategy import SignalEngine, TradeSignal
from utils.formatting import fmt_price
from utils.logger import get_logger

log = get_logger(__name__)


def format_caption(settings: Settings, signal: TradeSignal) -> str:
    """Caption foto Telegram (HTML). Dibatasi ±1000 karakter oleh pemanggil."""
    snap = signal.snapshot or {}
    icon = "🟢" if signal.side == "LONG" else "🔴"
    network = "TESTNET" if settings.binance_testnet else "MAINNET"
    mode = "DRY-RUN (simulasi)" if settings.trading_mode == "dry_run" else "LIVE"
    lines = [
        f"<b>{icon} SINYAL {signal.side} — {html.escape(signal.symbol)}</b>",
        f"Harga: <b>{fmt_price(signal.price)}</b>",
        f"Keyakinan gabungan: <b>{signal.confidence}%</b> · Skor teknikal: {signal.technical_score:+d}",
        f"SL: {fmt_price(signal.stop_loss) if signal.stop_loss else '—'} · "
        f"TP: {fmt_price(signal.take_profit) if signal.take_profit else '—'}",
        f"RSI 14: {snap.get('rsi14', 0) or 0:.1f} · Tren: {snap.get('trend', '—')} · "
        f"Funding: {(snap.get('funding_rate', 0) or 0) * 100:.3f}%",
    ]
    reasons = [r for r in signal.reasons if not r.startswith("Level SL/TP")][:4]
    if reasons:
        lines.append("")
        lines.append("<b>Alasan:</b>")
        for r in reasons:
            lines.append("• " + html.escape(r[:130]))
    if signal.ai is not None and signal.ai.reasoning:
        lines.append("")
        lines.append("<i>" + html.escape(signal.ai.reasoning[:280]) + "</i>")
    lines.append("")
    lines.append(f"Mode: {network} · {mode} — bukan saran finansial.")
    return "\n".join(lines)


def run_broadcast(
    settings: Settings,
    engine: SignalEngine,
    binance: BinanceFuturesClient,
    notifier: TelegramNotifier,
    symbols: list[str],
    *,
    min_score: int = 50,
    max_signals: int = 3,
    sent_cache: Optional[dict[str, float]] = None,
    cooldown_sec: int = 4 * 3600,
) -> dict:
    """Satu putaran siaran sinyal. Mengembalikan ringkasan hasil (dict)."""
    staged = engine.scan(symbols, use_ai=False)
    candidates = [s for s in staged if abs(s.technical_score) >= min_score]
    details: list[dict] = []
    sent: list[str] = []

    # Beri ruang 2x lipat: sebagian kandidat akan ditolak AI.
    for cand in candidates[: max_signals * 2]:
        if len(sent) >= max_signals:
            break
        key = f"{cand.symbol}:{cand.side}"
        if sent_cache is not None and time.time() - sent_cache.get(key, 0) < cooldown_sec:
            details.append({"symbol": cand.symbol, "status": "cooldown"})
            continue
        final = engine.analyze(cand.symbol, use_ai=True)
        if final.side == "NEUTRAL":
            details.append({"symbol": cand.symbol, "status": "ditolak AI / NEUTRAL",
                            "technical_score": cand.technical_score})
            continue
        klines = binance.klines(
            final.symbol, interval=settings.default_interval, limit=120
        )
        png = render_signal_chart(
            final.symbol,
            klines,
            side=final.side,
            stop_loss=final.stop_loss,
            take_profit=final.take_profit,
            interval=settings.default_interval,
            network_label="TESTNET" if settings.binance_testnet else "MAINNET",
        )
        ok = notifier.send_photo(png, format_caption(settings, final)) if notifier.enabled else False
        details.append({
            "symbol": final.symbol,
            "side": final.side,
            "confidence": final.confidence,
            "technical_score": final.technical_score,
            "status": "terkirim" if ok else ("telegram nonaktif" if not notifier.enabled else "gagal kirim"),
        })
        if ok:
            sent.append(final.symbol)
            if sent_cache is not None:
                sent_cache[key] = time.time()

    summary = {
        "telegram_enabled": notifier.enabled,
        "min_score": min_score,
        "candidates": len(candidates),
        "sent": sent,
        "details": details,
    }
    log.info("Siaran sinyal: %s", summary)
    return summary
