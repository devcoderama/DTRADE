"""Uji Fase 4: paginasi klines, cache riwayat, model biaya backtest
(slippage/funding/gap), model keluar, Sharpe berbasis waktu, riset."""
import math

import pytest

from core.backtest import BacktestConfig, run_backtest
from core.history import load_history
from core.research import random_baseline, run_research, walk_forward


# ----------------------------------------------------------------------
# Data bantu
# ----------------------------------------------------------------------
def _rows(n, start_price=100.0, drift=0.0025, t0=0):
    rows = []
    price = start_price
    for i in range(n):
        o = price
        price = price * (1 + drift) * (1 + 0.006 * math.sin(i / 3.0))
        rows.append([t0 + i * 900_000, o, max(o, price) * 1.001, min(o, price) * 0.999,
                     price, 100.0, t0 + (i + 1) * 900_000 - 1])
    return rows


class _FakePaginated:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def klines_paginated(self, symbol, interval, start_time, end_time):
        self.calls += 1
        return [r for r in self.rows if start_time <= int(r[0]) <= end_time]


# ----------------------------------------------------------------------
# 4.1 — paginasi client + cache
# ----------------------------------------------------------------------
class _FakeClientPages:
    def __init__(self, total):
        self.total = total

    def klines(self, symbol, interval="15m", limit=200, start_time=None, end_time=None):
        start = start_time or 0
        # Binance mengembalikan candle yang sejajar grid mulai dari >= startTime.
        t = -(-start // 900_000) * 900_000
        out = []
        while len(out) < limit and t < self.total * 900_000 and (end_time is None or t <= end_time):
            out.append([t, "1", "1", "1", "1", "1"])
            t += 900_000
        return out


def test_klines_paginated_merges_pages():
    from core.binance_client import BinanceFuturesClient

    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    fake = _FakeClientPages(5000)
    client.klines = fake.klines  # type: ignore[method-assign]
    rows = client.klines_paginated("BTCUSDT", "15m", 0, 5000 * 900_000, page=1500)
    assert len(rows) == 5000
    assert rows[0][0] == 0 and rows[-1][0] == 4999 * 900_000


def test_history_cache_avoids_refetch(tmp_path):
    rows = _rows(300, t0=1_700_000_000_000)
    client = _FakePaginated(rows)
    now = 1_700_000_000_000 + 300 * 900_000
    first = load_history(client, "BTCUSDT", "15m", days=4, cache_dir=tmp_path, now_ms=now)
    assert len(first) == 300 and client.calls == 1
    # Pemuatan kedua: hanya ekor (dari candle terakhir) yang diminta ulang.
    second = load_history(client, "BTCUSDT", "15m", days=4, cache_dir=tmp_path, now_ms=now)
    assert len(second) == 300
    assert len(list(tmp_path.glob("*.json"))) == 1


# ----------------------------------------------------------------------
# 4.2/4.3 — model biaya & keluar di backtest
# ----------------------------------------------------------------------
def test_backtest_slippage_and_funding_reduce_pnl():
    rows = _rows(500)
    clean = run_backtest("BTCUSDT", "15m", rows, BacktestConfig(
        slippage_bps=0.0, funding_rate_8h_pct=0.0, fee_pct=0.0))
    costly = run_backtest("BTCUSDT", "15m", rows, BacktestConfig(
        slippage_bps=20.0, funding_rate_8h_pct=0.05, fee_pct=0.05))
    assert clean.trades and costly.trades
    assert costly.final_balance < clean.final_balance


def test_backtest_gap_through_stop_fills_at_open():
    # SL lebar (10×ATR) agar posisi tren tetap terbuka sampai candle gap;
    # gap membuka di 50 — jauh di bawah SL — maka isi SL harus di harga
    # pembukaan (50), bukan di level SL.
    rows = _rows(200, drift=0.002)
    base_time = 200 * 900_000
    gap = [base_time, 50.0, 51.0, 49.0, 50.5, 100.0, base_time + 899_999]
    tail = _rows(60, start_price=50.5, drift=0.0, t0=base_time + 900_000)
    cfg = BacktestConfig(fee_pct=0.0, slippage_bps=0.0, funding_rate_8h_pct=0.0,
                         sl_atr_mult=10.0, tp_atr_mult=25.0)
    result = run_backtest("BTCUSDT", "15m", rows + [gap] + tail, cfg)
    sl_trades = [t for t in result.trades if t.exit_reason == "SL"]
    assert sl_trades, "posisi tren harus dihentikan oleh candle gap"
    assert sl_trades[0].exit_price == pytest.approx(50.0)


def test_backtest_partial_tp_and_breakeven():
    rows = _rows(600, drift=0.002)
    cfg = BacktestConfig(fee_pct=0.0, slippage_bps=0.0, funding_rate_8h_pct=0.0,
                         partial_tp_r=1.0, breakeven_trigger_r=1.0)
    result = run_backtest("BTCUSDT", "15m", rows, cfg)
    assert result.trades  # simulasi tetap menghasilkan trade utuh
    # PnL konsisten dengan saldo akhir.
    assert result.final_balance == pytest.approx(
        cfg.initial_balance + sum(t.pnl for t in result.trades), abs=0.05)


def test_backtest_dense_equity_curve_and_sharpe():
    rows = _rows(400)
    result = run_backtest("BTCUSDT", "15m", rows, BacktestConfig())
    # Kurva dicatat per candle (bukan hanya per trade).
    assert len(result.equity_curve) > 300
    assert result.sharpe_ratio is not None


# ----------------------------------------------------------------------
# 4.5 — riset: walk-forward, baseline acak, laporan
# ----------------------------------------------------------------------
def test_walk_forward_produces_folds():
    rows = _rows(2400)
    folds = walk_forward("BTCUSDT", "15m", rows, BacktestConfig(), folds=3)
    assert folds, "data tren panjang harus menghasilkan fold"
    for f in folds:
        assert f.test_candles > 0
        assert f.chosen_min_score in (25, 40, 55)


def test_random_baseline_deterministic_and_sane():
    rows = _rows(1200)
    base = random_baseline("BTCUSDT", "15m", rows, BacktestConfig(), target_trades=10, seeds=5)
    assert base.runs == 5
    assert base.worst_pnl <= base.mean_pnl <= base.best_pnl
    again = random_baseline("BTCUSDT", "15m", rows, BacktestConfig(), target_trades=10, seeds=5)
    assert again.mean_pnl == base.mean_pnl  # seed tetap → deterministik


def test_run_research_writes_honest_report(tmp_path):
    rows = _rows(2400)
    md, path = run_research("BTCUSDT", "15m", rows, BacktestConfig(), folds=3, reports_dir=tmp_path)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Walk-forward" in text and "Baseline acak" in text
    assert "TIDAK menjamin" in text  # peringatan wajib, tanpa klaim profit
