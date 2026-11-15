"""Uji MarketStream: parsing pesan, cache candle, backoff, kesehatan."""
import json
import threading

from config.settings import Settings
from core.market_stream import MarketStream, backoff_delay


def _settings():
    return Settings(binance_testnet=True)


def test_backoff_sequence():
    assert [backoff_delay(i) for i in range(7)] == [1, 2, 4, 8, 16, 30, 30]


def test_kline_message_updates_cache_and_listeners():
    ms = MarketStream(_settings())
    received = []
    ms.add_listener(lambda kind, payload: received.append((kind, payload)))
    raw = json.dumps({
        "stream": "btcusdt@kline_15m",
        "data": {
            "e": "kline",
            "k": {
                "s": "BTCUSDT", "i": "15m", "t": 1_700_000_000_000,
                "o": "100.0", "h": "101.0", "l": "99.0", "c": "100.5",
                "v": "12.5", "x": False,
            },
        },
    })
    ms._on_message(None, raw)
    candle = ms.last_candle("BTCUSDT", "15m")
    assert candle is not None
    assert candle["close"] == 100.5 and candle["time"] == 1_700_000_000_000
    assert received and received[0][0] == "kline"
    assert received[0][1]["closed"] is False


def test_mark_price_message():
    ms = MarketStream(_settings())
    raw = json.dumps({"data": {"e": "markPriceUpdate", "s": "ETHUSDT", "p": "2500.5"}})
    ms._on_message(None, raw)
    assert ms.mark_price("ETHUSDT") == 2500.5


def test_stale_detection():
    ms = MarketStream(_settings())
    assert ms.is_stale() is True  # belum terhubung sama sekali
    ms._on_open(None)
    ms._on_message(None, json.dumps({"data": {"e": "markPriceUpdate", "s": "BTCUSDT", "p": "1"}}))
    assert ms.is_stale() is False
    assert ms.health()["connected"] is True


def test_stream_url_contains_subscriptions():
    ms = MarketStream(_settings())
    ms._klines_subs.add(("BTCUSDT", "15m"))
    ms._mark_subs.add("BTCUSDT")
    url = ms._stream_url()
    assert "stream.binancefuture.com" in url  # testnet
    assert "btcusdt@kline_15m" in url and "btcusdt@markPrice@1s" in url


class _FakeApp:
    def __init__(self, url, **callbacks):
        self.url = url
        self.callbacks = callbacks
        self._closed = threading.Event()

    def run_forever(self, **kwargs):
        self._closed.wait(5)

    def close(self):
        self._closed.set()


def test_thread_lifecycle_with_fake_socket():
    made = []

    def factory(url, **callbacks):
        app = _FakeApp(url, **callbacks)
        made.append(app)
        return app

    ms = MarketStream(_settings(), ws_factory=factory)
    ms.subscribe_kline("BTCUSDT", "15m")
    # Tunggu thread membuat koneksi
    for _ in range(50):
        if made:
            break
        threading.Event().wait(0.1)
    assert made and "btcusdt@kline_15m" in made[0].url
    ms.stop()
    assert ms._thread is not None
    ms._thread.join(timeout=6)
    assert not ms._thread.is_alive()
