"""Uji Fase 1 (keamanan & eksekusi): cache sinyal, service atomik,
redaksi log, penolakan start tak aman, batas login, retry -1021, frontend."""
import threading
import time
from pathlib import Path

import pytest

from config.settings import Settings
from core.risk import RiskDecision
from core.service import TradingService
from core.strategy import SignalEngine, TradeSignal
from utils.logger import redact

ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
# 1.6 — analyze mengembalikan salinan; cache tidak bisa diracuni
# ----------------------------------------------------------------------
def _signal(price=100.0):
    return TradeSignal(
        symbol="BTCUSDT", side="LONG", confidence=80, price=price,
        stop_loss=95.0, take_profit=110.0, technical_score=60,
        snapshot={"atr14": 2.0}, reasons=["asli"],
    )


def test_analyze_returns_copy_cache_untouched():
    engine = SignalEngine(Settings(), binance=None, ai=None)
    engine._analyze_fresh = lambda symbol, interval=None, use_ai=True: _signal()
    first = engine.analyze("BTCUSDT", use_ai=False)
    first.side = "SHORT"
    first.reasons.append("diracuni")
    first.snapshot["atr14"] = 999
    second = engine.analyze("BTCUSDT", use_ai=False)
    assert second.side == "LONG"
    assert second.reasons == ["asli"]
    assert second.snapshot["atr14"] == 2.0


def test_analyze_fresh_bypasses_cache():
    engine = SignalEngine(Settings(), binance=None, ai=None)
    calls = {"n": 0}

    def _fresh(symbol, interval=None, use_ai=True):
        calls["n"] += 1
        return _signal()

    engine._analyze_fresh = _fresh
    engine.analyze("BTCUSDT", use_ai=False)
    engine.analyze("BTCUSDT", use_ai=False)
    assert calls["n"] == 1  # panggilan kedua dari cache
    engine.analyze("BTCUSDT", use_ai=False, fresh=True)
    assert calls["n"] == 2  # fresh memaksa analisis baru


# ----------------------------------------------------------------------
# 1.6/1.7 — TradingService: deviasi harga & atomisitas
# ----------------------------------------------------------------------
class _StubRisk:
    def __init__(self, executor):
        self._executor = executor

    def evaluate(self, signal):
        time.sleep(0.05)  # lebarkan jendela balapan
        if self._executor.open_positions >= 1:
            return RiskDecision(allowed=False, symbol=signal.symbol, side=signal.side,
                                reasons=["maks posisi tercapai"])
        return RiskDecision(allowed=True, symbol=signal.symbol, side=signal.side,
                            quantity=0.01, leverage=1, stop_loss=signal.stop_loss,
                            take_profit=signal.take_profit)


class _StubExecutor:
    def __init__(self):
        self.open_positions = 0
        self.executed_prices = []

    def execute(self, decision, reference_price):
        self.open_positions += 1
        self.executed_prices.append(reference_price)
        return object()


class _StubBinance:
    def __init__(self, price):
        self._price = price

    def ticker_price(self, symbol):
        return {"price": str(self._price)}


def test_service_rejects_stale_price():
    ex = _StubExecutor()
    svc = TradingService(Settings(), _StubRisk(ex), ex, _StubBinance(110.0))
    decision, result = svc.submit(_signal(price=100.0))  # deviasi 10% > 0,3%
    assert result is None and decision.allowed is False
    assert "Harga bergerak" in decision.reasons[0]
    assert ex.open_positions == 0


def test_service_uses_current_price_within_tolerance():
    ex = _StubExecutor()
    svc = TradingService(Settings(), _StubRisk(ex), ex, _StubBinance(100.1))
    decision, result = svc.submit(_signal(price=100.0))
    assert result is not None and decision.allowed is True
    assert ex.executed_prices == [100.1]  # dinilai & dieksekusi pada harga terkini


def test_service_atomicity_parallel_submits():
    ex = _StubExecutor()
    svc = TradingService(Settings(), _StubRisk(ex), ex, _StubBinance(100.0))
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(svc.submit(_signal())))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    executed = [r for _, r in results if r is not None]
    assert len(executed) == 1  # max_open_positions=1 hanya boleh dilanggar tanpa lock
    assert ex.open_positions == 1


