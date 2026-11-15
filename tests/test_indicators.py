"""Uji indikator murni: EMA, RSI, ADX, VWAP, dan klasifikasi rezim."""
import math

from core.indicators import adx, build_snapshot, classify_regime, ema, rsi, vwap


def _klines(closes, vol=100.0):
    rows = []
    price = closes[0]
    for i, c in enumerate(closes):
        o = price
        h = max(o, c) * 1.001
        lo = min(o, c) * 0.999
        rows.append([i * 900_000, o, h, lo, c, vol])
        price = c
    return rows


def test_ema_monotonic_series():
    closes = [float(i) for i in range(1, 101)]
    v = ema(closes, 20)
    # EMA tertinggal dari harga terakhir tetapi di atas rata-rata sederhana awal.
    assert v is not None and 50 < v < closes[-1]


def test_rsi_bounds_and_overbought():
    up = [100.0 + i for i in range(60)]
    v = rsi(up)
    assert v is not None and 0 <= v <= 100 and v > 70


def test_adx_strong_trend_high():
    closes = [100.0 + i * 0.8 for i in range(120)]
    rows = _klines(closes)
    highs = [float(k[2]) for k in rows]
    lows = [float(k[3]) for k in rows]
    v = adx(highs, lows, closes)
    assert v is not None and v > 25


def test_adx_insufficient_data():
    assert adx([1, 2], [1, 2], [1, 2]) is None


def test_vwap_between_low_high():
    rows = _klines([100.0, 101.0, 102.0, 101.0, 100.0])
    v = vwap(rows)
    assert v is not None and 99.0 < v < 103.0


def test_snapshot_has_new_fields():
    closes = [100.0 + 10 * math.sin(i / 9.0) + i * 0.05 for i in range(220)]
    snap = build_snapshot("BTCUSDT", _klines(closes), {}, {})
    for key in ("ema200", "adx14", "vwap", "regime"):
        assert key in snap
    assert snap["regime"] in {
        "bullish_trending", "bearish_trending", "sideways",
        "high_volatility", "low_volatility", "breakout", "uncertain",
    }


def test_classify_regime_sideways():
    snap = {"trend": "SIDEWAYS", "adx14": 12.0, "atr_pct": 1.2, "bb_pct_b": 0.5,
            "last_volume": 100.0, "avg_volume": 100.0}
    assert classify_regime(snap) == "sideways"


def test_classify_regime_bullish():
    snap = {"trend": "UPTREND", "adx14": 40.0, "atr_pct": 1.5, "bb_pct_b": 0.8,
            "last_volume": 100.0, "avg_volume": 100.0}
    assert classify_regime(snap) == "bullish_trending"
