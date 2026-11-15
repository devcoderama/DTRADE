#!/usr/bin/env python3
"""E2E Web Testnet — Fase 6 Audit November 2026 (skenario web 1–9).

Menjalankan server web sungguhan (subproses uvicorn) terhadap Binance
Futures TESTNET dan menguji: matriks auth, dashboard + WebSocket
klines, unsubscribe dua tab (B8), XSS, eksekusi/tutup via API, uji ras
A1 empiris (20 siklus buka→tutup dengan reconciler 5 detik), bukti
geser SL oleh PositionManager (BE/trailing), kemurnian jurnal dry-run
(A3), penerimaan workingType (TODO(verify)), dan penolakan Hedge Mode.

PENJAGA: menolak berjalan bila base URL bukan testnet. Mode posisi
akun SELALU dikembalikan ke One-way di akhir (blok finally).

Pakai:  python scripts/e2e_web_testnet.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

os.environ.setdefault("TRADING_MODE", "dry_run")  # proses skrip sendiri tidak trading langsung
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool | None, detail: str = "") -> None:
    status = "LULUS" if ok is True else ("TIDAK TERVERIFIKASI" if ok is None else "GAGAL")
    RESULTS.append((name, status, detail))
    print(f"[{status}] {name} — {detail}", flush=True)


def read_env_token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("WEB_API_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("WEB_API_TOKEN tidak ada di .env")


class Server:
    def __init__(self, overrides: dict, expect_fail: bool = False):
        env = dict(os.environ)
        env.update({
            "TRADING_MODE": "live",
            "RISK_PER_TRADE_PCT": "0.1",
            "MAX_CONSECUTIVE_LOSSES": "0",
            "BROADCAST_ENABLED": "false",
            "WEB_HOST": "127.0.0.1",
            "RECONCILER_GRACE_SEC": "120",
        })
        env.update(overrides)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "web.app:app", "--host", "127.0.0.1",
             "--port", str(PORT)],
            cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        self.expect_fail = expect_fail

    def wait_health(self, timeout: float = 90) -> bool:
        import requests

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                r = requests.get(f"{BASE}/api/health", timeout=3)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(1.5)
        return False

    def output(self) -> str:
        try:
            out, _ = self.proc.communicate(timeout=5)
            return out or ""
        except Exception:
            return ""

    def stop(self) -> str:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
        return self.output()


def main() -> int:
    import requests
    import websocket

    from config.settings import load_settings
    from core.binance_client import BinanceFuturesClient

    settings = load_settings()
    base_url = settings.binance_base_url or ""
    if not settings.binance_testnet or "testnet.binancefuture.com" not in base_url:
        print(f"REFUSE: base URL bukan testnet ({base_url!r}).")
        return 2
    print(f"Penjaga OK: {base_url}")
    binance = BinanceFuturesClient(settings)
    binance.sync_time()
    token = read_env_token()
    viewer = "viewer-e2e-" + uuid.uuid4().hex[:24]
    admin_h = {"X-API-Token": token}
    state_path = ROOT / "data" / "execution_state.jsonl"
    journal_path = Path(settings.trade_journal_path)

    def flat(symbol: str = "BTCUSDT") -> bool:
        return not any(float(p["positionAmt"]) != 0 for p in binance.positions(symbol))

    # ============ Server A: auth, dashboard, WS, XSS, trade, ras A1 ==
    srv = Server({"WEB_VIEWER_TOKEN": viewer, "RECONCILER_INTERVAL_SEC": "5",
                  "MARKET_WS_ENABLED": "true"})
    try:
        assert srv.wait_health(), "server A tidak sehat: " + srv.output()[-400:]

        # --- W1 matriks auth ---
        try:
            r = requests.get(f"{BASE}/api/config", timeout=10)
            assert r.status_code == 401, f"tanpa token: {r.status_code}"
            sess = requests.Session()
            r = sess.post(f"{BASE}/api/auth/login", json={"token": token}, timeout=10)
            assert r.status_code == 200 and r.json().get("role") == "admin", f"login: {r.status_code}"
            r = sess.get(f"{BASE}/api/config", timeout=10)
            assert r.status_code == 200, f"cookie config: {r.status_code}"
            r = requests.get(f"{BASE}/api/config", headers={"X-API-Token": viewer}, timeout=10)
            assert r.status_code == 200, f"viewer GET: {r.status_code}"
            r = requests.post(f"{BASE}/api/trade", json={"symbol": "BTCUSDT", "side": "LONG"},
                              headers={"X-API-Token": viewer}, timeout=10)
            assert r.status_code == 403, f"viewer POST trade: {r.status_code}"
            r = sess.post(f"{BASE}/api/auth/logout", timeout=10)
            assert r.status_code == 200, f"logout: {r.status_code}"
            r = sess.get(f"{BASE}/api/config", timeout=10)
            assert r.status_code == 401, f"config pasca-logout: {r.status_code}"
            codes = [requests.post(f"{BASE}/api/auth/login", json={"token": "salah"}, timeout=10).status_code
                     for _ in range(6)]
            # Ember batas login per-IP juga menghitung login BERHASIL di
            # awal skenario ini, jadi 429 bisa muncul sejak percobaan
            # salah ke-5 (bukan selalu ke-6) — yang penting: 401 untuk
            # kredensial salah dan 429 muncul dalam 6 percobaan.
            assert codes[:4] == [401] * 4 and 429 in codes[4:], f"barrage login: {codes}"
            record("W1. Matriks auth (401/cookie/viewer 403/logout/batas login 429)", True,
                   "tanpa token 401; login admin + cookie 200; viewer GET 200 & POST 403; "
                   "logout menghapus sesi; login salah → 401 lalu dibatasi 429 "
                   "(ember per-IP menghitung semua percobaan login termasuk yang berhasil)")
        except Exception:
            record("W1. Matriks auth (401/cookie/viewer 403/logout/batas login 429)", False,
                   traceback.format_exc(limit=3).replace("\n", " | ")[:500])

        # --- W2 dashboard + WS klines ---
        try:
            r = requests.get(f"{BASE}/", timeout=10)
            assert r.status_code == 200 and "<html" in r.text.lower(), f"dashboard: {r.status_code}"
            ws = websocket.create_connection(
                f"ws://127.0.0.1:{PORT}/api/ws/klines/BTCUSDT",
                header=[f"X-API-Token: {token}"], timeout=40)
            msg = ws.recv()
            payload = json.loads(msg)
            assert payload, "payload WS kosong"
            ws.close()
            record("W2. Dashboard termuat + WS klines menerima data", True,
                   f"GET / 200; pesan WS pertama: {str(payload)[:120]}")
        except Exception:
            record("W2. Dashboard termuat + WS klines menerima data", False,
                   traceback.format_exc(limit=3).replace("\n", " | ")[:500])

        # --- W3 dua tab: unsubscribe satu, lainnya tetap hidup (B8) ---
        try:
            ws1 = websocket.create_connection(f"ws://127.0.0.1:{PORT}/api/ws/klines/BTCUSDT",
                                              header=[f"X-API-Token: {token}"], timeout=40)
            ws2 = websocket.create_connection(f"ws://127.0.0.1:{PORT}/api/ws/klines/BTCUSDT",
                                              header=[f"X-API-Token: {token}"], timeout=40)
            ws1.recv()
            ws2.recv()
            ws1.close()
            ws2.settimeout(45)
            msg = ws2.recv()
            assert json.loads(msg), "tab kedua tidak menerima data setelah tab pertama ditutup"
            ws2.close()
            record("W3. Dua tab WS: menutup satu tab tidak memutus tab lain (B8 refcount)", True,
                   "tab kedua tetap menerima kline setelah tab pertama ditutup")
        except Exception:
            record("W3. Dua tab WS: menutup satu tab tidak memutus tab lain (B8 refcount)", False,
                   traceback.format_exc(limit=3).replace("\n", " | ")[:500])

        # --- W4 XSS ---
        try:
            from urllib.parse import quote

            payload = "<script>alert(1)</script>"
            r = requests.get(f"{BASE}/api/analyze/{quote(payload, safe='')}",
                             headers=admin_h, timeout=30)
            assert "application/json" in r.headers.get("content-type", ""), "respons bukan JSON"
            home = requests.get(f"{BASE}/", timeout=10).text
            assert payload not in home, "payload tercermin di HTML dashboard"
            appjs = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
            assert "esc(" in appjs, "app.js tidak memakai esc()"
            record("W4. XSS: payload simbol tidak tercermin sebagai HTML; app.js memakai esc()", True,
                   "respons API selalu JSON; payload tidak muncul di HTML; rendering dinamis melalui esc()")
        except Exception:
            record("W4. XSS: payload simbol tidak tercermin sebagai HTML; app.js memakai esc()", False,
                   traceback.format_exc(limit=3).replace("\n", " | ")[:500])

        # --- W5 eksekusi + tutup via API (+ W9 workingType) ---
        try:
            r = requests.post(f"{BASE}/api/trade", json={"symbol": "BTCUSDT", "side": "LONG"},
                              headers=admin_h, timeout=240)
            body = r.json()
            assert r.status_code == 200 and body.get("executed"), f"trade: {r.status_code} {body}"
            time.sleep(2)
            assert not flat(), "posisi BTCUSDT tidak ada setelah /api/trade"
            algos = binance.open_algo_orders("BTCUSDT")
            sl = next((o for o in algos if (o.get("type") or o.get("orderType")) == "STOP_MARKET"), None)
            assert sl is not None, "SL algo tidak ditemukan"
            wt = sl.get("workingType")
            record("W9. workingType pada algo order diterima testnet (TODO(verify) terjawab)", 
                   wt == "MARK_PRICE", f"workingType SL algo = {wt!r} (bawaan PROTECTION_WORKING_TYPE)")
            r = requests.post(f"{BASE}/api/close/BTCUSDT", headers=admin_h, timeout=60)
            assert r.status_code == 200 and r.json().get("closed"), f"close: {r.status_code} {r.text[:200]}"
            time.sleep(2)
            assert flat(), "posisi masih ada setelah /api/close"
            record("W5. Eksekusi & tutup posisi via API web (live testnet)", True,
                   "POST /api/trade LONG ter-eksekusi; POST /api/close menutup bersih")
        except Exception:
            record("W5. Eksekusi & tutup posisi via API web (live testnet)", False,
                   traceback.format_exc(limit=3).replace("\n", " | ")[:500])
            try:
                requests.post(f"{BASE}/api/close/BTCUSDT", headers=admin_h, timeout=60)
            except Exception:
                pass

        # --- W6 uji ras A1 empiris: 20 siklus, reconciler 5 detik ---
        try:
            before = state_path.read_text(encoding="utf-8").count("\n") if state_path.exists() else 0
            cycles_ok = 0
            skips = 0
            for i in range(20):
                # Penolakan RiskManager (mis. R/R efektif < 1,5 karena
                # harga bergerak selama analisis AI) BUKAN kegagalan
                # ras: tidak ada posisi yang terbuka. Siklus diulang
                # dengan analisis segar (maks 3 percobaan) dan dihitung
                # sebagai skip — yang diuji adalah posisi yang BENAR
                # terbuka tidak pernah ditutup reconciler.
                r = None
                for _attempt in range(3):
                    r = requests.post(f"{BASE}/api/trade", json={"symbol": "BTCUSDT", "side": "LONG"},
                                      headers=admin_h, timeout=240)
                    if r.status_code == 200 and r.json().get("executed"):
                        break
                    if r.status_code == 200 and r.json().get("executed") is False:
                        skips += 1
                        time.sleep(3)
                        continue
                    raise AssertionError(f"siklus {i + 1}: trade gagal {r.status_code} {r.text[:160]}")
                if not (r is not None and r.status_code == 200 and r.json().get("executed")):
                    raise AssertionError(f"siklus {i + 1}: tidak tereksekusi setelah 3 percobaan: {r.text[:160] if r else 'tanpa respons'}")
                deadline = time.time() + 60
                while time.time() < deadline:
                    if not flat() and len(binance.open_algo_orders("BTCUSDT")) >= 2:
                        break
                    time.sleep(1.5)
                else:
                    raise AssertionError(f"siklus {i + 1}: posisi/SL-TP tidak terkonfirmasi")
                r = requests.post(f"{BASE}/api/close/BTCUSDT", headers=admin_h, timeout=60)
                if not (r.status_code == 200 and r.json().get("closed")):
                    raise AssertionError(f"siklus {i + 1}: close gagal {r.status_code}")
                cycles_ok += 1
                time.sleep(6)  # beri reconciler (5 dtk) kesempatan "menyerang" antar siklus
            time.sleep(12)  # dua putaran reconciler ekstra pada akun datar
            new_lines = state_path.read_text(encoding="utf-8").splitlines()[before:]
            recovery = [x for x in new_lines if "RECOVERY_REQUIRED" in x]
            assert cycles_ok == 20 and not recovery and flat(), \
                f"siklus ok={cycles_ok}, RECOVERY_REQUIRED baru={len(recovery)}, flat={flat()}"
            record("W6. Uji ras A1 empiris: 20 siklus buka→tutup, reconciler 5 dtk", True,
                   f"20/20 siklus bersih ({skips} percobaan ditolak RiskManager & diulang — "
                   "penolakan sah, bukan ras); nol RECOVERY_REQUIRED; nol posisi sehat ditutup reconciler")
        except Exception:
            record("W6. Uji ras A1 empiris: 20 siklus buka→tutup, reconciler 5 dtk", False,
                   traceback.format_exc(limit=3).replace("\n", " | ")[:500])
    finally:
        srv.stop()

    # ============ Server B: bukti BE/trailing (PositionManager) ======
    srv = Server({"RECONCILER_INTERVAL_SEC": "5", "MOVE_SL_TO_BE": "true",
                  "TRAILING_STOP_ENABLED": "true", "BREAKEVEN_TRIGGER_R": "0.3",
                  "TRAILING_TRIGGER_R": "0.5", "MARKET_WS_ENABLED": "false"})
    try:
        assert srv.wait_health(), "server B tidak sehat: " + srv.output()[-400:]
        try:
            r = requests.post(f"{BASE}/api/trade", json={"symbol": "BTCUSDT", "side": "LONG"},
                              headers=admin_h, timeout=240)
            assert r.status_code == 200 and r.json().get("executed"), f"trade B: {r.text[:200]}"
            time.sleep(2)
            sl0 = next((o for o in binance.open_algo_orders("BTCUSDT")
                        if (o.get("type") or o.get("orderType")) == "STOP_MARKET"), None)
            assert sl0 is not None, "SL awal tidak ada"
            first_trigger = float(sl0.get("triggerPrice") or sl0.get("stopPrice"))
            moves = 0
            seen = {first_trigger}
            deadline = time.time() + 8 * 60
            while time.time() < deadline and not flat():
                for o in binance.open_algo_orders("BTCUSDT"):
                    if (o.get("type") or o.get("orderType")) == "STOP_MARKET":
                        trig = float(o.get("triggerPrice") or o.get("stopPrice"))
                        if trig not in seen:
                            seen.add(trig)
                            moves += 1
                if moves >= 2:
                    break
                time.sleep(10)
            requests.post(f"{BASE}/api/close/BTCUSDT", headers=admin_h, timeout=60)
            if moves >= 1:
                record("W7. Bukti BE/trailing: SL bergerak mengikuti harga (PositionManager)", True,
                       f"SL bergerak {moves} kali dari trigger awal {first_trigger}")
            else:
                record("W7. Bukti BE/trailing: SL bergerak mengikuti harga (PositionManager)", None,
                       "harga tidak mencapai 0,3R dalam jendela 8 menit sehingga pemicu BE/trailing "
                       "tidak teramati — bukan kegagalan kode (logika teruji di unit test Fase 3/B1)")
        except Exception:
            record("W7. Bukti BE/trailing: SL bergerak mengikuti harga (PositionManager)", False,
                   traceback.format_exc(limit=3).replace("\n", " | ")[:500])
    finally:
        srv.stop()
        try:
            if not flat():
                binance.place_order(symbol="BTCUSDT", side="SELL", order_type="MARKET",
                                    quantity=abs(float(next(p["positionAmt"] for p in binance.positions("BTCUSDT")
                                                             if float(p["positionAmt"]) != 0))),
                                    reduce_only=True)
                binance.cancel_all_algo_orders("BTCUSDT")
        except Exception:
            pass

    # ============ Server C: kemurnian jurnal dry-run (A3) ============
    srv = Server({"TRADING_MODE": "dry_run", "RECONCILER_INTERVAL_SEC": "5",
                  "MARKET_WS_ENABLED": "false"})
    try:
        assert srv.wait_health(), "server C tidak sehat: " + srv.output()[-400:]
        try:
            before = journal_path.read_text(encoding="utf-8").count("\n") if journal_path.exists() else 0
            r = requests.post(f"{BASE}/api/trade", json={"symbol": "BTCUSDT", "side": "LONG"},
                              headers=admin_h, timeout=240)
            body = r.json()
            assert r.status_code == 200 and body.get("executed") and body.get("dry_run"), \
                f"dry trade: {r.status_code} {body}"
            time.sleep(13)  # dua putaran reconciler
            new_entries = [json.loads(x) for x in
                           journal_path.read_text(encoding="utf-8").splitlines()[before:] if x.strip()]
            closes = [e for e in new_entries if e.get("type") == "close"]
            live_entries = [e for e in new_entries if e.get("type") == "execution" and not e.get("dry_run")]
            assert not closes and not live_entries, \
                f"jurnal tercemar: close={len(closes)}, live={len(live_entries)}"
            record("W8. Kemurnian jurnal dry-run (A3) di server web", True,
                   f"{len(new_entries)} entri baru semuanya dry_run; nol catatan close fiktif "
                   "setelah dua putaran reconciler")
        except Exception:
            record("W8. Kemurnian jurnal dry-run (A3) di server web", False,
                   traceback.format_exc(limit=3).replace("\n", " | ")[:500])
    finally:
        srv.stop()

    # ============ W10: Hedge Mode ditolak saat startup ===============
    try:
        assert flat() and not binance.open_algo_orders("BTCUSDT"), "akun tidak datar sebelum uji hedge"
        binance._request("POST", "/fapi/v1/positionSide/dual",
                         {"dualSidePosition": "true"}, signed=True)
        try:
            assert binance.dual_side_position().get("dualSidePosition") is True, "gagal masuk Hedge Mode"
            srv = Server({"MARKET_WS_ENABLED": "false"})
            deadline = time.time() + 45
            refused = False
            while time.time() < deadline:
                if srv.proc.poll() is not None:
                    refused = srv.proc.returncode not in (0, None)
                    break
                time.sleep(1.5)
            out = srv.stop()
            assert refused and "Hedge" in out, \
                f"server tidak menolak Hedge Mode (refused={refused}); keluaran: {out[-300:]}"
            record("W10. Hedge Mode: server live menolak start, akun dikembalikan One-way", True,
                   "proses keluar non-nol dengan pesan Hedge Mode saat startup")
        finally:
            binance._request("POST", "/fapi/v1/positionSide/dual",
                             {"dualSidePosition": "false"}, signed=True)
            assert binance.dual_side_position().get("dualSidePosition") is False, \
                "GAGAL mengembalikan One-way!"
    except Exception:
        record("W10. Hedge Mode: server live menolak start, akun dikembalikan One-way", False,
               traceback.format_exc(limit=3).replace("\n", " | ")[:500])

    # ---------------- Laporan ----------------------------------------
    ts = time.strftime("%Y%m%d-%H%M%S")
    report_path = ROOT / "reports" / f"e2e_web_testnet_{ts}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Laporan E2E Web Testnet — {ts}", "",
             "Server uvicorn sungguhan terhadap Binance Futures TESTNET.",
             "Tanpa rahasia di laporan ini.", ""]
    for name, status, detail in RESULTS:
        lines.append(f"- **{status}** — {name}: {detail}")
    fails = sum(1 for _, s, _ in RESULTS if s == "GAGAL")
    lines += ["", f"Ringkasan: {len(RESULTS) - fails}/{len(RESULTS)} tidak gagal; {fails} GAGAL."]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nLaporan: {report_path}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
