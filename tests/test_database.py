"""Uji lapisan database Supabase/PostgreSQL dengan koneksi palsu.

Sejak Fase 1.5 penulisan bersifat ASINKRON (antrean + thread pekerja):
pemanggil hanya memasukkan catatan ke antrean; test memakai flush()
untuk menunggu pekerja menyelesaikan tulisannya.
"""
import json

from config.settings import Settings
from core.database import Database, build_database


class _Cursor:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((sql, params))

    def fetchone(self):
        return (1,)


class _Conn:
    def __init__(self):
        self.executed = []

    def cursor(self):
        return _Cursor(self)


def _db_with_fake_conn():
    conn = _Conn()
    settings = Settings(database_url="postgresql://user:pass@db.example:5432/postgres")
    db = Database(settings, connect_fn=lambda url: conn)
    return db, conn


def test_disabled_without_url():
    db = Database(Settings(database_url=""))
    assert db.enabled is False
    assert db.insert_journal({"ts": 1, "symbol": "BTCUSDT"}) is False
    assert build_database(Settings(database_url="")) is None


def test_schema_and_journal_insert_are_parameterized():
    db, conn = _db_with_fake_conn()
    assert db.insert_journal({"time": 123, "symbol": "BTCUSDT", "side": "LONG", "quantity": 0.01}) is True
    db.flush()
    sqls = [s for s, _ in conn.executed]
    assert any("CREATE TABLE IF NOT EXISTS trade_journal" in s for s in sqls)
    insert_sql, params = conn.executed[-1]
    assert insert_sql.startswith("INSERT INTO trade_journal")
    assert params[0] == 123 and params[1] == "BTCUSDT" and params[2] == "LONG"
    assert json.loads(params[3])["quantity"] == 0.01  # nilai lewat parameter, bukan interpolasi


def test_execution_event_insert():
    db, conn = _db_with_fake_conn()
    assert db.insert_execution_event(
        {"ts": 5, "symbol": "ETHUSDT", "client_order_id": "baf-x", "state": "PROTECTED"}
    ) is True
    db.flush()
    sql, params = conn.executed[-1]
    assert "INSERT INTO execution_events" in sql
    assert params[2] == "baf-x" and params[3] == "PROTECTED"


def test_db_failure_is_swallowed_and_breaker_opens():
    def boom(url):
        raise RuntimeError("koneksi ditolak")

    settings = Settings(database_url="postgresql://u:p@db.example:5432/postgres")
    db = Database(settings, connect_fn=boom)
    # Tidak boleh melempar — jalur trading harus tetap hidup.
    assert db.insert_journal({"ts": 1, "symbol": "BTCUSDT"}) is True  # masuk antrean
    db.flush()
    health = db.health()
    assert health["configured"] is True and health["ok"] is False


def test_insert_is_non_blocking_with_slow_db():
    """DB yang menggantung tidak boleh menahan pemanggil (jalur kritis)."""
    import threading
    import time as _time

    gate = threading.Event()

    def slow_connect(url):
        gate.wait(10)  # koneksi menggantung sampai test melepaskannya
        return _Conn()

    settings = Settings(database_url="postgresql://u:p@db.example:5432/postgres")
    db = Database(settings, connect_fn=slow_connect)
    started = _time.time()
    assert db.insert_journal({"ts": 1, "symbol": "BTCUSDT"}) is True
    assert _time.time() - started < 1.0  # kembali seketika, pekerja yang menunggu
    gate.set()
    db.flush()
