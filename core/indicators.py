"""Indikator teknikal murni (tanpa I/O, tanpa jaringan).

Semua fungsi menerima deret angka (list[float]) dan mengembalikan angka.
Karena murni, modul ini mudah diuji dan dipakai ulang oleh strategi,
CLI, maupun Web UI.
"""

from __future__ import annotations

import time
from typing import Optional


def sma(values: list[float], period: int) -> Optional[float]:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def ema_series(values: list[float], period: int) -> list[float]:
    """EMA dengan seed SMA(period) bila data cukup (standar industri).

    Sebelum titik seed, nilai diisi rata-rata berjalan agar panjang seri
    tetap sama dengan data. Data kurang dari period → fallback perilaku
    lama (mulai dari nilai pertama).
    """
    if not values or period <= 0:
        return []
    k = 2.0 / (period + 1)
    out: list[float] = []
    prev: Optional[float] = None
    for i, v in enumerate(values):
        if len(values) >= period and i < period - 1:
            prev = sum(values[: i + 1]) / (i + 1)  # pra-seed: rata-rata berjalan
        elif len(values) >= period and i == period - 1:
            prev = sum(values[:period]) / period  # seed SMA(period)
        else:
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


def adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> Optional[float]:
    """Average Directional Index (Wilder) — kekuatan tren, 0..100.

    Mengembalikan None bila data kurang dari 2× period.
    """
    n = len(closes)
    if n < 2 * period + 1:
        return None
    trs: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))

    def _wilder_smooth(values: list[float]) -> list[float]:
        out = [sum(values[:period])]
        for v in values[period:]:
            out.append(out[-1] - out[-1] / period + v)
        return out

    s_tr = _wilder_smooth(trs)
    s_plus = _wilder_smooth(plus_dm)
    s_minus = _wilder_smooth(minus_dm)
    dxs: list[float] = []
    for tr_v, p_v, m_v in zip(s_tr, s_plus, s_minus):
        if tr_v == 0:
            dxs.append(0.0)
            continue
        plus_di = 100.0 * p_v / tr_v
        minus_di = 100.0 * m_v / tr_v
        denom = plus_di + minus_di
        dxs.append(100.0 * abs(plus_di - minus_di) / denom if denom else 0.0)
    if len(dxs) < period:
        return None
    adx_v = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx_v = (adx_v * (period - 1) + dx) / period
    return round(adx_v, 2)


def vwap(klines: list[list]) -> Optional[float]:
    """Volume Weighted Average Price atas jendela klines yang diberikan."""
    total_pv = 0.0
    total_v = 0.0
    for k in klines:
        high, low, close, vol = float(k[2]), float(k[3]), float(k[4]), float(k[5])
        total_pv += ((high + low + close) / 3.0) * vol
        total_v += vol
    if total_v <= 0:
        return None
    return total_pv / total_v


def classify_regime(snapshot: dict) -> str:
    """Klasifikasi rezim pasar dari snapshot — murni & deterministik.

    Nilai: bullish_trending | bearish_trending | sideways |
    high_volatility | low_volatility | breakout | uncertain.
    Strategi memakai ini sebagai konteks (bukan pemicu order).
    """
    trend = snapshot.get("trend")
    adx_v = snapshot.get("adx14")
    atr_pct = snapshot.get("atr_pct")
    pct_b = snapshot.get("bb_pct_b")
    last_vol = snapshot.get("last_volume")
    avg_vol = snapshot.get("avg_volume")

    if (
        pct_b is not None and (pct_b > 1.0 or pct_b < 0.0)
        and last_vol and avg_vol and avg_vol > 0 and last_vol > 1.5 * avg_vol
    ):
        return "breakout"
    if atr_pct is not None and atr_pct >= 3.0:
        return "high_volatility"
    if adx_v is not None and adx_v >= 25:
        if trend == "UPTREND":
            return "bullish_trending"
        if trend == "DOWNTREND":
            return "bearish_trending"
    if atr_pct is not None and atr_pct <= 0.5 and (adx_v is None or adx_v < 20):
        return "low_volatility"
    if adx_v is not None and adx_v < 20:
        return "sideways"
    return "uncertain"


def pct_change(values: list[float], lookback: int) -> Optional[float]:
    if len(values) <= lookback or values[-lookback - 1] == 0:
        return None
    return (values[-1] / values[-lookback - 1] - 1.0) * 100.0


_INTERVAL_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800,
}


def build_snapshot(
    symbol: str,
    klines: list[list],
    ticker24h: dict,
    funding: dict,
    open_interest: float | None = None,
    interval: str = "15m",
) -> dict:
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
    adx_v = adx(highs, lows, closes)
    vwap_v = vwap(klines)

    trend = "SIDEWAYS"
    if ema20 is not None and ema50 is not None:
        if price > ema20 > ema50:
            trend = "UPTREND"
        elif price < ema20 < ema50:
            trend = "DOWNTREND"

    snapshot = {
        "symbol": symbol,
        "price": price,
        "change_24h_pct": float(ticker24h.get("priceChangePercent", 0) or 0),
        "volume_24h_quote": float(ticker24h.get("quoteVolume", 0) or 0),
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema(closes, 200),
        "adx14": adx_v,
        "vwap": vwap_v,
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
        # Perubahan 1 jam dihitung dari interval AKTUAL: jumlah candle
        # yang mencakup 3600 detik. Interval > 1 jam tidak bisa memberi
        # perubahan intra-jam → None (bukan tebakan).
        "change_1h_pct": (
            pct_change(closes, max(1, 3600 // _INTERVAL_SECONDS[interval]))
            if interval in _INTERVAL_SECONDS and _INTERVAL_SECONDS[interval] <= 3600
            else None
        ),
        # True bila candle terakhir belum tertutup (closeTime > sekarang) —
        # penanda untuk kebijakan candle-tertutup (Fase 3.1).
        "includes_open_candle": bool(
            klines and len(klines[-1]) > 6 and int(klines[-1][6]) > int(time.time() * 1000)
        ),
        "avg_volume": (sum(volumes) / len(volumes)) if volumes else None,
        "last_volume": volumes[-1] if volumes else None,
        "funding_rate": float(funding.get("lastFundingRate", 0) or 0),
        "mark_price": float(funding.get("markPrice", price) or price),
        "open_interest": open_interest,
        "open_interest_usd": (open_interest * price) if open_interest else None,
    }
    snapshot["regime"] = classify_regime(snapshot)
    return snapshot
