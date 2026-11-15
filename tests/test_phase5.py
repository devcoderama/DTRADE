"""Uji Fase 5: selfcheck + celah cakupan kritis risk/executor
(penjaga funding, filter BTC, emergency close vs avgPrice)."""


from config.settings import Settings
from core.risk import RiskManager
from core.selfcheck import run_selfcheck, selfcheck_failed
from core.strategy import TradeSignal


# ----------------------------------------------------------------------
# Selfcheck
# ----------------------------------------------------------------------
def test_selfcheck_dry_run_without_keys(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    checks = run_selfcheck(Settings(trading_mode="dry_run"))
    by_name = {c.name: c for c in checks}
    assert by_name["Konfigurasi"].ok is True
    assert by_name["Mode trading"].ok is True
    assert by_name["Kunci Binance"].ok is False  # kosong → gagal, jujur
    assert by_name["Database"].ok is None       # tidak diatur → dilewati
    assert by_name["Konektivitas Binance"].ok is None
    assert selfcheck_failed(checks) is True


def test_selfcheck_live_without_token_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    checks = run_selfcheck(Settings(
        trading_mode="live", binance_api_key="k", binance_api_secret="s"))
    mode = {c.name: c for c in checks}["Mode trading"]
    assert mode.ok is False
    assert "MENOLAK" in mode.detail


def test_selfcheck_all_green(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    class _DB:
        def health(self):
            return {"ok": True}

    class _AI:
        name, model = "fake", "m1"

        def is_configured(self):
            return True

    checks = run_selfcheck(
        Settings(binance_api_key="k", binance_api_secret="s"),
        database=_DB(), ai=_AI())
    assert selfcheck_failed(checks) is False


# ----------------------------------------------------------------------
# Risk: penjaga funding & filter BTC (celah cakupan Fase 3.6)
# ----------------------------------------------------------------------
class _FakeSymbols:
    def is_tradable(self, symbol):
        return True

    def filters(self, symbol):
        from core.symbols import SymbolFilters

        return SymbolFilters(symbol=symbol, quantity_step=0.001, quantity_min=0.001,
                             quantity_max=1e9, price_tick=0.1, min_notional=5.0)


class _FakeBinanceRisk:
    def __init__(self, btc_closes=None):
        self._btc = btc_closes

    def account(self):
        return {"totalWalletBalance": "10000", "totalUnrealizedProfit": "0"}

    def positions(self, symbol=None):
        return []

    def open_algo_orders(self, symbol=None):
        return []

    def klines(self, symbol, interval="15m", limit=200):
        closes = self._btc or [100.0] * 120
        return [[i * 900_000, str(c), str(c), str(c), str(c), "1"] for i, c in enumerate(closes)]


def _signal(side="LONG", symbol="ETHUSDT", **snap_kw):
    snap = {
        "symbol": symbol, "price": 100.0, "atr14": 2.0, "funding_rate": 0.0,
        "volume_24h_quote": 50_000_000.0, "regime": "bullish_trending", "adx14": 30.0,
    }
    snap.update(snap_kw)
    return TradeSignal(symbol=symbol, side=side, confidence=80, price=100.0,
                       stop_loss=95.0 if side == "LONG" else 105.0,
                       take_profit=110.0 if side == "LONG" else 90.0,
                       technical_score=60, snapshot=snap)


def test_funding_guard_blocks_extreme_near_boundary():
    # Penjaga 720 menit selalu aktif (jarak maks ke batas funding = 240 mnt).
    rm = RiskManager(Settings(funding_guard_minutes=720, funding_extreme_pct=0.1),
                     _FakeBinanceRisk(), _FakeSymbols())
    decision = rm.evaluate(_signal(funding_rate=0.002))  # 0,2% ≥ ekstrem 0,1%
    assert decision.allowed is False
    assert any("Funding ekstrem" in r for r in decision.reasons)


def test_btc_filter_blocks_alt_long_when_btc_strong_down():
    down = [200.0 - i * 0.8 for i in range(120)]  # turun tegas: close < EMA20 < EMA50
    rm = RiskManager(Settings(btc_filter=True), _FakeBinanceRisk(down), _FakeSymbols())
    decision = rm.evaluate(_signal(symbol="ETHUSDT", side="LONG"))
    assert decision.allowed is False
    assert any("BTC_FILTER" in r for r in decision.reasons)
    # BTC sendiri tidak disaring oleh filternya sendiri.
    rm2 = RiskManager(Settings(btc_filter=True), _FakeBinanceRisk(down), _FakeSymbols())
    assert rm2.evaluate(_signal(symbol="BTCUSDT", side="LONG")).allowed is True


# ----------------------------------------------------------------------
# Executor: avgPrice membuat SL/TP tidak valid → emergency close
# ----------------------------------------------------------------------
def test_executor_emergency_close_when_avgprice_invalidates_sltp(tmp_path):
    from core.binance_client import BinanceAPIError
    from core.executor import OrderExecutor
    from core.risk import RiskDecision

    class _FakeBinanceEx:
        """Fill terjadi di avgPrice 90 — di bawah SL 95 untuk LONG acuan 100."""

        def __init__(self):
            self.position_amt = 0.0
            self._entry_order = None

        def change_leverage(self, symbol, leverage):
            return {}

        def place_order(self, symbol, side, order_type, quantity=None, price=None,
                        stop_price=None, time_in_force="GTC", reduce_only=False,
                        close_position=False, new_client_order_id=None):
            if order_type == "MARKET" and not reduce_only:
                self.position_amt = quantity if side == "BUY" else -quantity
                self._entry_order = {
                    "orderId": 1, "status": "FILLED", "executedQty": str(quantity),
                    "avgPrice": "90", "clientOrderId": new_client_order_id,
                }
                return dict(self._entry_order)
            if order_type == "MARKET" and reduce_only:
                self.position_amt = 0.0
                return {"orderId": 99, "status": "FILLED"}
            raise AssertionError(f"order tak terduga: {order_type}")

        def query_order(self, symbol, order_id=None, client_order_id=None):
            if self._entry_order and client_order_id == self._entry_order["clientOrderId"]:
                return dict(self._entry_order)
            raise BinanceAPIError(400, -2013, "Order does not exist")

        def positions(self, symbol=None):
            if self.position_amt == 0:
                return []
            return [{"symbol": symbol or "BTCUSDT", "positionAmt": str(self.position_amt)}]

        def place_algo_order(self, **kw):
            raise AssertionError("proteksi tidak boleh dipasang saat SL/TP invalid")

        def open_algo_orders(self, symbol=None):
            return []

        def cancel_all_orders(self, symbol):
            return {}

        def cancel_all_algo_orders(self, symbol):
            return 0

    fake = _FakeBinanceEx()
    settings = Settings(trading_mode="live", binance_api_key="k", binance_api_secret="s",
                        risk_state_path=str(tmp_path / "risk.json"), protection_retries=1)
    executor = OrderExecutor(settings, fake, _FakeSymbols())
    decision = RiskDecision(
        allowed=True, symbol="BTCUSDT", side="LONG", quantity=0.1, leverage=1,
        stop_loss=95.0, take_profit=110.0, risk_amount=10.0,
        notional=10.0, risk_reward=2.0, reasons=[],
    )
    result = executor.execute(decision, reference_price=100.0)
    assert result.state == "CLOSED"
    assert any("tidak valid" in n for n in result.notes)
