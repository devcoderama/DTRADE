"""Uji PIN aplikasi (web/pin_store.py) + login PIN di API.

PIN menggantikan token panjang sebagai cara utama membuka Web UI: dibuat
otomatis saat server pertama berjalan, hash PBKDF2-nya tersimpan di SQLite
<DATA_DIR>/app.db, dan login menerbitkan ID sesi (bukan PIN) sebagai cookie.
"""

import pytest
from fastapi.testclient import TestClient

from web import pin_store


@pytest.fixture()
def pin_db(tmp_path):
    old = pin_store._db_path
    pin_store.configure(tmp_path / "app.db")
    yield tmp_path / "app.db"
    if old is not None:
        pin_store.configure(old)
    pin_store.invalidate_cache()


def test_pin_stored_as_hash_not_plaintext(pin_db):
    pin_store.set_pin("135790")
    record = pin_store.get_pin_record()
    assert record is not None
    assert record.startswith("pbkdf2$")
    assert "135790" not in record


def test_verify_pin(pin_db):
    pin_store.set_pin("135790")
    assert pin_store.verify_pin("135790") is True
    assert pin_store.verify_pin("135791") is False
    assert pin_store.verify_pin("") is False


def test_pin_format_validation(pin_db):
    with pytest.raises(ValueError):
        pin_store.set_pin("12")  # terlalu pendek
    with pytest.raises(ValueError):
        pin_store.set_pin("12ab56")  # bukan angka semua


def test_ensure_pin_creates_once_then_stable(pin_db):
    created = pin_store.ensure_pin()
    assert created is not None and len(created) == 6 and created.isdigit()
    assert pin_store.verify_pin(created) is True
    # Panggilan berikutnya (simulasi restart) TIDAK membuat PIN baru.
    assert pin_store.ensure_pin() is None
    assert pin_store.verify_pin(created) is True


def test_ensure_pin_preset_from_env(pin_db):
    assert pin_store.ensure_pin("998877") is None  # preset tidak ditampilkan
    assert pin_store.verify_pin("998877") is True
    # Preset yang sama saat restart: tetap berlaku, tidak berubah.
    assert pin_store.ensure_pin("998877") is None
    assert pin_store.verify_pin("998877") is True


def test_generate_new_pin_replaces_old_and_revokes_sessions(pin_db):
    pin_store.set_pin("111111")
    sid = pin_store.create_session("admin")
    assert pin_store.session_role(sid) == "admin"
    new_pin = pin_store.generate_new_pin()
    assert pin_store.verify_pin(new_pin) is True
    assert pin_store.verify_pin("111111") is False
    # Sesi lama ikut dicabut saat PIN diganti.
    assert pin_store.session_role(sid) is None


def test_session_lifecycle(pin_db):
    sid = pin_store.create_session("admin")
    assert pin_store.session_role(sid) == "admin"
    assert pin_store.session_role("tidak-ada") is None
    assert pin_store.session_role("") is None
    pin_store.delete_session(sid)
    assert pin_store.session_role(sid) is None


def test_api_login_with_pin_issues_session_cookie(pin_db):
    from web.app import app

    # Impor web.app memanggil pin_store.configure() dengan DATA_DIR aplikasi —
    # arahkan kembali ke DB sementara milik test ini sebelum mengisi PIN.
    pin_store.configure(pin_db)
    pin_store.set_pin("135790")

    # Host harus literal IP (anti DNS-rebinding Fase 6-C1), sama seperti test_security.
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    ok = client.post("/api/auth/login", json={"pin": "135790"})
    assert ok.status_code == 200
    assert ok.json()["via"] == "pin"
    cookie = ok.cookies.get("baf_token")
    assert cookie and cookie != "135790"  # cookie = ID sesi, bukan PIN

    r = client.get("/api/config", cookies={"baf_token": cookie})
    assert r.status_code == 200

    # Tanpa sesi → ditolak karena PIN terdaftar = autentikasi aktif.
    # (Klien terpisah: TestClient utama menyimpan cookie login di jar-nya.)
    anon_client = TestClient(app, base_url="http://127.0.0.1:8000")
    anon = anon_client.get("/api/config")
    assert anon.status_code == 401

    # Logout mencabut sesi di server, bukan hanya menghapus cookie.
    client.post("/api/auth/logout", cookies={"baf_token": cookie})
    after = client.get("/api/config", cookies={"baf_token": cookie})
    assert after.status_code == 401
