"""Indikator teknikal murni (tanpa I/O, tanpa jaringan).

Semua fungsi menerima deret angka (list[float]) dan mengembalikan angka.
Karena murni, modul ini mudah diuji dan dipakai ulang oleh strategi,
CLI, maupun Web UI.
"""

from __future__ import annotations

from typing import Optional


def sma(values: list[float], period: int) -> Optional[float]:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def ema_series(values: list[float], period: int) -> list[float]:
    if not values or period <= 0:
        return []
    k = 2.0 / (period + 1)
    out: list[float] = []
    prev: Optional[float] = None
    for v in values:
        prev = v if prev is None else (v - prev) * k + prev
        out.append(prev)
    return out


def ema(values: list[float], period: int) -> Optional[float]:
    series = ema_series(values, period)
    return series[-1] if series else None


def rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """Relative Strength Index (Wilder)."""
    if len(closes) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal_period: int = 9
) -> dict[str, Optional[float]]:
    if len(closes) < slow + signal_period:
        return {"macd": None, "signal": None, "histogram": None}
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema_series(macd_line, signal_period)
    macd_v = macd_line[-1]
    signal_v = signal_line[-1] if signal_line else None
    hist = (macd_v - signal_v) if (macd_v is not None and signal_v is not None) else None
    return {"macd": macd_v, "signal": signal_v, "histogram": hist}


def bollinger_bands(
    closes: list[float], period: int = 20, mult: float = 2.0
) -> dict[str, Optional[float]]:
    if len(closes) < period:
        return {"upper": None, "middle": None, "lower": None, "pct_b": None}
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period
    std = variance ** 0.5
    upper = mid + mult * std
    lower = mid - mult * std
    pct_b = (closes[-1] - lower) / (upper - lower) if upper != lower else 0.5
    return {"upper": upper, "middle": mid, "lower": lower, "pct_b": pct_b}


def atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> Optional[float]:
    """Average True Range (Wilder)."""
    if len(closes) <= period:
        return None
    trs: list[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    value = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        value = (value * (period - 1) + trs[i]) / period
    return value


def pct_change(values: list[float], lookback: int) -> Optional[float]:
    if len(values) <= lookback or values[-lookback - 1] == 0:
        return None
    return (values[-1] / values[-lookback - 1] - 1.0) * 100.0


def build_snapshot(symbol: str, klines: list[list], ticker24h: dict, funding: dict) -> dict:
    """Ubah klines mentah Binance menjadi snapshot indikator yang ringkas.

    Snapshot inilah yang dinilai strategi lokal DAN dikirim ke AI —
    satu representasi yang sama untuk kedua penilai.
    """
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    price = closes[-1] if closes else 0.0
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    macd_v = macd(closes)
    bb = bollinger_bands(closes)
    atr_v = atr(highs, lows, closes)

    trend = "SIDEWAYS"
    if ema20 is not None and ema50 is not None:
        if price > ema20 > ema50:
            trend = "UPTREND"
        elif price < ema20 < ema50:
            trend = "DOWNTREND"

    return {
        "symbol": symbol,
        "price": price,
        "change_24h_pct": float(ticker24h.get("priceChangePercent", 0) or 0),
        "volume_24h_quote": float(ticker24h.get("quoteVolume", 0) or 0),
        "ema20": ema20,
        "ema50": ema50,
        "trend": trend,
        "rsi14": rsi(closes),
        "macd": macd_v["macd"],
        "macd_signal": macd_v["signal"],
        "macd_histogram": macd_v["histogram"],
        "bb_upper": bb["upper"],
        "bb_middle": bb["middle"],
        "bb_lower": bb["lower"],
        "bb_pct_b": bb["pct_b"],
        "atr14": atr_v,
        "atr_pct": (atr_v / price * 100.0) if (atr_v and price) else None,
        "change_1h_pct": pct_change(closes, 4),   # asumsi interval 15m: 4 candle = 1 jam
        "avg_volume": (sum(volumes) / len(volumes)) if volumes else None,
        "last_volume": volumes[-1] if volumes else None,
        "funding_rate": float(funding.get("lastFundingRate", 0) or 0),
        "mark_price": float(funding.get("markPrice", price) or price),
    }
