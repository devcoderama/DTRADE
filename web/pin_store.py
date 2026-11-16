"""PIN aplikasi + sesi login berbasis PIN (pengganti token panjang untuk Web UI).

Desain (permintaan pemilik, 2026-11-17):
- Saat server pertama kali dijalankan, sebuah PIN 6 digit dibuat otomatis dan
  ditampilkan SEKALI di konsol. Setelah itu hanya hash-nya yang hidup di
  database SQLite ``<DATA_DIR>/app.db`` — restart server tidak membuat PIN
  baru dan PIN lama tetap berlaku.
- PIN disimpan sebagai hash PBKDF2-HMAC-SHA256 (salt acak per PIN, 200 ribu
  iterasi) — tidak pernah plaintext di disk.
- Login PIN yang berhasil menerbitkan ID sesi acak (cookie `baf_token`
  berisi ID sesi, bukan PIN dan bukan token API). Sesi berlaku 7 hari dan
  tersimpan di tabel yang sama agar ikut persisten.
- ``WEB_PIN`` di .env (opsional) memaksa PIN tertentu: hash disinkronkan
  setiap start. Reset manual: ``python run.py pin``.

Modul ini hanya memakai stdlib (sqlite3/hashlib/secrets) — tanpa dependensi
baru. Semua fungsi aman dipanggil berulang (CREATE TABLE IF NOT EXISTS).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional

_PBKDF2_ITERATIONS = 200_000
_SESSION_TTL_SECONDS = 7 * 24 * 3600
_PIN_KEY = "pin_hash"

_db_path: Optional[Path] = None
_exists_cache: tuple[float, bool] | None = None
_EXISTS_TTL = 5.0  # detik — middleware memeriksa per request; cache singkat


def configure(db_path: str | Path) -> None:
    """Tetapkan lokasi database (dipanggil web.app saat import & oleh test)."""
    global _db_path
    _db_path = Path(db_path)
    invalidate_cache()


def invalidate_cache() -> None:
    global _exists_cache
    _exists_cache = None


def _connect() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("pin_store.configure() belum dipanggil")
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path), timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "id TEXT PRIMARY KEY, role TEXT NOT NULL, created REAL NOT NULL)"
    )
    return conn


def _hash_pin(pin: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", pin.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _check_pin(pin: str, stored: str) -> bool:
    try:
        _algo, iterations, salt_hex, hash_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", pin.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


def valid_pin_format(pin: str) -> bool:
    return pin.isdigit() and 4 <= len(pin) <= 8


def get_pin_record() -> Optional[str]:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (_PIN_KEY,)
            ).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def pin_exists() -> bool:
    return get_pin_record() is not None


def pin_exists_cached() -> bool:
    global _exists_cache
    now = time.time()
    if _exists_cache and now - _exists_cache[0] < _EXISTS_TTL:
        return _exists_cache[1]
    exists = pin_exists()
    _exists_cache = (now, exists)
    return exists


def set_pin(pin: str) -> None:
    """Simpan hash PIN baru (menimpa yang lama) dan cabut semua sesi PIN."""
    if not valid_pin_format(pin):
        raise ValueError("PIN harus 4–8 digit angka")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_PIN_KEY, _hash_pin(pin)),
        )
        conn.execute("DELETE FROM sessions")
    invalidate_cache()


def verify_pin(pin: str) -> bool:
    record = get_pin_record()
    if record is None or not pin:
        return False
    return _check_pin(pin.strip(), record)


def ensure_pin(preset: str = "") -> Optional[str]:
    """Pastikan PIN ada. Kembalikan PIN plaintext HANYA bila baru dibuat.

    - ``preset`` valid (dari WEB_PIN): sinkronkan hash ke PIN itu (bila belum
      sama) dan kembalikan None — PIN tidak ditampilkan karena pemilik yang
      menentukannya sendiri di .env.
    - Tanpa preset & belum ada PIN: buat PIN 6 digit acak, simpan hash-nya,
      kembalikan plaintext agar bisa ditampilkan sekali ke pemilik.
    - Tanpa preset & PIN sudah ada: kembalikan None (PIN tetap, restart aman).
    """
    preset = (preset or "").strip()
    if preset and valid_pin_format(preset):
        record = get_pin_record()
        if record is None or not _check_pin(preset, record):
            set_pin(preset)
        return None
    if pin_exists():
        return None
    pin = f"{secrets.randbelow(1_000_000):06d}"
    set_pin(pin)
    return pin


def generate_new_pin() -> str:
    """Buat PIN acak baru (perintah ``run.py pin``); kembalikan plaintext-nya."""
    pin = f"{secrets.randbelow(1_000_000):06d}"
    set_pin(pin)
    return pin


# ---------------------------------------------------------------------------
# Sesi login PIN
# ---------------------------------------------------------------------------

def create_session(role: str = "admin") -> str:
    session_id = secrets.token_hex(32)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, role, created) VALUES (?, ?, ?)",
            (session_id, role, time.time()),
        )
        conn.execute(
            "DELETE FROM sessions WHERE created < ?",
            (time.time() - _SESSION_TTL_SECONDS,),
        )
    return session_id


def session_role(session_id: str) -> Optional[str]:
    if not session_id:
        return None
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT role, created FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    role, created = row
    if time.time() - float(created) > _SESSION_TTL_SECONDS:
        delete_session(session_id)
        return None
    return str(role)


def delete_session(session_id: str) -> None:
    if not session_id:
        return
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    except sqlite3.Error:
        pass
