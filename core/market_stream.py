"""Data pasar real-time lewat WebSocket Binance Futures (Fase 3).

Satu koneksi combined-stream dipakai bersama untuk kline + mark price
simbol yang sedang dipantau. Arsitektur yang disengaja:

- REST tetap menjadi sumber data historis (grafik memuat riwayat lewat
  /api/klines seperti biasa) — WebSocket hanya mendorong PEMBARUAN
  candle yang sedang berjalan. Bila WS putus, UI tetap berfungsi
  dengan data REST (fallback alami, tanpa polling berlebihan).
- Reconnect otomatis dengan exponential backoff (1→2→4→…→30 dtk).
- Heartbeat ping/pong bawaan websocket-client (ping_interval).
- Deteksi data basi: `is_stale()` membandingkan waktu pesan terakhir.
- Setiap pesan dinormalisasi menjadi dict candle yang sama bentuknya
  dengan /api/klines ({time(ms), open, high, low, close, volume}).

Endpoint: testnet wss://stream.binancefuture.com,
mainnet wss://fstream.binance.com.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable, Optional

from config.settings import Settings
from utils.logger import get_logger

log = get_logger(__name__)

Listener = Callable[[str, dict], None]  # (kind, payload) — kind: "kline" | "mark_price"
_BACKOFF_START = 1.0
_BACKOFF_MAX = 30.0


def backoff_delay(attempt: int) -> float:
    """Jeda reconnect: 1, 2, 4, 8, 16, 30, 30, … detik."""
    return min(_BACKOFF_MAX, _BACKOFF_START * (2 ** max(0, attempt)))


class MarketStream:
    def __init__(self, settings: Settings, ws_factory=None):
        self._settings = settings
        self._base = (
            "wss://stream.binancefuture.com" if settings.binance_testnet
            else "wss://fstream.binance.com"
        )
        # Pabrik WebSocketApp dapat disuntikkan untuk pengujian.
        self._ws_factory = ws_factory
        self._klines_subs: set[tuple[str, str]] = set()  # (symbol, interval)
        self._refcounts: dict[tuple[str, str], int] = {}  # Fase 6-B8: pemakai per langganan
        self._mark_subs: set[str] = set()                # symbol
        self._listeners: list[Listener] = []
        self._candles: dict[tuple[str, str], dict] = {}
        self._marks: dict[str, float] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._app = None
        self._dirty = False  # langganan berubah → bangun ulang koneksi
        self._connected = False
        self._last_msg_at = 0.0
        self._reconnects = 0

    # ------------------------------------------------------------------
    # Langganan & pendengar
    # ------------------------------------------------------------------
    def subscribe_kline(self, symbol: str, interval: str) -> None:
        symbol = symbol.upper()
        with self._lock:
            # Fase 6-B8: refcount per (simbol, interval) — banyak tab UI
            # boleh berbagi satu langganan stream yang sama.
            key = (symbol, interval)
            self._refcounts[key] = self._refcounts.get(key, 0) + 1
            if key in self._klines_subs and symbol in self._mark_subs:
                return
            self._klines_subs.add(key)
            self._mark_subs.add(symbol)
            self._dirty = True
        self._ensure_running()
        self._poke()

    def unsubscribe_kline(self, symbol: str, interval: str) -> None:
        symbol = symbol.upper()
        with self._lock:
            # Fase 6-B8: langganan hanya dicabut saat pemakai TERAKHIR
            # pergi — menutup satu tab tidak menghentikan tab lainnya.
            key = (symbol, interval)
            remaining = self._refcounts.get(key, 0) - 1
            if remaining > 0:
                self._refcounts[key] = remaining
                return
            self._refcounts.pop(key, None)
            self._klines_subs.discard(key)
            if not any(s == symbol for s, _ in self._klines_subs):
                self._mark_subs.discard(symbol)
            self._dirty = True
        self._poke()

    def add_listener(self, fn: Listener) -> None:
        with self._lock:
            if fn not in self._listeners:
                self._listeners.append(fn)

    def remove_listener(self, fn: Listener) -> None:
        with self._lock:
            if fn in self._listeners:
                self._listeners.remove(fn)

    # ------------------------------------------------------------------
    # Akses data terakhir
    # ------------------------------------------------------------------
    def last_candle(self, symbol: str, interval: str) -> Optional[dict]:
        with self._lock:
            candle = self._candles.get((symbol.upper(), interval))
            return dict(candle) if candle else None

    def mark_price(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._marks.get(symbol.upper())

    def is_stale(self, max_age_sec: float = 15.0) -> bool:
        if not self._connected:
            return True
        return (time.time() - self._last_msg_at) > max_age_sec

    def health(self) -> dict:
        with self._lock:
            subs = [f"{s}@{i}" for s, i in sorted(self._klines_subs)]
        return {
            "connected": self._connected,
            "stale": self.is_stale(),
            "last_message_age_sec": (
                round(time.time() - self._last_msg_at, 1) if self._last_msg_at else None
            ),
            "reconnects": self._reconnects,
            "subscriptions": subs,
            "network": "testnet" if self._settings.binance_testnet else "mainnet",
        }

    # ------------------------------------------------------------------
    # Mesin koneksi
    # ------------------------------------------------------------------
    def _stream_url(self) -> str:
        with self._lock:
            names = [f"{s.lower()}@kline_{i}" for s, i in sorted(self._klines_subs)]
            names += [f"{s.lower()}@markPrice@1s" for s in sorted(self._mark_subs)]
        return f"{self._base}/stream?streams={'/'.join(names)}"

    def _ensure_running(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="market-stream", daemon=True)
            self._thread.start()

    def _poke(self) -> None:
        """Paksa loop membangun ulang koneksi bila langganan berubah."""
        app = self._app
        if app is not None:
            try:
                app.close()
            except Exception as exc:  # koneksi memang sedang ditutup
                log.debug("Menutup koneksi WS untuk rebuild: %s", exc)

    def start(self) -> None:
        self._ensure_running()

    def stop(self) -> None:
        self._stop.set()
        self._poke()

    def _run(self) -> None:
        import websocket  # websocket-client

        factory = self._ws_factory or websocket.WebSocketApp
        attempt = 0
        while not self._stop.is_set():
            with self._lock:
                has_subs = bool(self._klines_subs)
                self._dirty = False
            if not has_subs:
                self._connected = False
                self._stop.wait(0.5)
                continue
            url = self._stream_url()
            log.info("WS pasar: menghubungkan (%s)…", url.split("streams=")[-1][:80])
            app = factory(
                url,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
                on_open=self._on_open,
            )
            self._app = app
            try:
                app.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as exc:  # loop tidak boleh mati total
                log.warning("WS pasar berhenti dengan error: %s", exc)
            self._app = None
            self._connected = False
            if self._stop.is_set():
                break
            with self._lock:
                dirty = self._dirty
            if dirty:
                attempt = 0
                continue  # langganan berubah — langsung bangun koneksi baru
            self._reconnects += 1
            attempt += 1
            delay = backoff_delay(attempt)
            log.warning("WS pasar: reconnect ke-%d dalam %.0f dtk…", self._reconnects, delay)
            self._stop.wait(delay)

    # ------------------------------------------------------------------
    # Penanganan pesan
    # ------------------------------------------------------------------
    def _on_open(self, _app) -> None:
        self._connected = True
        self._last_msg_at = time.time()
        log.info("WS pasar: terhubung.")

    def _on_error(self, _app, error) -> None:
        log.warning("WS pasar error: %s", error)

    def _on_close(self, _app, status_code, msg) -> None:
        self._connected = False
        log.info("WS pasar: terputus (%s %s).", status_code, msg or "")

    def _on_message(self, _app, raw: str) -> None:
        self._last_msg_at = time.time()
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        data = msg.get("data") if isinstance(msg, dict) else None
        if not isinstance(data, dict):
            return
        event = data.get("e")
        if event == "kline":
            k = data.get("k") or {}
            symbol = str(k.get("s", "")).upper()
            interval = str(k.get("i", ""))
            candle = {
                "time": int(k.get("t", 0)),
                "open": float(k.get("o", 0)),
                "high": float(k.get("h", 0)),
                "low": float(k.get("l", 0)),
                "close": float(k.get("c", 0)),
                "volume": float(k.get("v", 0)),
            }
            with self._lock:
                self._candles[(symbol, interval)] = candle
                listeners = list(self._listeners)
            payload = {
                "symbol": symbol, "interval": interval,
                "candle": candle, "closed": bool(k.get("x")),
            }
            for fn in listeners:
                try:
                    fn("kline", payload)
                except Exception as exc:  # pendengar buruk tidak boleh membunuh stream
                    log.debug("Listener WS gagal: %s", exc)
        elif event == "markPriceUpdate":
            symbol = str(data.get("s", "")).upper()
            try:
                price = float(data.get("p", 0))
            except (TypeError, ValueError):
                return
            with self._lock:
                self._marks[symbol] = price
                listeners = list(self._listeners)
            for fn in listeners:
                try:
                    fn("mark_price", {"symbol": symbol, "mark_price": price})
                except Exception as exc:
                    log.debug("Listener WS gagal: %s", exc)
