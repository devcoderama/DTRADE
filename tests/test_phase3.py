"""Uji Fase 3: candle tertutup, gerbang keras, skor v2, peran AI,
validasi level AI vs ATR, risiko portofolio, PositionManager."""
import time

import pytest

from config.settings import Settings
from core.ai_client import AIAnalysis
from core.journal import TradeJournal
from core.position_manager import PositionManager
from core.risk import RiskManager, RiskDecision  # noqa: F401  (impor memastikan modul sehat)
from core.strategy import SignalEngine, technical_score, technical_score_v1
from core.symbols import SymbolManager


# ----------------------------------------------------------------------
# Snapshot & klien palsu bersama
# ----------------------------------------------------------------------
def _snap(**kw):
    base = {
        "symbol": "BTCUSDT", "price": 100.0, "trend": "UPTREND", "rsi14": 55.0,
        "macd": 1.0, "macd_histogram": 0.5, "bb_pct_b": 0.5, "change_1h_pct": 0.2,
        "funding_rate": 0.0, "atr14": 2.0, "adx14": 30.0, "regime": "bullish_trending",
        "volume_24h_quote": 50_000_000.0, "last_volume": 10.0, "avg_volume": 10.0,
    }
    base.update(kw)
    return base


class _FakeBinance:
    def __init__(self, klines=None):
        self._klines = klines

    def klines(self, symbol, interval="15m", limit=200):
        return self._klines

    def ticker_24h(self, symbol):
        return {"lastPrice": "100", "quoteVolume": "1000000"}

    def funding_rate(self, symbol):
        return {"lastFundingRate": "0", "markPrice": "101"}

    def open_interest(self, symbol):
        return 1.0


def _klines(n=120, open_last=False):
    now = int(time.time() * 1000)
    rows = []
    for i in range(n):
        close_t = now - (n - i) * 900_000 - 1
        if open_last and i == n - 1:
            close_t = now + 900_000
        rows.append([close_t - 900_000, "100", "101", "99", "100", "10", close_t])
    return rows


# ----------------------------------------------------------------------
# 3.1 — candle tertutup
# ----------------------------------------------------------------------
def test_closed_candles_drop_open_one():
    eng = SignalEngine(Settings(), _FakeBinance(_klines(open_last=True)))
    used, dropped = eng._closed_only(_klines(open_last=True))
    assert dropped and len(used) == 119
    snap = eng.snapshot_for("BTCUSDT")
    assert snap["includes_open_candle"] is False
    # Harga acuan diganti mark price (101), bukan close candle tertutup.
    assert snap["price"] == 101.0


def test_closed_candles_can_be_disabled():
    eng = SignalEngine(Settings(use_closed_candles_only=False), _FakeBinance(_klines(open_last=True)))
    _, dropped = eng._closed_only(_klines(open_last=True))
    assert dropped is False


# ----------------------------------------------------------------------
# 3.2 — gerbang keras
# ----------------------------------------------------------------------
def test_gate_mtf_opposition_blocks():
    eng = SignalEngine(Settings(), None)
    mtf = {"1h": {"trend": "DOWNTREND", "regime": "bearish_trending"}}
    blocks = eng._gate_blocks(_snap(), "LONG", mtf)
    assert blocks and "MTF" in blocks[0]
    assert eng._gate_blocks(_snap(), "SHORT", mtf) == []


def test_gate_adx_and_regime():
    eng = SignalEngine(Settings(), None)
    assert eng._gate_blocks(_snap(adx14=12.0), "LONG", None)  # trending + ADX lemah
    assert eng._gate_blocks(_snap(regime="high_volatility", adx14=None), "LONG", None)
    assert eng._gate_blocks(_snap(regime="sideways", adx14=5.0), "LONG", None) == []  # ADX hanya utk trending
    assert eng._gate_blocks(_snap(), "LONG", None) == []


# ----------------------------------------------------------------------
# 3.3 — skor v2
# ----------------------------------------------------------------------
def test_scoring_v2_categories_and_agreement():
    # Trend kuat saja (positioning & MR nol) → dibatasi < ambang trade.
    score, reasons = technical_score(_snap(rsi14=50.0, funding_rate=0.0, regime="bullish_trending"), version=2)
    assert score <= 24
    # Trend + positioning sepakat (funding sangat negatif → +) → lolos penuh.
    snap2 = _snap(rsi14=50.0, funding_rate=-0.002, regime="bullish_trending")
    score2, _ = technical_score(snap2, version=2)
    assert score2 > 24
    # v1 tidak berubah perilakunya.
    assert technical_score(_snap(), version=1) == technical_score_v1(_snap())


