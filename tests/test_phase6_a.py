"""Uji Fase 6 (Audit November 2026) — Fase A: A1–A7.

Semua memakai fake — tanpa jaringan, tanpa order sungguhan.
"""
import json
import logging
import threading
import time

import pytest

from config.settings import Settings
from core.binance_client import BinanceFuturesClient
from core.executor import OrderExecutor
from core.journal import TradeJournal
from core.reconciler import Reconciler, open_registry
from core.risk import RiskDecision
from core.symbols import SymbolFilters
from utils.logger import RedactingFormatter, redact


# ----------------------------------------------------------------------
# A2 — serialisasi angka tanpa notasi ilmiah
# ----------------------------------------------------------------------
class _CaptureSession:
    def __init__(self):
        self.calls = []

    def request(self, method, url, params=None, headers=None, timeout=None):
        self.calls.append(dict(params or {}))

        class _Resp:
            status_code = 200

            def json(self):
                return {"orderId": 1, "status": "FILLED"}

        return _Resp()


def test_a2_no_scientific_notation_in_params():
    client = BinanceFuturesClient(Settings(binance_api_key="k", binance_api_secret="s"))
    client._session = _CaptureSession()
    client.place_order(symbol="1000PEPEUSDT", side="BUY", order_type="LIMIT",
                       quantity=1.23e-05, price=1.2e-07, time_in_force="GTC")
    params = client._session.calls[-1]
    for key in ("quantity", "price"):
        assert "e" not in str(params[key]).lower(), f"{key}={params[key]!r} memakai notasi ilmiah"
    assert params["quantity"] == "0.0000123"
    assert params["price"] == "0.00000012"


def test_a2_algo_trigger_price_plain():
    client = BinanceFuturesClient(Settings(binance_api_key="k", binance_api_secret="s"))
    client._session = _CaptureSession()
    client.place_algo_order(symbol="1000SHIBUSDT", side="SELL", order_type="STOP_MARKET",
                            trigger_price=8.5e-06, close_position=True)
    params = client._session.calls[-1]
    assert params["triggerPrice"] == "0.0000085"


# ----------------------------------------------------------------------
# A4 — repr(Settings) & redaksi traceback
# ----------------------------------------------------------------------
def test_a4_repr_settings_hides_secrets():
    s = Settings(binance_api_secret="RAHASIA123", binance_api_key="KUNCI456",
                 web_api_token="TOKEN789-token-uji-panjang", database_url="postgres://u:PSSWD@h/db",
                 telegram_bot_token="BOTTOKEN0")
    text = repr(s)
    for secret in ("RAHASIA123", "KUNCI456", "TOKEN789-token-uji-panjang", "PSSWD", "BOTTOKEN0"):
        assert secret not in text


def test_a4_traceback_redacted():
    formatter = RedactingFormatter("%(message)s")
    try:
        raise RuntimeError("gagal signature=deadbeefcafe1234 untuk token BOTRAHASIA")
    except RuntimeError:
        record = logging.LogRecord("x", logging.ERROR, __file__, 1,
                                   "error saat request", (), __import__("sys").exc_info())
    out = formatter.format(record)
    assert "deadbeefcafe1234" not in out
    assert "signature=***" in out


def test_a4_redact_keeps_normal_text():
    assert redact("harga naik 5%") == "harga naik 5%"


# ----------------------------------------------------------------------
# A3 — registry mengabaikan dry-run; langkah (c) tak menulis saat dry-run
# ----------------------------------------------------------------------
def _journal(tmp_path, rows):
    path = tmp_path / "trades.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return TradeJournal(str(path))


def test_a3_open_registry_ignores_dry_run(tmp_path):
    j = _journal(tmp_path, [
        {"type": "execution", "symbol": "BTCUSDT", "client_order_id": "dry-1",
         "execution_state": "PROTECTED", "dry_run": True, "stop_loss": 90.0},
        {"type": "execution", "symbol": "ETHUSDT", "client_order_id": "live-1",
         "execution_state": "PROTECTED", "dry_run": False, "stop_loss": 90.0},
    ])
    registry = open_registry(j)
    assert "dry-1" not in registry
    assert "live-1" in registry


class _FakeBinanceRecon:
    def __init__(self):
        self.income = [{"symbol": "BTCUSDT", "incomeType": "REALIZED_PNL", "income": "12.5"}]

    def positions(self, symbol=None):
        return []

    def open_algo_orders(self, symbol=None):
        return []

    def income_history(self, **kw):
        return list(self.income)


def test_a3_dry_run_reconciler_writes_no_close(tmp_path):
    j = _journal(tmp_path, [
        {"type": "execution", "symbol": "BTCUSDT", "client_order_id": "dry-1",
         "execution_state": "PROTECTED", "dry_run": True, "stop_loss": 90.0, "time": 1},
    ])
    rec = Reconciler(Settings(trading_mode="dry_run"), _FakeBinanceRecon(), object(), j)
    summary = rec.run_once()
    assert summary["closes_recorded"] == []
    assert all(e.get("type") != "close" for e in j.all_entries())


