"""Uji RiskManager dengan Binance & simbol palsu (mock) — tanpa jaringan."""
import json

import pytest

from config.settings import Settings
from core.risk import RiskManager
from core.strategy import TradeSignal
from core.symbols import SymbolFilters


class FakeBinance:
    def __init__(self, equity=10_000.0, positions=None, incomes=None):
        self._equity = equity
        self._positions = positions or []
        self._incomes = incomes or []

    def account(self):
        return {"totalWalletBalance": self._equity, "totalUnrealizedProfit": 0.0}

    def positions(self, symbol=None):
        return list(self._positions)

    def income_history(self, limit=100, start_time=None, end_time=None, income_type=None):
        rows = self._incomes
        if start_time is not None:
            rows = [r for r in rows if int(r.get("time", 0)) >= start_time]
        if income_type:
            rows = [r for r in rows if r.get("incomeType") == income_type]
        return rows[:limit]
        return list(self._incomes)


class FakeSymbols:
    def is_tradable(self, symbol):
        return True

    def filters(self, symbol):
        return SymbolFilters(
            symbol=symbol, quantity_step=0.001, quantity_min=0.001,
            quantity_max=1000.0, price_tick=0.1, min_notional=5.0,
        )


def _settings(tmp_path, **over):
    base = dict(
        risk_state_path=str(tmp_path / "risk_state.json"),
        risk_per_trade_pct=1.0, max_leverage=5, max_open_positions=3,
        daily_loss_limit_pct=3.0, weekly_loss_limit_pct=6.0,
        min_risk_reward=1.5, max_consecutive_losses=3,
        max_symbol_exposure_pct=150.0, max_total_exposure_pct=300.0,
    )
    base.update(over)
    return Settings(**base)


def _signal(side="LONG", price=100.0, sl=98.0, tp=103.0, atr=2.0):
    return TradeSignal(
        symbol="BTCUSDT", side=side, confidence=80, price=price,
        stop_loss=sl, take_profit=tp, technical_score=60,
        snapshot={"atr14": atr},
    )


def test_valid_long_approved(tmp_path):
    rm = RiskManager(_settings(tmp_path), FakeBinance(), FakeSymbols())
    d = rm.evaluate(_signal())
    assert d.allowed, d.reasons
    assert d.quantity > 0 and d.risk_reward >= 1.5


def test_long_with_sl_above_entry_rejected(tmp_path):
    rm = RiskManager(_settings(tmp_path), FakeBinance(), FakeSymbols())
    d = rm.evaluate(_signal(sl=101.0, tp=105.0))
    assert not d.allowed and any("LONG" in r for r in d.reasons)


def test_short_with_wrong_levels_rejected(tmp_path):
    rm = RiskManager(_settings(tmp_path), FakeBinance(), FakeSymbols())
    d = rm.evaluate(_signal(side="SHORT", sl=99.0, tp=95.0))
    assert not d.allowed


def test_low_risk_reward_rejected(tmp_path):
    rm = RiskManager(_settings(tmp_path), FakeBinance(), FakeSymbols())
    d = rm.evaluate(_signal(sl=99.0, tp=101.0))  # RR = 1.0 < 1.5
    assert not d.allowed and any("Risk/reward" in r for r in d.reasons)


def test_stop_too_close_rejected(tmp_path):
    rm = RiskManager(_settings(tmp_path), FakeBinance(), FakeSymbols())
    d = rm.evaluate(_signal(sl=99.9, tp=100.3, atr=4.0))  # min stop = 0.25*ATR = 1.0
    assert not d.allowed and any("terlalu dekat" in r for r in d.reasons)


def test_daily_loss_lock_persists(tmp_path):
    state_path = tmp_path / "risk_state.json"
    settings = _settings(tmp_path)
    rm = RiskManager(settings, FakeBinance(equity=10_000.0), FakeSymbols())
    assert rm.evaluate(_signal()).allowed  # baseline 10.000 tercatat

    # Ekuitas turun 4% dari baseline → kunci harian aktif & tersimpan.
    rm2 = RiskManager(settings, FakeBinance(equity=9_600.0), FakeSymbols())
    d = rm2.evaluate(_signal())
    assert not d.allowed
    saved = json.loads(state_path.read_text())
    assert saved["daily_locked"] is True

    # Restart (manajer baru) tetap terkunci walau ekuitas pulih sebagian.
    rm3 = RiskManager(settings, FakeBinance(equity=9_900.0), FakeSymbols())
    assert not rm3.evaluate(_signal()).allowed


def test_consecutive_losses_block(tmp_path):
    import time as _time

    now = int(_time.time() * 1000)
    incomes = [
        {"incomeType": "REALIZED_PNL", "income": "-10", "time": now - 1000},
        {"incomeType": "REALIZED_PNL", "income": "-5", "time": now - 2000},
        {"incomeType": "REALIZED_PNL", "income": "-7", "time": now - 3000},
        {"incomeType": "REALIZED_PNL", "income": "50", "time": now - 4000},
    ]
    rm = RiskManager(_settings(tmp_path), FakeBinance(incomes=incomes), FakeSymbols())
    d = rm.evaluate(_signal())
    assert not d.allowed and any("beruntun" in r for r in d.reasons)


def test_consecutive_losses_outside_window_ignored(tmp_path):
    """Fase 2.1: rugi beruntun di LUAR jendela 24 jam tidak lagi mengunci."""
    import time as _time

    now = int(_time.time() * 1000)
    old = now - 48 * 3600 * 1000  # 48 jam lalu — di luar jendela bawaan 24 jam
    incomes = [
        {"incomeType": "REALIZED_PNL", "income": "-10", "time": old},
        {"incomeType": "REALIZED_PNL", "income": "-5", "time": old - 1000},
        {"incomeType": "REALIZED_PNL", "income": "-7", "time": old - 2000},
    ]
    rm = RiskManager(_settings(tmp_path), FakeBinance(incomes=incomes), FakeSymbols())
    assert rm.evaluate(_signal()).allowed


def test_existing_position_same_symbol_rejected(tmp_path):
    pos = [{"symbol": "BTCUSDT", "positionAmt": "0.01", "notional": "100"}]
    rm = RiskManager(_settings(tmp_path), FakeBinance(positions=pos), FakeSymbols())
    assert not rm.evaluate(_signal()).allowed
