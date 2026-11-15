"""Reconciler — penjaga keselarasan posisi ↔ proteksi ↔ jurnal.

Thread latar (web server) yang tiap `RECONCILER_INTERVAL_SEC` detik:

(a) Posisi terbuka TANPA Stop Loss algo → pasang SL dari data entri
    terakhir di jurnal. Bila SL asal tidak diketahui, kebijakan amannya:
    tutup posisi (emergency) — posisi tanpa SL tidak pernah dibiarkan.
(b) Algo order yatim (simbol tanpa posisi) → dibatalkan.
(c) Posisi yang sudah tertutup (SL/TP/manual) → dicatat ke jurnal
    sebagai event penutupan beserta realized PnL dari riwayat income,
    terhubung ke `client_order_id` entri.

Registri posisi terbuka diturunkan dari jurnal (eksekusi PROTECTED
dikurangi penutupan), jadi rekonsiliator tidak menyimpan state sendiri
dan tetap benar setelah restart. Mode dry-run: HANYA mencatat niat di
log — tidak ada panggilan tulis ke bursa.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from config.settings import Settings
from utils.logger import get_logger

log = get_logger(__name__)


def open_registry(journal) -> dict[str, dict]:
    """client_order_id → entri eksekusi yang posisinya diyakini terbuka.

    Diturunkan murni dari jurnal: eksekusi PROTECTED/FILLED dikurangi
    event penutupan. Dipakai bersama oleh Reconciler & PositionManager.
    """
    registry: dict[str, dict] = {}
    for e in journal.all_entries():
        cid = e.get("client_order_id")
        if not cid:
            continue
        # Fase 6-A3: entri dry-run TIDAK PERNAH dianggap posisi terbuka —
        # menyesuaikan registry live dengan simulasi mencemari jurnal &
        # performa, dan memasang SL dari data basi berbahaya.
        if e.get("dry_run"):
            continue
        if e.get("type") == "close":
            registry.pop(cid, None)
        elif e.get("execution_state") in {"PROTECTED", "FILLED", "PARTIALLY_FILLED", "SUBMITTING"}:
            # SUBMITTING = entri write-ahead "intent" (Fase 6-A1): posisi
            # dikenali (beserta SL-nya) sejak sebelum order entri terkirim.
            registry[cid] = e
    return registry


class Reconciler:
    def __init__(self, settings: Settings, binance, executor, journal, position_manager=None, risk=None):
        self._settings = settings
        self._binance = binance
        self._executor = executor
        self._journal = journal
        self._position_manager = position_manager
        self._risk = risk  # Fase 6-B4: baseline harian dicatat di sini
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    def _open_registry(self) -> dict[str, dict]:
        return open_registry(self._journal)

    # ------------------------------------------------------------------
    def run_once(self) -> dict[str, Any]:
        """Satu putaran rekonsiliasi. Mengembalikan ringkasan tindakan.

        Fase 6-A1: SELURUH putaran berjalan di bawah lock executor —
        reconciler tidak pernah menilai/menutup posisi selagi sebuah
        eksekusi sedang memasang perlindungannya. Posisi + algo order
        dibaca ulang DI DALAM lock, jadi keputusan selalu atas keadaan
        terbaru, bukan snapshot sebelum eksekusi selesai.
        """
        lock = getattr(self._executor, "lock", None)
        if lock is None:
            return self._run_once()
        with lock:
            return self._run_once()

    def _run_once(self) -> dict[str, Any]:
        summary = {"sl_placed": [], "emergency_closed": [], "orphans_cancelled": [], "closes_recorded": []}
        # Fase 6-B4: baseline awal hari/minggu UTC dicatat oleh loop ini
        # tepat pada pergantian periode — bukan menunggu evaluasi sinyal.
        if self._risk is not None:
            try:
                self._risk.sync_daily_baselines()
            except Exception as exc:
                log.warning("Rekonsiliasi: sinkronisasi baseline gagal: %s", exc)
        dry = not self._settings.is_live
        try:
            positions = self._binance.positions()
        except Exception as exc:
            log.warning("Rekonsiliasi dilewati: posisi tidak terbaca (%s)", exc)
            return summary
        open_by_symbol = {
            p["symbol"]: p for p in positions if float(p.get("positionAmt", 0)) != 0
        }
        try:
            algo_orders = self._binance.open_algo_orders()
        except Exception as exc:
            log.warning("Rekonsiliasi dilewati: algo orders tidak terbaca (%s)", exc)
            return summary
        algo_by_symbol: dict[str, list[dict]] = {}
        for o in algo_orders:
            algo_by_symbol.setdefault(o.get("symbol", ""), []).append(o)

        registry = self._open_registry()
        registry_by_symbol = {e["symbol"]: (cid, e) for cid, e in registry.items()}

        # (a) posisi tanpa SL
        grace = float(getattr(self._settings, "reconciler_grace_sec", 120))
        recent_fn = getattr(self._executor, "recent_activity", None)
        for symbol, pos in open_by_symbol.items():
            orders = algo_by_symbol.get(symbol, [])
            has_sl = any(
                (o.get("type") or o.get("orderType")) == "STOP_MARKET" for o in orders
            )
            if has_sl:
                continue
            # Masa tenggang (Fase 6-A1): simbol dengan transisi eksekusi
            # sangat baru dilewati — perlindungannya mungkin sedang dipasang.
            if recent_fn is not None and grace > 0:
                age = recent_fn(symbol)
                if age is not None and age < grace:
                    log.info(
                        "Rekonsiliasi: %s dilewati (transisi eksekusi %.0f dtk lalu < tenggang %.0f dtk).",
                        symbol, age, grace,
                    )
                    continue
            amt = float(pos["positionAmt"])
            exit_side = "SELL" if amt > 0 else "BUY"
            known = registry_by_symbol.get(symbol)
            sl = known[1].get("stop_loss") if known else None
            if dry:
                log.info(
                    "[DRY-RUN] Rekonsiliasi: %s terbuka tanpa SL — %s.",
                    symbol,
                    f"akan memasang SL di {sl}" if sl else "akan ditutup darurat (SL asal tidak diketahui)",
                )
                continue
            if sl:
                try:
                    self._binance.place_algo_order(
                        symbol=symbol, side=exit_side, order_type="STOP_MARKET",
                        trigger_price=float(sl), close_position=True,
                        # Fase 6-B1: id unik per pemasangan (anti-duplikat).
                        client_algo_id=f"recon-{symbol.lower()}-sl-{int(time.time() * 1000):x}"[:36],
                        working_type=getattr(self._settings, "protection_working_type", "MARK_PRICE"),
                    )
                    log.warning("Rekonsiliasi: SL dipasang ulang untuk %s di %s", symbol, sl)
                    summary["sl_placed"].append(symbol)
                except Exception as exc:
                    log.error("Rekonsiliasi: gagal memasang SL %s (%s) — menutup darurat.", symbol, exc)
                    self._executor.close_position(symbol)
                    summary["emergency_closed"].append(symbol)
            else:
                log.error(
                    "Rekonsiliasi: %s terbuka tanpa SL dan tanpa data entri — menutup darurat.",
                    symbol,
                )
                self._executor.close_position(symbol)
                summary["emergency_closed"].append(symbol)

        # (b) algo yatim
        for symbol, orders in algo_by_symbol.items():
            if symbol in open_by_symbol or not orders:
                continue
            if dry:
                log.info("[DRY-RUN] Rekonsiliasi: %d algo order yatim di %s akan dibatalkan.",
                         len(orders), symbol)
                continue
            try:
                self._binance.cancel_all_algo_orders(symbol)
                log.warning("Rekonsiliasi: %d algo order yatim %s dibatalkan.", len(orders), symbol)
                summary["orphans_cancelled"].append(symbol)
            except Exception as exc:
                log.warning("Rekonsiliasi: gagal membatalkan algo yatim %s: %s", symbol, exc)

        # (c) posisi tertutup → catat ke jurnal.
        # Fase 6-A3: mode dry-run TIDAK menulis apa pun di sini — income
        # yang terbaca adalah milik akun nyata dan akan mencemari
        # performance() dengan penutupan fiktif.
        for cid, entry in (registry.items() if not dry else []):
            symbol = entry["symbol"]
            if symbol in open_by_symbol:
                continue
            pnl = self._realized_pnl_since(symbol, int(entry.get("time", 0)))
            self._journal.record_close(
                symbol=symbol,
                side=entry.get("side", ""),
                exit_price=None,
                exit_reason="reconciled",
                realized_pnl=pnl,
                client_order_id=cid,
                opened_at_ms=int(entry.get("time", 0)) or None,
                risk_amount=entry.get("risk_amount"),
            )
            log.info("Rekonsiliasi: penutupan %s dicatat (PnL %s).", symbol, pnl)
            summary["closes_recorded"].append(symbol)

        # Manajemen keluar (Fase 3.5) — fitur-fitur di dalamnya bawaan mati.
        if self._position_manager is not None:
            try:
                summary["position_manager"] = self._position_manager.manage()
            except Exception as exc:
                log.warning("PositionManager gagal pada putaran ini: %s", exc)
        return summary

    def _realized_pnl_since(self, symbol: str, since_ms: int) -> Optional[float]:
        try:
            rows = self._binance.income_history(
                limit=1000, start_time=since_ms or None, income_type="REALIZED_PNL"
            )
        except Exception as exc:
            log.warning("Rekonsiliasi: income %s tidak terbaca: %s", symbol, exc)
            return None
        total = 0.0
        found = False
        for r in rows:
            if r.get("symbol") == symbol and r.get("incomeType") == "REALIZED_PNL":
                total += float(r.get("income", 0))
                found = True
        return round(total, 4) if found else None

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.wait(float(self._settings.reconciler_interval_sec)):
            try:
                self.run_once()
            except Exception as exc:  # penjaga tidak boleh mati karena satu putaran gagal
                log.warning("Putaran rekonsiliasi gagal: %s", exc)

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            self.run_once()  # putaran pertama segera (termasuk pemulihan awal)
        except Exception as exc:
            log.warning("Rekonsiliasi awal gagal: %s", exc)
        self._thread = threading.Thread(target=self._loop, name="reconciler", daemon=True)
        self._thread.start()
        log.info("Reconciler aktif (interval %d dtk).", self._settings.reconciler_interval_sec)

    def stop(self) -> None:
        self._stop.set()