# ----------------------------------------------------------------------
# A5 — pending_recovery: close berpasangan + RESOLVED + rotasi
# ----------------------------------------------------------------------
class _FakeSymbols:
    def filters(self, symbol):
        return SymbolFilters(symbol=symbol, quantity_step=0.001, quantity_min=0.001,
                             quantity_max=1e9, price_tick=0.1, min_notional=5.0)


class _FakeBinanceClose:
    def __init__(self):
        self.position_amt = 0.1

    def positions(self, symbol=None):
        if self.position_amt == 0:
            return []
        return [{"symbol": symbol or "BTCUSDT", "positionAmt": str(self.position_amt)}]

    def place_order(self, symbol, side, order_type, quantity=None, **kw):
        if kw.get("reduce_only"):
            self.position_amt = 0.0
        return {"orderId": 5, "status": "FILLED"}

    def cancel_all_orders(self, symbol):
        return {}

    def cancel_all_algo_orders(self, symbol):
        return 0


def _executor(tmp_path, fake):
    settings = Settings(trading_mode="live", binance_api_key="k", binance_api_secret="s",
                        risk_state_path=str(tmp_path / "risk.json"), protection_retries=1)
    return OrderExecutor(settings, fake, _FakeSymbols())


def test_a5_close_position_not_in_pending_recovery(tmp_path):
    ex = _executor(tmp_path, _FakeBinanceClose())
    result = ex.close_position("BTCUSDT")
    assert result["closed"] is True
    assert ex.pending_recovery() == []


def test_a5_mark_resolved_clears_pending(tmp_path):
    ex = _executor(tmp_path, _FakeBinanceClose())
    from core.executor import ExecutionResult

    stuck = ExecutionResult(symbol="ETHUSDT", side="LONG", dry_run=False,
                            client_order_id="baf-stuck-1")
    ex._transition(stuck, "RECOVERY_REQUIRED", "uji")
    assert [r["client_order_id"] for r in ex.pending_recovery()] == ["baf-stuck-1"]
    ex.mark_resolved("baf-stuck-1", note="sudah ditangani manual")
    assert ex.pending_recovery() == []


# ----------------------------------------------------------------------
# A1 — race reconciler vs executor + write-ahead intent + masa tenggang
# ----------------------------------------------------------------------
class _FakeBinanceRace:
    """place_algo_order lambat (0,6 dtk) — jendela race A1."""

    def __init__(self):
        self.position_amt = 0.0
        self.algos = []
        self.close_calls = 0
        self._entry = None

    def positions(self, symbol=None):
        if self.position_amt == 0:
            return []
        return [{"symbol": symbol or "BTCUSDT", "positionAmt": str(self.position_amt)}]

    def change_leverage(self, symbol, leverage):
        return {}

    def place_order(self, symbol, side, order_type, quantity=None, **kw):
        if kw.get("reduce_only"):
            self.close_calls += 1
            self.position_amt = 0.0
            return {"orderId": 9, "status": "FILLED"}
        self.position_amt = quantity if side == "BUY" else -quantity
        self._entry = {"orderId": 1, "status": "FILLED", "executedQty": str(quantity),
                       "avgPrice": "100", "clientOrderId": kw.get("new_client_order_id")}
        return dict(self._entry)

    def query_order(self, symbol, order_id=None, client_order_id=None):
        return dict(self._entry)

    def place_algo_order(self, symbol, side, order_type, trigger_price, **kw):
        time.sleep(0.6)
        order = {"algoId": len(self.algos) + 10, "type": order_type,
                 "clientAlgoId": kw.get("client_algo_id"), "triggerPrice": str(trigger_price),
                 "symbol": symbol}
        self.algos.append(order)
        return order

    def open_algo_orders(self, symbol=None):
        return list(self.algos)

    def cancel_all_orders(self, symbol):
        return {}

    def cancel_all_algo_orders(self, symbol):
        n = len(self.algos)
        self.algos = []
        return n

    def income_history(self, **kw):
        return []


def _decision():
    return RiskDecision(allowed=True, symbol="BTCUSDT", side="LONG", quantity=0.1,
                        leverage=1, stop_loss=95.0, take_profit=110.0,
                        risk_amount=10.0, notional=10.0, risk_reward=2.0, reasons=[])


