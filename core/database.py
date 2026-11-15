"""Lapisan database opsional — Supabase (PostgreSQL) via DATABASE_URL.

Peran modul ini adalah CERMIN (mirror) data operasional ke database
terpusat agar mudah dianalisis/dilihat dari mana pun:

- `trade_journal`   — satu baris per eksekusi (sama dengan logs/trades.jsonl)
- `execution_events`— satu baris per transisi status executor
                      (sama dengan data/execution_state.jsonl)

Prinsip keselamatan:
- JSONL lokal TETAP menjadi sumber kebenaran utama. Bila DATABASE_URL
  kosong, modul ini nonaktif dan tidak ada perilaku yang berubah.
- Penulisan TIDAK PERNAH sinkron di jalur trading: pemanggil hanya
  memasukkan catatan ke antrean; satu thread pekerja daemon yang
  menulis ke database. Urutan di jalur kritis selalu: pasang SL dulu,
  catat belakangan. Database yang lambat/menggantung tidak menambah
  latensi eksekusi sama sekali.
- Circuit breaker: setelah 3 kegagalan beruntun, penulisan dijeda
  (cooldown 60 detik) alih-alih mencoba koneksi ulang di setiap event.
- Koneksi dipakai bergantian lewat satu lock (thread-safety).
- Kredensial hanya dari environment (DATABASE_URL) — tidak pernah
  dicatat ke log dan tidak pernah tampil di ringkasan publik.
- Semua query memakai parameter (tanpa interpolasi string nilai).

Supabase adalah PostgreSQL: gunakan connection string dari
Dashboard Supabase → Project Settings → Database → Connection string.
Driver: psycopg v3 (diimpor malas — hanya saat DATABASE_URL terisi).
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Optional

from config.settings import Settings
from utils.logger import get_logger

log = get_logger(__name__)

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS trade_journal (
        id BIGSERIAL PRIMARY KEY,
        ts_ms BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        symbol TEXT NOT NULL,
        side TEXT,
        record JSONB NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trade_journal_symbol_ts ON trade_journal (symbol, ts_ms)",
    """
    CREATE TABLE IF NOT EXISTS execution_events (
        id BIGSERIAL PRIMARY KEY,
        ts_ms BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        symbol TEXT NOT NULL,
        client_order_id TEXT,
        state TEXT NOT NULL,
        record JSONB NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_execution_events_cid ON execution_events (client_order_id)",
)

_FAILURE_THRESHOLD = 3
_COOLDOWN_SEC = 60.0


class Database:
    """Antrean tulis asinkron ke PostgreSQL (Supabase). Aman tanpa URL."""

    def __init__(self, settings: Settings, connect_fn=None):
        self._url = (settings.database_url or "").strip()
        self._connect_fn = connect_fn  # dapat disuntikkan untuk pengujian
        self._conn = None
        self._schema_ready = False
        self._conn_lock = threading.Lock()
        self._queue: "queue.Queue[tuple[str, tuple]]" = queue.Queue(maxsize=1000)
        self._worker: Optional[threading.Thread] = None
        self._failures = 0
        self._cooldown_until = 0.0
        self._dropped = 0

    @property
    def enabled(self) -> bool:
        return bool(self._url)

    # ------------------------------------------------------------------
    # Sisi pemanggil (jalur trading) — hanya memasukkan ke antrean.
    # ------------------------------------------------------------------
    def insert_journal(self, record: dict[str, Any]) -> bool:
        return self._enqueue(
            "INSERT INTO trade_journal (ts_ms, symbol, side, record) VALUES (%s, %s, %s, %s)",
            (
                int(record.get("ts") or record.get("time") or 0),
                str(record.get("symbol", "")),
                record.get("side"),
                json.dumps(record, ensure_ascii=False),
            ),
        )

    def insert_execution_event(self, record: dict[str, Any]) -> bool:
        return self._enqueue(
            "INSERT INTO execution_events (ts_ms, symbol, client_order_id, state, record) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                int(record.get("ts") or record.get("time") or 0),
                str(record.get("symbol", "")),
                record.get("client_order_id"),
                str(record.get("state", "")),
                json.dumps(record, ensure_ascii=False),
            ),
        )

    def _enqueue(self, sql: str, params: tuple) -> bool:
        if not self.enabled:
            return False
        self._ensure_worker()
        try:
            self._queue.put_nowait((sql, params))
            return True
        except queue.Full:
            self._dropped += 1
            log.warning("Antrean database penuh — catatan cermin dibuang (JSONL tetap utama).")
            return False

    def flush(self) -> None:
        """Tunggu antrean kosong. Untuk pengujian & shutdown rapi."""
        self._queue.join()

    # ------------------------------------------------------------------
    # Thread pekerja — satu-satunya yang menyentuh koneksi untuk tulis.
    # ------------------------------------------------------------------
    def _ensure_worker(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="db-writer", daemon=True)
            self._worker.start()

    def _work(self) -> None:
        while True:
            sql, params = self._queue.get()
            try:
                self._write(sql, params)
            finally:
                self._queue.task_done()

    def _write(self, sql: str, params: tuple) -> None:
        if time.time() < self._cooldown_until:
            self._dropped += 1
            return  # circuit breaker terbuka: jangan sentuh jaringan
        try:
            conn = self._connection()
            with self._conn_lock:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
            self._failures = 0
        except Exception as exc:  # DB tidak boleh menjatuhkan jalur trading
            self._failures += 1
            log.warning(
                "Tulis database gagal %d× (diabaikan, JSONL tetap utama): %s",
                self._failures, str(exc)[:200],
            )
            with self._conn_lock:
                self._conn = None  # paksa reconnect pada percobaan berikutnya
            if self._failures >= _FAILURE_THRESHOLD:
                self._cooldown_until = time.time() + _COOLDOWN_SEC
                log.warning(
                    "Circuit breaker database TERBUKA — penulisan dijeda %.0f detik.",
                    _COOLDOWN_SEC,
                )

    def _connection(self):
        with self._conn_lock:
            if self._conn is not None:
                return self._conn
            if self._connect_fn is not None:
                self._conn = self._connect_fn(self._url)
            else:
                import psycopg  # impor malas: hanya dibutuhkan saat DB aktif

                self._conn = psycopg.connect(self._url, autocommit=True, connect_timeout=10)
            if not self._schema_ready:
                with self._conn.cursor() as cur:
                    for stmt in _SCHEMA:
                        cur.execute(stmt)
                self._schema_ready = True
                log.info("Database (Supabase/PostgreSQL): terhubung, skema siap.")
            return self._conn

    # ------------------------------------------------------------------
    def health(self) -> dict:
        if not self._url:
            return {"configured": False, "ok": None}
        if time.time() < self._cooldown_until:
            return {"configured": True, "ok": False, "error": "circuit breaker terbuka (cooldown)"}
        try:
            conn = self._connection()
            with self._conn_lock:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return {"configured": True, "ok": True, "dropped": self._dropped}
        except Exception as exc:
            return {"configured": True, "ok": False, "error": str(exc)[:160]}


def build_database(settings: Settings) -> Optional[Database]:
    """Bangun Database bila DATABASE_URL terisi; selain itu None."""
    if (settings.database_url or "").strip():
        return Database(settings)
    return None