# ----------------------------------------------------------------------
# 3.4 — AI tidak boleh memulai; level AI divalidasi vs ATR
# ----------------------------------------------------------------------
class _FakeAI:
    def __init__(self, analysis):
        self._analysis = analysis

    def is_configured(self):
        return True

    def analyze_market(self, symbol, snapshot):
        return self._analysis


def _engine_with_ai(ai, **settings_kw):
    eng = SignalEngine(Settings(**settings_kw), _FakeBinance(_klines()), ai=ai)
    eng._multi_timeframe = lambda symbol, primary: {}
    return eng


def test_ai_cannot_initiate_by_default():
    # Skor teknikal dibuat netral (sideways, tanpa komponen) lewat stub snapshot.
    ai = _FakeAI(AIAnalysis(symbol="BTCUSDT", signal="LONG", confidence=90,
                            reasoning="yakin", model_used="fake:x"))
    eng = _engine_with_ai(ai)
    eng.snapshot_for = lambda symbol, interval=None: _snap(
        trend=None, macd=None, macd_histogram=None, rsi14=50.0, change_1h_pct=0.0,
        regime="uncertain")
    sig = eng._analyze_fresh("BTCUSDT")
    assert sig.side == "NEUTRAL"
    assert any("AI_CAN_INITIATE=false" in r for r in sig.reasons)


def test_ai_levels_outside_atr_range_replaced():
    ai = _FakeAI(AIAnalysis(symbol="BTCUSDT", signal="LONG", confidence=90,
                            stop_loss=99.9, take_profit=199.0,  # SL 0,05×ATR, TP 49×ATR
                            reasoning="yakin", model_used="fake:x"))
    eng = _engine_with_ai(ai)
    eng.snapshot_for = lambda symbol, interval=None: _snap()
    sig = eng._analyze_fresh("BTCUSDT")
    assert sig.side == "LONG"
    # Diganti level ATR: SL = 100 − 1,5×2 = 97; TP = 100 + 2,5×2 = 105.
    assert sig.stop_loss == pytest.approx(97.0)
    assert sig.take_profit == pytest.approx(105.0)


# ----------------------------------------------------------------------
# 3.6 — risiko portofolio
# ----------------------------------------------------------------------
class _FakeBinanceRisk:
    def __init__(self, positions=None, algos=None):
        self._positions = positions or []
        self._algos = algos or {}

    def account(self):
        return {"totalWalletBalance": "10000", "totalUnrealizedProfit": "0"}

    def positions(self, symbol=None):
        return list(self._positions)

    def open_algo_orders(self, symbol=None):
        return list(self._algos.get(symbol, []))

    def klines(self, symbol, interval="15m", limit=200):
        return _klines()


class _FakeSymbols:
    def is_tradable(self, symbol):
        return True

    def filters(self, symbol):
        from core.symbols import SymbolFilters

        return SymbolFilters(symbol=symbol, quantity_step=0.001, quantity_min=0.001,
                             quantity_max=1e9, price_tick=0.1, min_notional=5.0)


def _signal(side="LONG", symbol="ETHUSDT", **snap_kw):
    from core.strategy import TradeSignal

    snap = _snap(**snap_kw)
    return TradeSignal(symbol=symbol, side=side, confidence=80, price=100.0,
                       stop_loss=95.0 if side == "LONG" else 105.0,
                       take_profit=110.0 if side == "LONG" else 90.0,
                       technical_score=60, snapshot=snap)


def test_portfolio_same_direction_cap():
    positions = [
        {"symbol": "AAAUSDT", "positionAmt": "1", "entryPrice": "10"},
        {"symbol": "BBBUSDT", "positionAmt": "2", "entryPrice": "10"},
    ]
    rm = RiskManager(Settings(max_same_direction_positions=2, max_open_positions=10),
                     _FakeBinanceRisk(positions), _FakeSymbols())
    decision = rm.evaluate(_signal())
    assert decision.allowed is False
    assert any("searah" in r for r in decision.reasons)


def test_portfolio_open_risk_cap():
    # Posisi BTC: risiko |100 − 90| × 10 = 100 USDT; batas terbuka = 2 × 1% × 10.000 = 200;
    # rencana 100 lagi → total 200 (pas) masih boleh; buat posisi lebih besar → ditolak.
    algos = {"BTCUSDT": [{"type": "STOP_MARKET", "triggerPrice": 90.0}]}
    positions = [{"symbol": "BTCUSDT", "positionAmt": "30", "entryPrice": "100"}]
    rm = RiskManager(Settings(max_open_positions=10), _FakeBinanceRisk(positions, algos), _FakeSymbols())
    decision = rm.evaluate(_signal())  # risiko terbuka 300 + rencana 100 > 200
    assert decision.allowed is False
    assert any("risiko terbuka" in r for r in decision.reasons)


