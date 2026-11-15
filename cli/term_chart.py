"""Grafik candlestick terminal berbasis plotext (dependensi opsional).

Merender candle + EMA20/EMA50 + volume menjadi string ANSI yang bisa
dicetak lewat Rich (``Text.from_ansi``) atau ditampilkan di widget
Textual. plotext yang dipakai adalah seri 5.x (API klasik: candlestick
+ build) — seri 6 mengubah API-nya secara total.

Contoh:
    from cli.term_chart import render_chart
    ansi = render_chart(binance.klines("BTCUSDT", "1h", 120), width=100)
"""

from __future__ import annotations

from datetime import datetime


def _ema(values: list[float], period: int) -> list[float]:
    k = 2.0 / (period + 1)
    out: list[float] = []
    prev: float | None = None
    for v in values:
        prev = v if prev is None else (v - prev) * k + prev
        out.append(prev)
    return out


def render_chart(
    klines: list[list],
    width: int = 100,
    height: int = 28,
    ema_fast: int = 20,
    ema_slow: int = 50,
) -> str:
    """Render klines mentah Binance menjadi grafik candlestick ANSI.

    klines: baris [open_time, open, high, low, close, volume, ...].
    Lebar grafik mengikuti `width` (satu candle ≈ satu kolom); tinggi
    dibagi ±80% harga dan ±20% volume.
    """
    import shutil

    import plotext as plt

    # plotext membatasi tinggi figur pada tinggi terminal; sesuaikan
    # agar panel volume tidak hilang di terminal yang pendek.
    term = shutil.get_terminal_size((120, 40))
    width = max(40, min(width, term.columns))
    height = max(14, min(height, term.lines - 1))

    rows = klines[-max(20, width):]
    opens = [float(r[1]) for r in rows]
    highs = [float(r[2]) for r in rows]
    lows = [float(r[3]) for r in rows]
    closes = [float(r[4]) for r in rows]
    vols = [float(r[5]) for r in rows]
    times = [datetime.fromtimestamp(int(r[0]) / 1000).strftime("%H:%M") for r in rows]
    x = list(range(len(rows)))

    vol_h = max(4, int(height * 0.2))
    price_h = max(10, height - vol_h)

    plt.clear_figure()
    plt.theme("dark")
    plt.subplots(2, 1)

    # --- panel harga: candlestick + dua garis EMA ---
    plt.subplot(1, 1)
    plt.candlestick(x, {"Open": opens, "Close": closes, "High": highs, "Low": lows})
    plt.plot(x, _ema(closes, ema_fast), color="orange", label=f"EMA{ema_fast}")
    plt.plot(x, _ema(closes, ema_slow), color="gray", label=f"EMA{ema_slow}")
    try:
        plt.xticks([], [])
    except Exception:
        pass
    plt.plotsize(width, price_h)

    # --- panel volume: batang hijau/merah mengikuti arah candle ---
    plt.subplot(2, 1)
    plt.bar(x, vols, color=["green" if c >= o else "red" for o, c in zip(opens, closes)])
    if vols:
        plt.ylim(0, max(vols) * 1.15)
    step = max(1, len(rows) // 5)
    tick_pos = list(range(0, len(rows), step))
    try:
        plt.xticks(tick_pos, [times[i] for i in tick_pos])
    except Exception:
        pass
    plt.plotsize(width, vol_h)

    return plt.build()


def chart_available() -> bool:
    try:
        import plotext  # noqa: F401

        return True
    except Exception:
        return False
