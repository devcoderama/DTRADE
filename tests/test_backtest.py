"""Uji metrik backtest: expectancy, Sharpe, Sortino, rugi beruntun."""
from core.backtest import BacktestResult, BacktestTrade, run_backtest, BacktestConfig


def _trade(pnl):
    return BacktestTrade(
        side="LONG", entry_time=0, entry_price=100.0, exit_time=1,
        exit_price=100.0 + pnl, quantity=1.0, pnl=pnl, exit_reason="TP", score=60,
    )


def test_metrics_on_handcrafted_result():
    result = BacktestResult(
        symbol="BTCUSDT", interval="15m", candles=100,
        trades=[_trade(10.0), _trade(-5.0), _trade(-3.0), _trade(20.0), _trade(-2.0)],
        equity_curve=[
            {"time": 0, "equity": 1000.0}, {"time": 1, "equity": 1010.0},
            {"time": 2, "equity": 1005.0}, {"time": 3, "equity": 1002.0},
            {"time": 4, "equity": 1022.0}, {"time": 5, "equity": 1020.0},
        ],
        initial_balance=1000.0, final_balance=1020.0,
    )
    d = result.to_dict()
    assert d["expectancy"] == 4.0
    assert d["avg_win"] == 15.0
    assert d["avg_loss"] == round(-10.0 / 3, 2)
    assert d["max_consecutive_losses"] == 2
    assert d["sharpe_ratio"] is not None
    assert d["sortino_ratio"] is not None
    assert d["profit_factor"] == round(30.0 / 10.0, 2)


def test_run_backtest_produces_trades_on_trend():
    # Tren naik dengan noise realistis → skor teknikal positif → ada trade.
    # (Deret yang terlalu mulus membuat RSI = 100 dan justru dihukum skor.)
    import math

    rows = []
    price = 100.0
    for i in range(400):
        o = price
        price = price * 1.002 * (1 + 0.004 * math.sin(i / 3.0))
        rows.append([i * 900_000, o, price * 1.001, o * 0.999, price, 100.0])
    result = run_backtest("BTCUSDT", "15m", rows, BacktestConfig())
    assert result.trades, "tren searah harus menghasilkan trade"
    d = result.to_dict()
    for key in ("expectancy", "sharpe_ratio", "sortino_ratio", "max_consecutive_losses"):
        assert key in d