def test_portfolio_liquidity_filter():
    rm = RiskManager(Settings(min_quote_volume_usd=1_000_000, max_open_positions=10),
                     _FakeBinanceRisk(), _FakeSymbols())
    decision = rm.evaluate(_signal(volume_24h_quote=500_000.0))
    assert decision.allowed is False
    assert any("Likuiditas" in r for r in decision.reasons)


# ----------------------------------------------------------------------
# 3.5 — PositionManager
# ----------------------------------------------------------------------
class _FakeBinancePM:
    def __init__(self, mark=115.0):
        self.mark = mark
        self.algos = [{"algoId": 7, "type": "STOP_MARKET", "triggerPrice": 95.0}]
        self.placed = []
        self.cancelled = []
        self.orders = []

    def positions(self, symbol=None):
        return [{"symbol": "BTCUSDT", "positionAmt": "0.01", "entryPrice": "100"}]

    def ticker_price(self, symbol):
        return {"price": str(self.mark)}

    def klines(self, symbol, interval="15m", limit=60):
        return [[0, "100", "101", "99", "100", "1", 0] for _ in range(60)]

    def open_algo_orders(self, symbol=None):
        return list(self.algos)

    def place_algo_order(self, **kw):
        self.placed.append(kw)
        self.algos.append({"algoId": 99, "type": kw["order_type"], "triggerPrice": kw["trigger_price"]})
        return {"algoId": 99}

    def cancel_algo_order(self, symbol, algo_id=None, client_algo_id=None):
        self.cancelled.append(algo_id)
        self.algos = [a for a in self.algos if a.get("algoId") != algo_id]
        return {}

    def place_order(self, **kw):
        self.orders.append(kw)
        return {"orderId": 1}


def _pm(tmp_path, fake, **settings_kw):
    journal = TradeJournal(str(tmp_path / "trades.jsonl"))
    journal.record({
        "symbol": "BTCUSDT", "side": "LONG", "quantity": 0.01, "leverage": 1,
        "reference_price": 100.0, "stop_loss": 95.0, "take_profit": 130.0,
        "notional": 100.0, "risk_amount": 0.05, "dry_run": False,
        "execution_state": "PROTECTED", "client_order_id": "baf-pm", "filled_qty": 0.01,
        "notes": [], "time": int(time.time() * 1000),
    })

    class _Ex:
        def close_position(self, symbol):
            return {"closed": True}

    settings = Settings(trading_mode="live", risk_state_path=str(tmp_path / "risk_state.json"), **settings_kw)
    symbols = SymbolManager.__new__(SymbolManager)
    symbols.filters = lambda symbol: _FakeSymbols().filters(symbol)  # type: ignore[method-assign]
    return PositionManager(settings, fake, _Ex(), journal, symbols)


def test_pm_breakeven_moves_sl_after_1r(tmp_path):
    fake = _FakeBinancePM(mark=106.0)  # profit 6 / R 5 = 1,2R
    pm = _pm(tmp_path, fake, move_sl_to_be=True)
    summary = pm.manage()
    assert summary["breakeven"] == ["BTCUSDT"]
    assert fake.placed and fake.placed[0]["trigger_price"] == 100.0  # SL baru = entri
    assert fake.cancelled == [7]  # SL lama dibatalkan SETELAH yang baru terverifikasi


def test_pm_breakeven_not_triggered_below_1r(tmp_path):
    fake = _FakeBinancePM(mark=103.0)  # 0,6R
    pm = _pm(tmp_path, fake, move_sl_to_be=True)
    assert pm.manage()["breakeven"] == []
    assert fake.placed == []


def test_pm_partial_tp(tmp_path):
    fake = _FakeBinancePM(mark=110.0)  # 2R ≥ 1,5R
    pm = _pm(tmp_path, fake, partial_tp_enabled=True)
    summary = pm.manage()
    assert summary["partial_tp"] == ["BTCUSDT"]
    assert fake.orders and fake.orders[0]["reduce_only"] is True
    assert fake.orders[0]["quantity"] == pytest.approx(0.005)
    # Kedua kalinya tidak mengulang (state persisten).
    assert pm.manage()["partial_tp"] == []


def test_pm_all_disabled_by_default(tmp_path):
    fake = _FakeBinancePM(mark=120.0)
    pm = _pm(tmp_path, fake)
    assert pm.manage() == {"breakeven": [], "trailing": [], "partial_tp": [], "max_hold": []}
