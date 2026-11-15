"""Uji Fase 6 — Fase B: B1–B8. Semua fake, tanpa jaringan."""
import json
import time

import pytest

from config.settings import ConfigError, Settings
from core.journal import TradeJournal
from core.market_stream import MarketStream
from core.position_manager import PositionManager
from core.risk import RiskManager
from core.symbols import SymbolFilters
from core.strategy import TradeSignal


# ----------------------------------------------------------------------
# B1 — id algo unik + buffer break-even
# ----------------------------------------------------------------------
class _FakeSymbols:
    def is_tradable(self, symbol):
        return True

    def filters(self, symbol):
        return SymbolFilters(symbol=symbol, quantity_step=0.001, quantity_min=0.001,
                             quantity_max=1e9, price_tick=0.01, min_notional=5.0)


class _FakeBinancePM:
    def __init__(self):
        self.algos = []
        self._next_id = 100

    def place_algo_order(self, symbol, side, order_type, trigger_price, **kw):
        self._next_id += 1
        order = {"algoId": self._next_id, "type": order_type,
                 "clientAlgoId": kw.get("client_algo_id"),
                 "triggerPrice": str(trigger_price), "symbol": symbol}
        self.algos.append(order)
        return order

    def open_algo_orders(self, symbol=None):
        return list(self.algos)

    def cancel_algo_order(self, symbol, algo_id=None, client_algo_id=None):
        self.algos = [o for o in self.algos if o["algoId"] != algo_id]
        return {}


def test_b1_replace_sl_ids_unique_across_calls(tmp_path):
    pm = PositionManager(Settings(risk_state_path=str(tmp_path / "r.json")),
                         _FakeBinancePM(), object(), object(), _FakeSymbols())
    fake = pm._binance
    assert pm._replace_sl("BTCUSDT", "SELL", 95.0, "trail") is True
    assert pm._replace_sl("BTCUSDT", "SELL", 96.0, "trail") is True
    ids = [o["clientAlgoId"] for o in fake.algos]
    assert len(ids) == len(set(ids)) == 1  # lama dibatalkan, tersisa yang baru & unik
    assert ids[0].startswith("pm-trail-btcusdt-")
    assert len(ids[0]) <= 36


def test_b1_breakeven_includes_fee_buffer(tmp_path):
    # BE untuk LONG 100 dengan buffer 0,1% → SL di 100,10 (bukan tepat 100).
    pm = PositionManager(Settings(risk_state_path=str(tmp_path / "r.json")),
                         _FakeBinancePM(), object(), object(), _FakeSymbols())
    price = pm._breakeven_price(100.0, "LONG")
    assert price == pytest.approx(100.10)
    assert pm._breakeven_price(100.0, "SHORT") == pytest.approx(99.90)


# ----------------------------------------------------------------------
# B2 + B3 — streak per trade & fail-closed live
# ----------------------------------------------------------------------
class _FakeBinanceRisk:
    def __init__(self, income_rows=None, fail_income=False):
        self._rows = income_rows or []
        self._fail = fail_income

    def account(self):
        return {"totalWalletBalance": "10000", "totalUnrealizedProfit": "0"}

    def positions(self, symbol=None):
        return []

    def open_algo_orders(self, symbol=None):
        return []

    def income_history(self, **kw):
        if self._fail:
            raise RuntimeError("API income mati")
        return list(self._rows)


def _signal(**snap_kw):
    snap = {"symbol": "ETHUSDT", "price": 100.0, "atr14": 2.0, "funding_rate": 0.0,
            "volume_24h_quote": 50_000_000.0, "regime": "bullish_trending", "adx14": 30.0}
    snap.update(snap_kw)
    return TradeSignal(symbol="ETHUSDT", side="LONG", confidence=80, price=100.0,
                       stop_loss=95.0, take_profit=110.0, technical_score=60, snapshot=snap)


def _income(trade_id, total, when_ms, parts=3):
    each = total / parts
    return [{"symbol": "ETHUSDT", "incomeType": "REALIZED_PNL", "income": str(each),
             "tradeId": trade_id, "time": when_ms + i} for i in range(parts)]


