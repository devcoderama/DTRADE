"""Uji Fase 2: presisi Decimal, top_by_volume, validasi Settings,
indikator per-interval & seed EMA, jurnal performa, reconciler."""
import random
import time

import pytest

from config.settings import ConfigError, Settings
from core.indicators import build_snapshot, ema_series
from core.journal import TradeJournal
from core.reconciler import Reconciler
from core.symbols import SymbolFilters, SymbolManager


# ----------------------------------------------------------------------
# 2.3 — presisi Decimal
# ----------------------------------------------------------------------
def _filters(step=0.1, tick=0.1):
    return SymbolFilters(
        symbol="X", quantity_step=step, quantity_min=0.0, quantity_max=1e9,
        price_tick=tick, min_notional=0.0,
    )


@pytest.mark.parametrize("qty,step,expected", [
    (0.3, 0.1, 0.3),      # kasus klasik kegagalan float: 0.3/0.1 = 2.9999…
    (0.57, 0.01, 0.57),
    (1.15, 0.01, 1.15),
    (0.999, 0.001, 0.999),
])
def test_decimal_rounding_exact_steps(qty, step, expected):
    f = _filters(step=step, tick=step)
    assert f.round_quantity(qty) == pytest.approx(expected)
    assert f.round_price(qty) == pytest.approx(expected)


def test_decimal_rounding_property_random():
    rng = random.Random(42)
    for step in (0.1, 0.01, 0.001, 0.05, 0.25):
        f = _filters(step=step, tick=step)
        for _ in range(200):
            v = rng.uniform(0, 500)
            r = f.round_quantity(v)
            assert r <= v + 1e-12
            assert abs(r / step - round(r / step)) < 1e-6  # kelipatan step
            assert v - r < step + 1e-9  # tidak turun lebih dari satu step


# ----------------------------------------------------------------------
# 2.2 — top_by_volume
# ----------------------------------------------------------------------
class _FakeClientVol:
    def exchange_info(self):
        def sym(s):
            return {"symbol": s, "status": "TRADING", "contractType": "PERPETUAL",
                    "quoteAsset": "USDT", "filters": []}
        return {"symbols": [sym("AAAUSDT"), sym("BBBUSDT"), sym("CCCUSDT")]}

    def tickers_24h_all(self):
        return [
            {"symbol": "AAAUSDT", "quoteVolume": "100"},
            {"symbol": "BBBUSDT", "quoteVolume": "9999"},
            {"symbol": "CCCUSDT", "quoteVolume": "500"},
            {"symbol": "XXXUSDT", "quoteVolume": "77777"},  # tidak aktif → disaring
        ]


def test_top_by_volume_orders_and_filters():
    mgr = SymbolManager(_FakeClientVol(), Settings())
    assert mgr.top_by_volume(2) == ["BBBUSDT", "CCCUSDT"]


# ----------------------------------------------------------------------
# 2.5 — validasi Settings
# ----------------------------------------------------------------------
def test_settings_range_validation():
    with pytest.raises(ConfigError):
        Settings(risk_per_trade_pct=9)
    with pytest.raises(ConfigError):
        Settings(max_leverage=0)
    with pytest.raises(ConfigError):
        Settings(min_risk_reward=0.5)
    with pytest.raises(ConfigError):
        Settings(default_interval="7x")
    Settings()  # bawaan harus sah


def test_has_ai_key_follows_active_provider():
    assert Settings(ai_provider="deepseek", deepseek_api_key="k").has_ai_key is True
    assert Settings(ai_provider="deepseek", bangunweb_api_key="k").has_ai_key is False
    assert Settings(ai_provider="openai", openai_api_key="k").active_ai_model == "gpt-4o"


def test_config_error_on_malformed_env(monkeypatch):
    from config.settings import load_settings

    monkeypatch.setenv("DAILY_LOSS_LIMIT_PCT", "tiga")
    with pytest.raises(ConfigError):
        load_settings()


# ----------------------------------------------------------------------
# 2.4 — indikator: change_1h per interval + seed SMA pada EMA
# ----------------------------------------------------------------------
def _klines(closes, interval_ms=900_000):
    now = int(time.time() * 1000)
    start = now - len(closes) * interval_ms
    return [
        [start + i * interval_ms, str(c), str(c), str(c), str(c), "10",
         start + (i + 1) * interval_ms - 1]
        for i, c in enumerate(closes)
    ]


def test_change_1h_uses_actual_interval():
    closes = [100.0] * 20 + [110.0]
    snap = build_snapshot("X", _klines(closes), {}, {}, interval="15m")
    # 15m: 1 jam = 4 candle → bandingkan close[-1] vs close[-5] (=100) → +10%
    assert snap["change_1h_pct"] == pytest.approx(10.0)
    snap4h = build_snapshot("X", _klines(closes), {}, {}, interval="4h")
    assert snap4h["change_1h_pct"] is None  # interval > 1 jam: tidak ditebak


def test_ema_seed_is_sma():
    values = [float(i) for i in range(1, 31)]
    series = ema_series(values, 10)
    assert series[9] == pytest.approx(sum(values[:10]) / 10)  # titik seed = SMA(10)


def test_snapshot_marks_open_candle():
    rows = _klines([100.0] * 30)
    rows[-1][6] = int(time.time() * 1000) + 900_000  # closeTime di masa depan
    snap = build_snapshot("X", rows, {}, {}, interval="15m")
    assert snap["includes_open_candle"] is True  # candle terakhir belum tertutup
    rows[-1][6] = int(time.time() * 1000) - 1
    snap2 = build_snapshot("X", rows, {}, {}, interval="15m")
    assert snap2["includes_open_candle"] is False