def test_a1_reconciler_does_not_close_protected_in_flight(tmp_path):
    fake = _FakeBinanceRace()
    settings = Settings(trading_mode="live", binance_api_key="k", binance_api_secret="s",
                        risk_state_path=str(tmp_path / "risk.json"), protection_retries=1)
    executor = OrderExecutor(settings, fake, _FakeSymbols())
    journal = TradeJournal(str(tmp_path / "trades.jsonl"))
    executor.set_intent_hook(journal.record_intent)
    reconciler = Reconciler(settings, fake, executor, journal)

    outcome = {}

    def _execute():
        outcome["result"] = executor.execute(_decision(), reference_price=100.0)

    t = threading.Thread(target=_execute)
    t.start()
    time.sleep(0.15)  # entri terisi; pemasangan SL sedang berjalan (0,6 dtk)
    summary = reconciler.run_once()  # tidak boleh menutup posisi yang sedang dilindungi
    t.join(timeout=10)
    assert outcome["result"].state == "PROTECTED"
    assert fake.close_calls == 0, "reconciler menutup posisi sehat — race A1 masih ada"
    assert fake.position_amt != 0
    assert summary["emergency_closed"] == []


def test_a1_intent_written_before_entry(tmp_path):
    fake = _FakeBinanceRace()
    settings = Settings(trading_mode="live", binance_api_key="k", binance_api_secret="s",
                        risk_state_path=str(tmp_path / "risk.json"), protection_retries=1)
    executor = OrderExecutor(settings, fake, _FakeSymbols())
    journal = TradeJournal(str(tmp_path / "trades.jsonl"))
    executor.set_intent_hook(journal.record_intent)
    result = executor.execute(_decision(), reference_price=100.0)
    entries = journal.all_entries()
    intents = [e for e in entries if e.get("type") == "intent"]
    assert len(intents) == 1
    assert intents[0]["client_order_id"] == result.client_order_id
    assert intents[0]["stop_loss"] == 95.0
    # Registry mengenali posisi dari intent (SL diketahui sejak awal).
    registry = open_registry(journal)
    assert result.client_order_id in registry


def test_a1_grace_period_skips_recent_symbol(tmp_path):
    fake = _FakeBinanceRace()
    fake.position_amt = 0.2  # posisi tanpa SL, tanpa data jurnal
    settings = Settings(trading_mode="live", binance_api_key="k", binance_api_secret="s",
                        risk_state_path=str(tmp_path / "risk.json"), protection_retries=1,
                        reconciler_grace_sec=120)
    executor = OrderExecutor(settings, fake, _FakeSymbols())
    from core.executor import ExecutionResult

    recent = ExecutionResult(symbol="BTCUSDT", side="LONG", dry_run=False,
                             client_order_id="baf-baru-1")
    executor._transition(recent, "FILLED", "baru saja terisi")
    journal = TradeJournal(str(tmp_path / "trades.jsonl"))
    reconciler = Reconciler(settings, fake, executor, journal)
    summary = reconciler.run_once()
    assert summary["emergency_closed"] == []  # dalam masa tenggang → dilewati
    assert fake.close_calls == 0


# ----------------------------------------------------------------------
# A6 — DATA_DIR tunggal + penjaga keterulisan saat live
# ----------------------------------------------------------------------
def test_a6_data_dir_defaults_unify_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "datax"))
    from config.settings import load_settings

    s = load_settings()
    assert s.risk_state_path.startswith(str(tmp_path / "datax"))
    assert s.trade_journal_path.startswith(str(tmp_path / "datax"))


def test_a6_live_unwritable_data_dir_rejected(tmp_path):
    from core.selfcheck import assert_data_writable

    # Path di bawah BERKAS biasa: mkdir pasti gagal untuk user mana pun
    # (termasuk root, yang mengabaikan bit izin) — deterministik di CI.
    blocker = tmp_path / "berkas"
    blocker.write_text("x", encoding="utf-8")
    bad = str(blocker / "data")
    with pytest.raises(RuntimeError):
        assert_data_writable(Settings(trading_mode="live", data_dir=bad))
    # dry-run tidak menolak (hanya live yang wajib persisten).
    assert_data_writable(Settings(trading_mode="dry_run", data_dir=bad))


# ----------------------------------------------------------------------
# A7 — selfcheck: Hedge Mode ditolak saat live; ping bertanda tangan
# ----------------------------------------------------------------------
class _FakeBinanceAccount:
    def __init__(self, dual):
        self._dual = dual

    def server_time(self):
        return {"serverTime": int(time.time() * 1000)}

    def dual_side_position(self):
        return {"dualSidePosition": self._dual}

    def account(self):
        return {"totalWalletBalance": "5000"}


def test_a7_hedge_mode_fails_selfcheck_when_live():
    from core.selfcheck import run_selfcheck

    s = Settings(trading_mode="live", binance_api_key="k", binance_api_secret="s",
                 web_api_token="x" * 32)
    checks = run_selfcheck(s, binance=_FakeBinanceAccount(dual=True), allow_network=True)
    mode = {c.name: c for c in checks}["Mode posisi akun"]
    assert mode.ok is False
    assert "Hedge" in mode.detail


