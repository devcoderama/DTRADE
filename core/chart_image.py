"""Render grafik candlestick menjadi gambar PNG untuk siaran Telegram.

Menggambar dengan matplotlib (backend Agg, tanpa layar): candle + EMA20/50,
volume, panel RSI, serta garis harga terakhir, SL, dan TP — gaya gelap
khas Binance agar familier dibaca di channel.

Fungsi utama: `render_signal_chart(...) -> bytes (PNG)`.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Optional

import matplotlib

# Redam INFO font_manager (keluhan font sistem yang tidak terbaca saat
# cache font pertama dibangun) agar log start server tetap bersih.
logging.getLogger("matplotlib.font_manager").setLevel(logging.WARNING)

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from core.indicators import ema_series  # noqa: E402

_BG = "#0b0e11"
_PANEL = "#161a1e"
_LINE = "#2b3139"
_TEXT = "#eaecef"
_MUTED = "#848e9c"
_UP = "#0ecb81"
_DOWN = "#f6465d"
_GOLD = "#f0b90b"
_BLUE = "#4dabf7"
_PURPLE = "#a371f7"


def _rsi_series(closes: list[float], period: int = 14) -> list[Optional[float]]:
    """RSI (Wilder) sebagai deret; None untuk candle sebelum data cukup."""
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))

    def _val(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out[period] = _val(avg_gain, avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = _val(avg_gain, avg_loss)
    return out


def _fmt_price(v: float) -> str:
    a = abs(v)
    d = 2 if a >= 1000 else 4 if a >= 1 else 6 if a >= 0.01 else 8
    return f"{v:,.{d}f}"


def render_signal_chart(
    symbol: str,
    klines: list[list],
    *,
    side: str,
    stop_loss: Optional[float],
    take_profit: Optional[float],
    interval: str = "15m",
    network_label: str = "",
) -> bytes:
    """Gambar PNG (bytes) grafik `symbol` dengan anotasi sinyal."""
    if not klines:
        raise ValueError("klines kosong — tidak bisa menggambar grafik")

    times = [int(k[0]) for k in klines]
    opens = [float(k[1]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]
    n = len(closes)
    xs = list(range(n))
    up = [c >= o for o, c in zip(opens, closes)]
    colors = [_UP if u else _DOWN for u in up]

    ema20 = ema_series(closes, 20)
    ema50 = ema_series(closes, 50)
    rsi_vals = _rsi_series(closes)

    fig = plt.figure(figsize=(12, 7.6), dpi=110)
    fig.patch.set_facecolor(_BG)
    grid = fig.add_gridspec(3, 1, height_ratios=[3.3, 0.9, 0.9], hspace=0.07,
                            left=0.065, right=0.925, top=0.90, bottom=0.07)
    ax = fig.add_subplot(grid[0])
    ax_vol = fig.add_subplot(grid[1], sharex=ax)
    ax_rsi = fig.add_subplot(grid[2], sharex=ax)

    for a in (ax, ax_vol, ax_rsi):
        a.set_facecolor(_PANEL)
        for spine in a.spines.values():
            spine.set_color(_LINE)
        a.tick_params(colors=_MUTED, labelsize=8.5)
        a.grid(axis="y", color="#ffffff", alpha=0.05, linewidth=0.7)
        a.set_axisbelow(True)

    # --- panel harga: wick + body ---
    ax.vlines(xs, lows, highs, color=colors, linewidth=0.9)
    bottoms = [min(o, c) for o, c in zip(opens, closes)]
    heights = [max(abs(c - o), (max(highs) - min(lows)) * 0.0008 or 1e-9) for o, c in zip(opens, closes)]
    ax.bar(xs, heights, bottom=bottoms, width=0.62, color=colors, linewidth=0)
    ax.plot(xs, ema20, color=_GOLD, linewidth=1.2, label="EMA20")
    ax.plot(xs, ema50, color=_BLUE, linewidth=1.2, label="EMA50")
    ax.legend(loc="upper left", fontsize=8.5, frameon=False, labelcolor=_MUTED, ncols=2)

    last = closes[-1]
    ax.axhline(last, color=_MUTED, linewidth=0.8, linestyle=(0, (4, 4)), alpha=0.7)
    ax.text(n + 1.5, last, _fmt_price(last), color=_TEXT, fontsize=9, va="center", fontweight="bold")
    if stop_loss:
        ax.axhline(stop_loss, color=_DOWN, linewidth=1.1, linestyle="--", alpha=0.9)
        ax.text(n + 1.5, stop_loss, f"SL {_fmt_price(stop_loss)}", color=_DOWN, fontsize=9, va="center", fontweight="bold")
    if take_profit:
        ax.axhline(take_profit, color=_UP, linewidth=1.1, linestyle="--", alpha=0.9)
        ax.text(n + 1.5, take_profit, f"TP {_fmt_price(take_profit)}", color=_UP, fontsize=9, va="center", fontweight="bold")
    ax.set_xlim(-1, n + 14)

    side_color = _UP if side == "LONG" else _DOWN if side == "SHORT" else _MUTED
    fig.text(0.065, 0.955, f"{symbol} · {interval}", color=_TEXT, fontsize=16, fontweight="bold", va="center")
    fig.text(0.30, 0.955, f"SINYAL {side}", color=side_color, fontsize=16, fontweight="bold", va="center")
    fig.text(0.935, 0.955, f"binance-ai-future{(' · ' + network_label) if network_label else ''}",
             color=_MUTED, fontsize=10, va="center", ha="right")
    ax.tick_params(labelbottom=False)

    # --- panel volume ---
    ax_vol.bar(xs, volumes, width=0.62, color=colors, alpha=0.55, linewidth=0)

    def _vol_fmt(v, _pos):
        if v >= 1e9:
            return f"{v / 1e9:.1f}B"
        if v >= 1e6:
            return f"{v / 1e6:.0f}M"
        if v >= 1e3:
            return f"{v / 1e3:.0f}K"
        return f"{v:.0f}"

    ax_vol.yaxis.set_major_formatter(FuncFormatter(_vol_fmt))
    ax_vol.set_ylabel("Vol", color=_MUTED, fontsize=8.5)
    ax_vol.tick_params(labelbottom=False)
    ax_vol.set_ylim(0, max(volumes) * 1.15 if volumes else 1)

    # --- panel RSI ---
    rsi_x = [x for x, v in zip(xs, rsi_vals) if v is not None]
    rsi_y = [v for v in rsi_vals if v is not None]
    ax_rsi.plot(rsi_x, rsi_y, color=_PURPLE, linewidth=1.2)
    ax_rsi.axhline(70, color=_MUTED, linewidth=0.8, linestyle="--", alpha=0.6)
    ax_rsi.axhline(30, color=_MUTED, linewidth=0.8, linestyle="--", alpha=0.6)
    ax_rsi.axhspan(30, 70, color="#ffffff", alpha=0.03)
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_yticks([30, 50, 70])
    ax_rsi.set_ylabel("RSI", color=_MUTED, fontsize=8.5)
    if rsi_y:
        ax_rsi.text(n + 1.5, rsi_y[-1], f"{rsi_y[-1]:.0f}", color=_PURPLE, fontsize=9, va="center", fontweight="bold")

    def _time_label(x, _pos):
        i = int(round(x))
        if 0 <= i < n:
            return datetime.fromtimestamp(times[i] / 1000, tz=timezone.utc).strftime("%d %b %H:%M")
        return ""

    ax_rsi.xaxis.set_major_formatter(FuncFormatter(_time_label))
    for label in ax_rsi.get_xticklabels():
        label.set_color(_MUTED)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=_BG)
    plt.close(fig)
    return buf.getvalue()