def test_b2_streak_counts_trades_not_fills():
    now = int(time.time() * 1000)
    rows = []
    rows += _income("t3", -30.0, now - 1000)   # trade rugi terbaru (3 fill)
    rows += _income("t2", -10.0, now - 5000)   # trade rugi sebelumnya (3 fill)
    rows += _income("t1", 50.0, now - 9000)    # trade untung → menghentikan streak
    rm = RiskManager(Settings(), _FakeBinanceRisk(rows), _FakeSymbols())
    assert rm._consecutive_losses() == 2  # bukan 6 baris


def test_b3_income_failure_fail_closed_when_live():
    rm = RiskManager(Settings(trading_mode="live", max_consecutive_losses=3),
                     _FakeBinanceRisk(fail_income=True), _FakeSymbols())
    assert rm._consecutive_losses() is None
    decision = rm.evaluate(_signal())
    assert decision.allowed is False
    assert any("fail-closed" in r or "tidak terbaca" in r for r in decision.reasons)


def test_b3_income_failure_lenient_when_dry_run():
    rm = RiskManager(Settings(trading_mode="dry_run", max_consecutive_losses=3),
                     _FakeBinanceRisk(fail_income=True), _FakeSymbols())
    decision = rm.evaluate(_signal())
    assert decision.allowed is True  # dry-run: peringatan saja, tidak memblokir


# ----------------------------------------------------------------------
# B4 — baseline dicatat reconciler (API publik) + file lock
# ----------------------------------------------------------------------
def test_b4_sync_daily_baselines_public(tmp_path):
    rm = RiskManager(Settings(risk_state_path=str(tmp_path / "risk.json")),
                     _FakeBinanceRisk(), _FakeSymbols())
    state = rm.sync_daily_baselines()
    assert state["day_start_equity"] == 10000.0
    assert (tmp_path / "risk.json").exists()


def test_b4_file_lock_roundtrip(tmp_path):
    from utils.filelock import file_lock

    target = tmp_path / "state.json"
    with file_lock(target, timeout=1.0) as acquired:
        assert acquired is True
        with file_lock(target, timeout=0.2) as second:
            assert second is False  # terkunci → menyerah tanpa crash
    with file_lock(target, timeout=1.0) as again:
        assert again is True


# ----------------------------------------------------------------------
# B5 — validasi Settings tambahan
# ----------------------------------------------------------------------
@pytest.mark.parametrize("kwargs", [
    {"reconciler_interval_sec": 3},
    {"partial_tp_fraction": 1.5},
    {"partial_tp_fraction": 0.0},
    {"max_hold_hours": -1.0},
    {"breakeven_trigger_r": 0.0},
    {"trailing_trigger_r": -1.0},
    {"trailing_atr_mult": 0.0},
    {"max_symbol_exposure_pct": 0.0},
    {"max_total_exposure_pct": -5.0},
    {"rate_limit_per_minute": -1},
    {"web_api_token": "pendek"},
])
def test_b5_invalid_settings_rejected(kwargs):
    with pytest.raises(ConfigError):
        Settings(**kwargs)


def test_b5_valid_edge_settings_accepted():
    Settings(reconciler_interval_sec=5, partial_tp_fraction=0.5,
             web_api_token="x" * 24, rate_limit_per_minute=0)