class _VersionSession:
    """v3 gagal (404) → client harus fallback ke v2."""

    def __init__(self, v3_ok):
        self.v3_ok = v3_ok
        self.urls = []

    def request(self, method, url, params=None, headers=None, timeout=None):
        self.urls.append(url)
        v3 = "/v3/" in url

        class _Resp:
            def __init__(self, ok, payload):
                self.status_code = 200 if ok else 404
                self._payload = payload

            def json(self):
                return self._payload

        if v3 and not self.v3_ok:
            return _Resp(False, {"code": -5000, "msg": "path not found"})
        if "positionRisk" in url:
            return _Resp(True, [{"symbol": "BTCUSDT", "positionAmt": "0.5"}])
        return _Resp(True, {"totalWalletBalance": "5000"})


def test_a7_v3_preferred_with_v2_fallback():
    client = BinanceFuturesClient(Settings(binance_api_key="k", binance_api_secret="s"))
    client._session = _VersionSession(v3_ok=True)
    assert client.account()["totalWalletBalance"] == "5000"
    assert any("/fapi/v3/account" in u for u in client._session.urls)
    assert client.positions()[0]["positionAmt"] == "0.5"

    client2 = BinanceFuturesClient(Settings(binance_api_key="k", binance_api_secret="s"))
    client2._session = _VersionSession(v3_ok=False)
    assert client2.account()["totalWalletBalance"] == "5000"  # fallback v2
    assert any("/fapi/v2/account" in u for u in client2._session.urls)


def test_a7_oneway_passes_and_signed_ping_ok():
    from core.selfcheck import run_selfcheck, selfcheck_failed

    s = Settings(binance_api_key="k", binance_api_secret="s")
    checks = run_selfcheck(s, binance=_FakeBinanceAccount(dual=False), allow_network=True)
    by_name = {c.name: c for c in checks}
    assert by_name["Mode posisi akun"].ok is True
    assert by_name["Konektivitas Binance"].ok is True
    assert selfcheck_failed(checks) is False


# ----------------------------------------------------------------------
# Cakupan executor (Fase 6-C2: gerbang executor ≥78%)
# ----------------------------------------------------------------------
def test_exec_find_order_not_found_returns_none(tmp_path):
    from core.binance_client import BinanceAPIError

    class _Fake:
        def query_order(self, symbol, client_order_id=None):
            raise BinanceAPIError(400, -2013, "Order does not exist")

    ex = _executor(tmp_path, _Fake())
    assert ex._find_order("BTCUSDT", "baf-x") is None

    class _Fake2:
        def query_order(self, symbol, client_order_id=None):
            raise BinanceAPIError(400, -1000, "Unknown error")

    ex2 = _executor(tmp_path, _Fake2())
    with pytest.raises(BinanceAPIError):
        ex2._find_order("BTCUSDT", "baf-x")


def test_exec_await_fill_polls_until_filled(tmp_path):
    class _Fake:
        def __init__(self):
            self.calls = 0

        def query_order(self, symbol, client_order_id=None):
            self.calls += 1
            return {"status": "FILLED", "executedQty": "0.01", "avgPrice": "100.0"}

    fake = _Fake()
    ex = _executor(tmp_path, fake)
    import core.executor as exmod

    orig_sleep = exmod.time.sleep
    exmod.time.sleep = lambda s: None
    try:
        order = ex._await_fill("BTCUSDT", {"status": "NEW"}, "baf-y")
    finally:
        exmod.time.sleep = orig_sleep
    assert order["status"] == "FILLED" and fake.calls == 1


def test_exec_mark_resolved_clears_pending(tmp_path):
    ex = _executor(tmp_path, _FakeBinanceClose())
    from core.executor import ExecutionResult, RECOVERY_REQUIRED

    rec = ExecutionResult(symbol="BTCUSDT", side="LONG", dry_run=False,
                          client_order_id="baf-pending-1")
    ex._transition(rec, RECOVERY_REQUIRED, "uji")
    assert any(r["client_order_id"] == "baf-pending-1" for r in ex.pending_recovery())
    ex.mark_resolved("baf-pending-1", "sudah ditangani")
    assert not any(r["client_order_id"] == "baf-pending-1" for r in ex.pending_recovery())


def test_exec_close_tolerates_cancel_failures(tmp_path):
    class _FakeCloseFail(_FakeBinanceClose):
        def cancel_all_orders(self, symbol):
            raise RuntimeError("cancel gagal")

        def cancel_all_algo_orders(self, symbol):
            raise RuntimeError("cancel algo gagal")

    ex = _executor(tmp_path, _FakeCloseFail())
    result = ex.close_position("BTCUSDT")
    assert result["closed"] is True  # kegagalan bersih-bersih tidak membatalkan penutupan
