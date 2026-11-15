"""Uji Fase 6 — Fase C1: hardening web. Tanpa jaringan eksternal."""
import os
from types import SimpleNamespace

os.environ.setdefault("WEB_API_TOKEN", "token-utama-uji-yang-cukup-panjang")
os.environ.setdefault("WEB_VIEWER_TOKEN", "token-viewer-uji")

import pytest
from fastapi.testclient import TestClient

import web.app as webapp

# CATATAN: token & klien dibaca MALAS (per uji), bukan saat impor modul.
# test_security me-reload web.app saat impornya; menangkap webapp.app /
# settings lebih awal akan mengikat instance lama yang tokennya berasal
# dari .env lokal (load_dotenv) — tidak konsisten setelah reload.
def _token():
    return webapp.settings.web_api_token


def _client():
    return TestClient(webapp.app, raise_server_exceptions=False,
                      base_url="http://127.0.0.1:8000")


def _login(client):
    r = client.post("/api/auth/login", json={"token": _token()})
    assert r.status_code == 200
    return r


def test_c1_logout_clears_cookie():
    client = _client()
    _login(client)
    assert client.get("/api/config").status_code == 200  # cookie sesi aktif
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    assert client.get("/api/config").status_code == 401  # cookie sudah dihapus


def test_c1_origin_foreign_rejected_for_cookie_posts():
    client = _client()
    _login(client)
    r = client.post(
        "/api/auth/logout", headers={"Origin": "https://evil.example.com"}
    )
    assert r.status_code == 403  # Origin asing + auth cookie → CSRF ditolak
    # Origin se-host tetap boleh.
    r2 = client.post("/api/auth/logout", headers={"Origin": "http://127.0.0.1:8000"})
    assert r2.status_code == 200


def test_c1_content_type_enforced_when_body_present():
    client = _client()
    r = client.post(
        "/api/auth/login",
        content="token=abc",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "X-API-Token": _token()},
    )
    assert r.status_code == 415


def test_c1_public_host_header_rejected_lan_allowed():
    client = _client()
    r = client.get("/api/config", headers={"Host": "evil.example.com",
                                           "X-API-Token": _token()})
    assert r.status_code == 400
    r2 = client.get("/api/config", headers={"Host": "192.168.1.20:8000",
                                            "X-API-Token": _token()})
    assert r2.status_code == 200  # IP privat LAN (kasus D TRADE) tetap boleh


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


def test_c1_ws_query_token_removed_header_still_works(monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setattr(webapp, "market_stream", _StubStream())
    client = _client()
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/ws/klines/BTCUSDT?token={_token()}") as ws:
            ws.receive_json()
    with client.websocket_connect(
        "/api/ws/klines/BTCUSDT", headers={"X-API-Token": _token()}
    ) as ws:
        payload = ws.receive_json()
        assert payload["symbol"] == "BTCUSDT"


def test_c1_xff_only_from_trusted_proxy():
    mw = webapp.SecurityMiddleware(
        webapp.app, api_token="x" * 30, viewer_token="",
        rate_per_minute=240, trusted_proxies=["10.0.0.1"],
    )
    req_trusted = SimpleNamespace(
        client=SimpleNamespace(host="10.0.0.1"),
        headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1"},
    )
    req_direct = SimpleNamespace(
        client=SimpleNamespace(host="192.0.2.5"),
        headers={"x-forwarded-for": "203.0.113.7"},
    )
    assert mw._client_ip(req_trusted) == "203.0.113.7"
    assert mw._client_ip(req_direct) == "192.0.2.5"  # XFF dari peer tak tepercaya diabaikan
