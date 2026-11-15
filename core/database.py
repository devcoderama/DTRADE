"""Lapisan database opsional — Supabase (PostgreSQL) via DATABASE_URL.

Peran modul ini adalah CERMIN (mirror) data operasional ke database
terpusat agar mudah dianalisis/dilihat dari mana pun:

- `trade_journal`   — satu baris per eksekusi (sama dengan logs/trades.jsonl)
- `execution_events`— satu baris per transisi status executor
                      (sama dengan data/execution_state.jsonl)

Prinsip keselamatan:
- JSONL lokal TETAP menjadi sumber kebenaran utama. Bila DATABASE_URL
  kosong, modul ini nonaktif dan tidak ada perilaku yang berubah.
- Kegagalan database TIDAK PERNAH mengganggu trading: semua penulisan
  dibungkus try/except, dicatat sebagai warning, lalu diabaikan.
- Kredensial hanya dari environment (DATABASE_URL) — tidak pernah
  dicatat ke log dan tidak pernah tampil di ringkasan publik.
- Semua query memakai parameter (tanpa interpolasi string nilai).

Supabase adalah PostgreSQL: gunakan connection string dari
Dashboard Supabase → Project Settings → Database → Connection string
(mode pooler/session atau direct). Driver: psycopg v3 (diimpor malas —
hanya saat DATABASE_URL terisi).
"""

from __future__ import annotations

import json
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


class Database:
    """Koneksi malas ke PostgreSQL (Supabase). Aman dipakai tanpa URL."""

    def __init__(self, settings: Settings, connect_fn=None):
        self._url = (settings.database_url or "").strip()
        self._connect_fn = connect_fn  # dapat disuntikkan untuk pengujian
        self._conn = None
        self._schema_ready = False
        self._broken = False  # sekali gagal fatal → berhenti mencoba sesi ini

    @property
    def enabled(self) -> bool:
        return bool(self._url) and not self._broken

    def _connection(self):
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

    def _execute(self, sql: str, params: tuple) -> bool:
        if not self.enabled:
            return False
        try:
            conn = self._connection()
            with conn.cursor() as cur:
                cur.execute(sql, params)
            return True
        except Exception as exc:  # DB tidak boleh menjatuhkan jalur trading
            log.warning("Tulis database gagal (diabaikan, JSONL tetap utama): %s", str(exc)[:200])
            self._conn = None  # paksa reconnect pada percobaan berikutnya
            return False

    # ------------------------------------------------------------------
    def insert_journal(self, record: dict[str, Any]) -> bool:
        return self._execute(
            "INSERT INTO trade_journal (ts_ms, symbol, side, record) VALUES (%s, %s, %s, %s)",
            (
                int(record.get("ts") or record.get("time") or 0),
                str(record.get("symbol", "")),
                record.get("side"),
                json.dumps(record, ensure_ascii=False),
            ),
        )

    def insert_execution_event(self, record: dict[str, Any]) -> bool:
        return self._execute(
            "INSERT INTO execution_events (ts_ms, symbol, client_order_id, state, record) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                int(record.get("ts", 0)),
                str(record.get("symbol", "")),
                record.get("client_order_id"),
                str(record.get("state", "")),
                json.dumps(record, ensure_ascii=False),
            ),
        )

    def health(self) -> dict:
        if not self._url:
            return {"configured": False, "ok": None}
        if self._broken:
            return {"configured": True, "ok": False, "error": "koneksi ditandai gagal"}
        try:
            conn = self._connection()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return {"configured": True, "ok": True}
        except Exception as exc:
            return {"configured": True, "ok": False, "error": str(exc)[:160]}


def build_database(settings: Settings) -> Optional[Database]:
    """Bangun Database bila DATABASE_URL terisi; selain itu None."""
    if (settings.database_url or "").strip():
        return Database(settings)
    return None
