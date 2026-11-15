"""Uji keamanan Web API: autentikasi token, peran viewer, CORS, rate limit.

Mengimpor web.app dengan environment khusus. Konstruksi klien di modul
itu tidak melakukan panggilan jaringan, jadi aman dijalankan offline.
"""
import importlib
import os
import sys

import pytest

os.environ["WEB_API_TOKEN"] = "token-utama-uji-yang-cukup-panjang"  # Fase 6-B5: token <24 char kini ditolak Settings
os.environ["WEB_VIEWER_TOKEN"] = "token-viewer-uji"
os.environ["BROADCAST_ENABLED"] = "false"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import web.app as webapp  # noqa: E402

importlib.reload(webapp)

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(webapp.app)


def test_health_is_public():
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_api_requires_token():
    r = client.get("/api/config")
    assert r.status_code == 401


def test_admin_token_full_access():
    r = client.get("/api/config", headers={"X-API-Token": "token-utama-uji-yang-cukup-panjang"})
    assert r.status_code == 200
    assert r.json()["web_auth_enabled"] is True


def test_viewer_cannot_write():
    r = client.post(
        "/api/notify/test", headers={"X-API-Token": "token-viewer-uji"}
    )
    assert r.status_code == 403


def test_login_sets_cookie_and_rejects_wrong_token():
    bad = client.post("/api/auth/login", json={"token": "salah"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"token": "token-utama-uji-yang-cukup-panjang"})
    assert ok.status_code == 200 and ok.json()["role"] == "admin"
    assert "baf_token" in ok.headers.get("set-cookie", "")


def test_no_wildcard_cors():
    r = client.options(
        "/api/config",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") != "*"
    assert "evil.example" not in (r.headers.get("access-control-allow-origin") or "")


def test_rate_limiter_unit():
    mw = webapp.SecurityMiddleware(
        app=lambda scope: None, api_token="x", viewer_token="", rate_per_minute=3
    )
    assert [mw._rate_limited("1.2.3.4") for _ in range(3)] == [False, False, False]
    assert mw._rate_limited("1.2.3.4") is True
    assert mw._rate_limited("5.6.7.8") is False  # IP lain tidak terpengaruh


def test_trade_request_validation():
    with pytest.raises(Exception):
        webapp.TradeRequest(symbol="BTCUSDT", side="SIDEWAYS")
    ok = webapp.TradeRequest(symbol="btcusdt", side="long")
    assert ok.symbol == "BTCUSDT" and ok.side == "LONG"


class _StubStream:
    """Pengganti MarketStream: langsung memberi satu candle ke listener."""

    def __init__(self):
        self.listeners = []

    def add_listener(self, fn):
        self.listeners.append(fn)
        fn("kline", {
            "symbol": "BTCUSDT", "interval": "15m",
            "candle": {"time": 1, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
            "closed": False,
        })

    def remove_listener(self, fn):
        self.listeners.remove(fn)

    def subscribe_kline(self, symbol, interval):
        pass

    def unsubscribe_kline(self, symbol, interval):
        pass

    def health(self):
        return {"connected": True, "stale": False, "subscriptions": ["BTCUSDT@15m"]}


def test_ws_requires_token_and_streams(monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setattr(webapp, "market_stream", _StubStream())
    client.cookies.clear()  # uji login sebelumnya menyimpan cookie sesi di client bersama
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws/klines/BTCUSDT") as ws:
            ws.receive_json()
    with client.websocket_connect("/api/ws/klines/BTCUSDT?token=token-utama-uji-yang-cukup-panjang") as ws:
        payload = ws.receive_json()
        assert payload["symbol"] == "BTCUSDT" and payload["candle"]["close"] == 1.0