# ----------------------------------------------------------------------
# B6 — keputusan AI terpisah + cache registry
# ----------------------------------------------------------------------
def test_b6_ai_decisions_separate_file(tmp_path):
    path = tmp_path / "trades.jsonl"
    j = TradeJournal(str(path))

    class _AI:
        model_used = "m1"
        signal = "LONG"
        confidence = 80

    class _Sig:
        ai = _AI()
        symbol = "BTCUSDT"
        side = "LONG"
        confidence = 70
        technical_score = 40

    j.record_ai_decision(_Sig(), provider="fake")
    assert not path.exists() or "ai_decision" not in path.read_text(encoding="utf-8")
    ai_file = tmp_path / "ai_decisions.jsonl"
    assert ai_file.exists()
    rows = [json.loads(x) for x in ai_file.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert rows[0]["type"] == "ai_decision"


def test_b6_all_entries_cache_invalidated_on_record(tmp_path):
    j = TradeJournal(str(tmp_path / "trades.jsonl"))
    j.record({"type": "execution", "symbol": "BTCUSDT"})
    assert len(j.all_entries()) == 1
    j.record({"type": "execution", "symbol": "ETHUSDT"})
    assert len(j.all_entries()) == 2  # cache tidak basi setelah append


# ----------------------------------------------------------------------
# B7 — gerbang ADX/sideways, ATR None → NEUTRAL, rezim terskala interval
# ----------------------------------------------------------------------
def test_b7_gate_blocks_sideways_and_weak_adx():
    from core.strategy import SignalEngine

    engine = SignalEngine(Settings(), object(), None)
    snap = {"regime": "sideways", "adx14": 30.0}
    assert engine._gate_blocks(snap, "LONG") != []
    snap2 = {"regime": "uncertain", "adx14": 10.0}
    assert engine._gate_blocks(snap2, "LONG") != []
    snap3 = {"regime": "bullish_trending", "adx14": 30.0}
    assert engine._gate_blocks(snap3, "LONG") == []


def test_b7_regime_thresholds_scale_with_interval():
    from core.indicators import classify_regime

    snap = {"trend": "SIDEWAYS", "adx14": 10.0, "atr_pct": 1.0, "bb_pct_b": 0.5,
            "last_volume": 1.0, "avg_volume": 1.0}
    assert classify_regime(snap, interval="15m") == "sideways"
    # Di 4h, ATR 1% itu relatif kecil → low_volatility (ambang terskala naik).
    assert classify_regime(snap, interval="4h") == "low_volatility"


def test_b7_atr_missing_forces_neutral():
    from core.strategy import SignalEngine

    class _FakeAI:
        name, model = "fake", "m"

        def is_configured(self):
            return True

        def analyze_market(self, symbol, snapshot):
            from core.ai_providers import AIAnalysis

            return AIAnalysis(symbol=symbol, signal="LONG", confidence=90, stop_loss=95.0,
                              take_profit=110.0, reasoning="uji", model_used="m")

    engine = SignalEngine(Settings(ai_can_initiate=True), object(), _FakeAI())
    snapshot = {
        "symbol": "BTCUSDT", "price": 100.0, "atr14": None, "atr_pct": None,
        "trend": "UPTREND", "ema20": 101.0, "ema50": 99.0, "ema200": 90.0,
        "rsi14": 60.0, "macd_histogram": 1.0, "adx14": 30.0, "regime": "bullish_trending",
        "funding_rate": 0.0, "volume_24h_quote": 10_000_000.0, "change_24h_pct": 1.0,
        "bb_pct_b": 0.5, "recent_candles": [], "includes_open_candle": False,
    }
    engine.snapshot_for = lambda symbol, interval=None: dict(snapshot)
    engine._multi_timeframe = lambda symbol, interval: {}
    engine._btc_context = lambda symbol: None
    signal = engine.analyze("BTCUSDT", use_ai=True, fresh=True)
    assert signal.side == "NEUTRAL"
    assert any("ATR" in r for r in signal.reasons)


# ----------------------------------------------------------------------
# B8 — refcount langganan market stream
# ----------------------------------------------------------------------
def test_b8_unsubscribe_refcount():
    stream = MarketStream(Settings(binance_testnet=True))
    stream._ensure_running = lambda: None
    stream._poke = lambda: None
    stream.subscribe_kline("BTCUSDT", "15m")
    stream.subscribe_kline("BTCUSDT", "15m")  # tab kedua, simbol sama
    stream.unsubscribe_kline("BTCUSDT", "15m")  # satu tab tutup
    assert ("BTCUSDT", "15m") in stream._klines_subs  # tab lain tetap menerima
    stream.unsubscribe_kline("BTCUSDT", "15m")
    assert ("BTCUSDT", "15m") not in stream._klines_subs
    assert "BTCUSDT" not in stream._mark_subs