# ----------------------------------------------------------------------
# 2.7 — jurnal: penutupan, R-multiple, metrik performa
# ----------------------------------------------------------------------
def test_journal_close_and_performance(tmp_path):
    j = TradeJournal(str(tmp_path / "trades.jsonl"))
    j.record_close(symbol="BTCUSDT", side="LONG", exit_price=110.0, exit_reason="TP",
                   realized_pnl=20.0, client_order_id="baf-1", risk_amount=10.0)
    j.record_close(symbol="ETHUSDT", side="LONG", exit_price=90.0, exit_reason="SL",
                   realized_pnl=-10.0, client_order_id="baf-2", risk_amount=10.0)
    entries = j.all_entries()
    assert entries[0]["r_multiple"] == 2.0
    perf = j.performance()
    assert perf["closed_trades"] == 2
    assert perf["win_rate_pct"] == 50.0
    assert perf["profit_factor"] == 2.0
    assert perf["expectancy"] == 5.0
    assert perf["avg_r"] == 0.5
    assert perf["total_pnl"] == 10.0


# ----------------------------------------------------------------------
# 2.6 — reconciler: tiga skenario
# ----------------------------------------------------------------------
class _FakeBinanceRecon:
    def __init__(self, positions=None, algo=None, incomes=None):
        self._positions = positions or []
        self._algo = algo or []
        self._incomes = incomes or []
        self.placed = []
        self.cancelled = []

    def positions(self, symbol=None):
        return list(self._positions)

    def open_algo_orders(self, symbol=None):
        return list(self._algo)

    def place_algo_order(self, **kw):
        self.placed.append(kw)
        self._algo.append({"symbol": kw["symbol"], "type": kw["order_type"]})
        return {"algoId": 1}

    def cancel_all_algo_orders(self, symbol):
        self.cancelled.append(symbol)
        self._algo = [o for o in self._algo if o.get("symbol") != symbol]
        return 1

    def income_history(self, limit=100, start_time=None, end_time=None, income_type=None):
        return list(self._incomes)


class _FakeExecutor:
    def __init__(self):
        self.closed = []

    def close_position(self, symbol):
        self.closed.append(symbol)
        return {"closed": True}


def _journal_with_entry(tmp_path, symbol="BTCUSDT", cid="baf-1", sl=95.0):
    j = TradeJournal(str(tmp_path / "trades.jsonl"))
    j.record({
        "symbol": symbol, "side": "LONG", "quantity": 0.01, "leverage": 1,
        "reference_price": 100.0, "stop_loss": sl, "take_profit": 110.0,
        "notional": 100.0, "risk_amount": 10.0, "dry_run": False,
        "execution_state": "PROTECTED", "client_order_id": cid, "filled_qty": 0.01,
        "notes": [],
    })
    return j


def _recon(tmp_path, fake, journal, live=True):
    settings = Settings(trading_mode="live" if live else "dry_run")
    ex = _FakeExecutor()
    return Reconciler(settings, fake, ex, journal), ex


def test_reconciler_replaces_missing_sl(tmp_path):
    j = _journal_with_entry(tmp_path)
    fake = _FakeBinanceRecon(
        positions=[{"symbol": "BTCUSDT", "positionAmt": "0.01"}], algo=[])
    recon, _ = _recon(tmp_path, fake, j)
    summary = recon.run_once()
    assert summary["sl_placed"] == ["BTCUSDT"]
    assert fake.placed and fake.placed[0]["trigger_price"] == 95.0


def test_reconciler_cancels_orphan_algo(tmp_path):
    j = _journal_with_entry(tmp_path)
    fake = _FakeBinanceRecon(
        positions=[{"symbol": "BTCUSDT", "positionAmt": "0.01"}],
        algo=[{"symbol": "BTCUSDT", "type": "STOP_MARKET"},
              {"symbol": "ETHUSDT", "type": "STOP_MARKET"}])
    recon, _ = _recon(tmp_path, fake, j)
    summary = recon.run_once()
    assert summary["orphans_cancelled"] == ["ETHUSDT"]
    assert fake.cancelled == ["ETHUSDT"]


def test_reconciler_records_closed_position(tmp_path):
    j = _journal_with_entry(tmp_path)
    fake = _FakeBinanceRecon(
        positions=[], algo=[],
        incomes=[{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "12.5", "time": 1}])
    recon, _ = _recon(tmp_path, fake, j)
    summary = recon.run_once()
    assert summary["closes_recorded"] == ["BTCUSDT"]
    closes = [e for e in j.all_entries() if e.get("type") == "close"]
    assert len(closes) == 1 and closes[0]["realized_pnl"] == 12.5
    # Putaran kedua tidak mencatat ulang (registri sudah tertutup).
    assert recon.run_once()["closes_recorded"] == []


def test_reconciler_dry_run_only_logs(tmp_path):
    j = _journal_with_entry(tmp_path)
    fake = _FakeBinanceRecon(
        positions=[{"symbol": "BTCUSDT", "positionAmt": "0.01"}], algo=[])
    recon, ex = _recon(tmp_path, fake, j, live=False)
    summary = recon.run_once()
    assert summary["sl_placed"] == [] and fake.placed == [] and ex.closed == []
