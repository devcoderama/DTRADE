"""Uji mesin status executor dengan Binance palsu — tanpa jaringan & tanpa order live."""
import pytest

from config.settings import Settings
from core.binance_client import BinanceAPIError
from core.executor import OrderExecutor
from core.risk import RiskDecision
from core.symbols import SymbolFilters


class FakeBinance:
    """Meniru perilaku penting Binance Futures untuk skenario executor."""

    def __init__(self, fail_sl=False, entry_unknown=False):
        self.fail_sl = fail_sl
        self.entry_unknown = entry_unknown
        self.orders = []          # semua place_order yang diterima
        self._open = []           # order proteksi yang menggantung
        self.position_amt = 0.0
        self.entry_attempts = 0
        self._entry_order = None

    def change_leverage(self, symbol, leverage):
        return {"leverage": leverage}

    def positions(self, symbol=None):
        if self.position_amt == 0:
            return []
        return [{"symbol": symbol or "BTCUSDT", "positionAmt": str(self.position_amt)}]

    def open_orders(self, symbol=None):
        return list(self._open)

    def query_order(self, symbol, order_id=None, client_order_id=None):
        if self._entry_order and client_order_id == self._entry_order["clientOrderId"]:
            return dict(self._entry_order)
        raise BinanceAPIError(400, -2013, "Order does not exist")

    def place_order(self, symbol, side, order_type, quantity=None, price=None,
                    stop_price=None, time_in_force="GTC", reduce_only=False,
                    close_position=False, new_client_order_id=None):
        if order_type == "MARKET" and not reduce_only:
            self.entry_attempts += 1
            self.position_amt = quantity if side == "BUY" else -quantity
            self._entry_order = {
                "orderId": 1, "status": "FILLED", "executedQty": str(quantity),
                "clientOrderId": new_client_order_id,
            }
            if self.entry_unknown:
                raise ConnectionError("timeout — hasil tidak diketahui")
            return dict(self._entry_order)
        if order_type == "MARKET" and reduce_only:
            self.position_amt = 0.0
            self.orders.append({"type": "EMERGENCY_CLOSE"})
            return {"orderId": 99, "status": "FILLED"}
        raise AssertionError(f"order tak terduga: {order_type}")

    def cancel_all_orders(self, symbol):
        self._open = []

    # --- Algo Order API (jalur SL/TP sejak migrasi Binance) ---
    def place_algo_order(self, symbol, side, order_type, trigger_price,
                         quantity=None, close_position=False, client_algo_id=None):
        if order_type == "STOP_MARKET" and self.fail_sl:
            raise BinanceAPIError(400, -4120, "simulasi penolakan algo order")
        order = {
            "algoId": len(self.orders) + 10, "type": order_type,
            "clientAlgoId": client_algo_id, "algoStatus": "NEW",
            "triggerPrice": str(trigger_price),
        }
        self._open.append(order)
        self.orders.append(order)
        return order

    def open_algo_orders(self, symbol=None):
        return list(self._open)

    def cancel_all_algo_orders(self, symbol):
        count = len(self._open)
        self._open = []
        return count
        return {}


class FakeSymbols:
    def filters(self, symbol):
        return SymbolFilters(
            symbol=symbol, quantity_step=0.001, quantity_min=0.001,
            quantity_max=1000.0, price_tick=0.1, min_notional=5.0,
        )


def _executor(tmp_path, fake, live=True):
    settings = Settings(
        trading_mode="live" if live else "dry_run",
        risk_state_path=str(tmp_path / "risk_state.json"),
        protection_retries=2,
    )
    return OrderExecutor(settings, fake, FakeSymbols())


def _decision():
    return RiskDecision(
        allowed=True, symbol="BTCUSDT", side="LONG", quantity=0.01,
        leverage=2, notional=1000.0, risk_amount=10.0,
        stop_loss=98.0, take_profit=103.0, reasons=["OK"],
    )


def test_live_happy_path_protected(tmp_path):
    fake = FakeBinance()
    ex = _executor(tmp_path, fake)
    result = ex.execute(_decision(), 100.0)
    assert result.state == "PROTECTED"
    assert result.filled_qty == pytest.approx(0.01)
    types = {o["type"] for o in fake._open}
    assert types == {"STOP_MARKET", "TAKE_PROFIT_MARKET"}
    states = [t["state"] for t in result.transitions]
    assert states[0] == "CREATED" and states[-1] == "PROTECTED"


def test_sl_failure_triggers_emergency_close(tmp_path):
    fake = FakeBinance(fail_sl=True)
    ex = _executor(tmp_path, fake)
    result = ex.execute(_decision(), 100.0)
    assert result.state == "CLOSED"  # ditutup darurat, bukan dibiarkan terbuka
    assert fake.position_amt == 0.0
    assert any(o.get("type") == "EMERGENCY_CLOSE" for o in fake.orders)


def test_unknown_entry_outcome_reconciled_not_duplicated(tmp_path):
    fake = FakeBinance(entry_unknown=True)
    ex = _executor(tmp_path, fake)
    result = ex.execute(_decision(), 100.0)
    assert fake.entry_attempts == 1  # TIDAK ada kirim ulang membabi buta
    assert result.state == "PROTECTED"


def test_dry_run_never_calls_exchange(tmp_path):
    fake = FakeBinance()
    ex = _executor(tmp_path, fake, live=False)
    result = ex.execute(_decision(), 100.0)
    assert result.state == "PROTECTED" and result.dry_run
    assert fake.entry_attempts == 0


def test_pending_recovery_reads_state_log(tmp_path):
    fake = FakeBinance(fail_sl=True)
    ex = _executor(tmp_path, fake)
    ex.execute(_decision(), 100.0)
    # Eksekusi di atas berakhir CLOSED (terminal) → tidak ada yang pending.
    assert ex.pending_recovery() == []