# ----------------------------------------------------------------------
# 1.8 — redaksi secret di log
# ----------------------------------------------------------------------
def test_redact_masks_telegram_token_and_signature():
    url = "https://api.telegram.org/bot8269245157:AAFakeTokenValue123/sendMessage"
    out = redact(url)
    assert "AAFakeTokenValue123" not in out and "/bot8269245157:***" in out
    signed = "GET /fapi/v1/order?symbol=BTCUSDT&signature=abcdef0123456789&timestamp=1"
    out2 = redact(signed)
    assert "abcdef0123456789" not in out2 and "signature=***" in out2


# ----------------------------------------------------------------------
# 1.1 — penolakan start tak aman + batas login terpisah
# ----------------------------------------------------------------------
def test_enforce_web_safety(monkeypatch):
    import web.app as webapp

    def _run(settings):
        monkeypatch.setattr(webapp, "settings", settings)
        webapp.enforce_web_safety()

    # live tanpa token → tolak walau loopback
    with pytest.raises(RuntimeError):
        _run(Settings(trading_mode="live", web_host="127.0.0.1", web_api_token=""))
    # bind publik tanpa token (dry-run) → tolak
    with pytest.raises(RuntimeError):
        _run(Settings(trading_mode="dry_run", web_host="0.0.0.0", web_api_token=""))
    # loopback + dry-run tanpa token → boleh
    _run(Settings(trading_mode="dry_run", web_host="127.0.0.1", web_api_token=""))
    # dengan token → boleh di mana pun
    _run(Settings(trading_mode="live", web_host="0.0.0.0", web_api_token="token-kuat"))
    # pengecualian eksplisit → boleh (dengan peringatan keras di log)
    _run(Settings(trading_mode="live", web_host="0.0.0.0", web_api_token="",
                  allow_insecure_web=True))


def test_login_rate_limit_is_separate_and_strict():
    from web.app import SecurityMiddleware

    async def _dummy_app(scope, receive, send):
        pass

    mw = SecurityMiddleware(_dummy_app, api_token="t", viewer_token="", rate_per_minute=240)
    results = [mw._login_limited("1.2.3.4") for _ in range(6)]
    assert results[:5] == [False] * 5 and results[5] is True  # batas login: 5/menit
    assert mw._rate_limited("1.2.3.4") is False  # kuota umum tidak ikut terkuras login


# ----------------------------------------------------------------------
# 1.9 — error -1021: sinkronkan jam lalu ulangi sekali
# ----------------------------------------------------------------------
class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload


class _Session1021:
    def __init__(self):
        self.calls = []

    def request(self, method, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        if url.endswith("/fapi/v1/time"):
            return _Resp(200, {"serverTime": 1_700_000_000_000})
        if len([c for c in self.calls if c.endswith("/fapi/v1/order")]) == 1:
            return _Resp(400, {"code": -1021, "msg": "Timestamp outside recvWindow"})
        return _Resp(200, {"orderId": 7, "status": "NEW"})


def test_minus_1021_syncs_and_retries_once():
    from core.binance_client import BinanceFuturesClient

    settings = Settings(binance_api_key="kunci", binance_api_secret="rahasia")
    client = BinanceFuturesClient(settings)
    client._session = _Session1021()
    out = client._request("GET", "/fapi/v1/order", {"symbol": "BTCUSDT"}, signed=True)
    assert out["orderId"] == 7
    assert len(client._session.calls) == 3  # order(gagal) → time → order(berhasil)


# ----------------------------------------------------------------------
# 1.2 — pemeriksaan statis frontend
# ----------------------------------------------------------------------
def test_frontend_no_localstorage_token_no_inline_onclick():
    js = (ROOT / "web/static/app.js").read_text(encoding="utf-8")
    html = (ROOT / "web/static/index.html").read_text(encoding="utf-8")
    assert "baf-api-token" not in js  # token tidak lagi disimpan di localStorage
    assert "onclick=" not in js and "onclick=" not in html
    assert "function esc(" in js  # helper escape tersedia & dipakai
