"""PositionManager — manajemen keluar posisi terbuka (Fase 3.5).

Semua fitur BAWAAAN MATI dan diaktifkan satu per satu lewat .env:

- `MOVE_SL_TO_BE`       — profit ≥ BREAKEVEN_TRIGGER_R (bawaan 1R):
                          geser SL ke harga entri (break-even).
- `TRAILING_STOP_ENABLED` — profit ≥ TRAILING_TRIGGER_R (bawaan 1,5R):
                          SL mengikuti harga sejauh TRAILING_ATR_MULT×ATR.
- `PARTIAL_TP_ENABLED`  — profit ≥ PARTIAL_TP_R: tutup PARTIAL_TP_FRACTION
                          (bawaan 50%) kuantitas pada harga pasar.
- `MAX_HOLD_HOURS`      — posisi lebih tua dari ini ditutup (0 = mati).

Keamanan penggantian SL bersifat atomik-aman: SL BARU dipasang dan
DIVERIFIKASI lebih dulu; SL lama dibatalkan hanya setelah penggantinya
terbukti ada. Kegagalan di tengah jalan membiarkan SL lama utuh.

R dihitung dari entri jurnal: R = |harga referensi − SL awal|.
Status per posisi (BE sudah digeser, TP parsial sudah diambil)
disimpan di `data/position_manager_state.json` agar tidak berulang
setelah restart. Mode dry-run: hanya mencatat niat di log.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from config.settings import Settings
from core.indicators import atr
from core.reconciler import open_registry
from utils.logger import get_logger

log = get_logger(__name__)


class PositionManager:
    def __init__(self, settings: Settings, binance, executor, journal, symbols):
        self._settings = settings
        self._binance = binance
        self._executor = executor
        self._journal = journal
        self._symbols = symbols
        base = Path(settings.risk_state_path).parent
        self._state_path = base / "position_manager_state.json"

    # ------------------------------------------------------------------
    @property
    def any_enabled(self) -> bool:
        s = self._settings
        return bool(
            s.move_sl_to_be or s.trailing_stop_enabled or s.partial_tp_enabled
            or s.max_hold_hours > 0
        )

    def _load_state(self) -> dict:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: dict) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
            tmp.replace(self._state_path)
        except OSError as exc:
            log.warning("Gagal menyimpan state PositionManager: %s", exc)

    # ------------------------------------------------------------------
    def _mark_price(self, symbol: str) -> Optional[float]:
        try:
            return float(self._binance.ticker_price(symbol)["price"])
        except Exception:
            return None

    def _atr(self, symbol: str) -> Optional[float]:
        try:
            klines = self._binance.klines(symbol, interval="15m", limit=60)
            return atr(
                [float(k[2]) for k in klines],
                [float(k[3]) for k in klines],
                [float(k[4]) for k in klines],
            )
        except Exception:
            return None

    def _replace_sl(self, symbol: str, exit_side: str, new_sl: float, tag: str) -> bool:
        """Pasang SL baru terverifikasi, BARU batalkan SL lama. Aman gagal."""
        filters = self._symbols.filters(symbol)
        new_sl = filters.round_price(new_sl)
        try:
            self._binance.place_algo_order(
                symbol=symbol, side=exit_side, order_type="STOP_MARKET",
                trigger_price=new_sl, close_position=True,
                client_algo_id=f"pm-{tag}-{symbol.lower()}"[:36],
                working_type=getattr(self._settings, "protection_working_type", "MARK_PRICE"),
            )
        except Exception as exc:
            log.warning("PositionManager: gagal memasang SL baru %s: %s — SL lama dipertahankan.", symbol, exc)
            return False
        # Verifikasi pengganti benar-benar tercatat.
        try:
            open_orders = self._binance.open_algo_orders(symbol)
        except Exception as exc:
            log.warning("PositionManager: verifikasi SL baru %s gagal: %s — SL lama dipertahankan.", symbol, exc)
            return False
        new_found = any(
            (o.get("type") or o.get("orderType")) == "STOP_MARKET"
            and abs(float(o.get("triggerPrice") or o.get("stopPrice") or 0) - new_sl) < 1e-9
            for o in open_orders
        )
        if not new_found:
            log.warning("PositionManager: SL baru %s tidak terverifikasi — SL lama dipertahankan.", symbol)
            return False
        # Batalkan SL lama (yang trigger-nya berbeda dari yang baru).
        for o in open_orders:
            if (o.get("type") or o.get("orderType")) != "STOP_MARKET":
                continue
            trigger = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
            if abs(trigger - new_sl) > 1e-9 and o.get("algoId") is not None:
                try:
                    self._binance.cancel_algo_order(symbol, algo_id=o["algoId"])
                except Exception as exc:
                    log.warning("PositionManager: gagal membatalkan SL lama %s: %s", symbol, exc)
        return True

    # ------------------------------------------------------------------
    def manage(self) -> dict[str, Any]:
        """Satu putaran manajemen keluar untuk semua posisi dari jurnal."""
        summary: dict[str, list] = {"breakeven": [], "trailing": [], "partial_tp": [], "max_hold": []}
        if not self.any_enabled:
            return summary
        s = self._settings
        dry = not s.is_live
        try:
            positions = {
                p["symbol"]: p for p in self._binance.positions()
                if float(p.get("positionAmt", 0)) != 0
            }
        except Exception as exc:
            log.warning("PositionManager dilewati: posisi tidak terbaca (%s)", exc)
            return summary
        state = self._load_state()
        changed = False
        now_ms = int(time.time() * 1000)

        for cid, entry in open_registry(self._journal).items():
            symbol = entry["symbol"]
            pos = positions.get(symbol)
            if pos is None:
                continue
            flags = state.setdefault(cid, {})
            amt = float(pos["positionAmt"])
            side = "LONG" if amt > 0 else "SHORT"
            exit_side = "SELL" if amt > 0 else "BUY"
            entry_price = float(entry.get("reference_price") or pos.get("entryPrice") or 0)
            initial_sl = entry.get("stop_loss")
            if not initial_sl or entry_price <= 0:
                continue
            r_unit = abs(entry_price - float(initial_sl))
            if r_unit <= 0:
                continue
            mark = self._mark_price(symbol)
            if mark is None:
                continue
            profit_r = ((mark - entry_price) if side == "LONG" else (entry_price - mark)) / r_unit
            opened_ms = int(entry.get("time", 0))

            # --- batas waktu posisi ---
            if s.max_hold_hours > 0 and opened_ms and (now_ms - opened_ms) > s.max_hold_hours * 3600 * 1000:
                if dry:
                    log.info("[DRY-RUN] PositionManager: %s melewati MAX_HOLD_HOURS — akan ditutup.", symbol)
                else:
                    log.warning("PositionManager: %s ditutup (MAX_HOLD_HOURS %.1f jam).", symbol, s.max_hold_hours)
                    self._executor.close_position(symbol)
                    summary["max_hold"].append(symbol)
                continue

            # --- TP parsial ---
            if s.partial_tp_enabled and not flags.get("partial_done") and profit_r >= float(s.partial_tp_r):
                qty = abs(amt) * float(s.partial_tp_fraction)
                qty = self._symbols.filters(symbol).round_quantity(qty)
                if qty > 0:
                    if dry:
                        log.info("[DRY-RUN] PositionManager: TP parsial %s %.4f @ %.2fR.", symbol, qty, profit_r)
                    else:
                        try:
                            self._binance.place_order(
                                symbol=symbol, side=exit_side, order_type="MARKET",
                                quantity=qty, reduce_only=True,
                            )
                            flags["partial_done"] = True
                            changed = True
                            summary["partial_tp"].append(symbol)
                            log.info("PositionManager: TP parsial %s %.4f terkirim (%.2fR).", symbol, qty, profit_r)
                        except Exception as exc:
                            log.warning("PositionManager: TP parsial %s gagal: %s", symbol, exc)

            # --- trailing stop (mendahului BE: target SL-nya lebih agresif) ---
            acted = False
            if s.trailing_stop_enabled and profit_r >= float(s.trailing_trigger_r):
                atr_v = self._atr(symbol)
                if atr_v:
                    if side == "LONG":
                        new_sl = mark - float(s.trailing_atr_mult) * atr_v
                        better = new_sl > float(initial_sl)
                    else:
                        new_sl = mark + float(s.trailing_atr_mult) * atr_v
                        better = new_sl < float(initial_sl)
                    current_best = float(flags.get("best_sl") or initial_sl)
                    if side == "LONG":
                        better = better and new_sl > current_best
                    else:
                        better = better and new_sl < current_best
                    if better:
                        if dry:
                            log.info("[DRY-RUN] PositionManager: trailing SL %s → %.6f (%.2fR).", symbol, new_sl, profit_r)
                        elif self._replace_sl(symbol, exit_side, new_sl, "trail"):
                            flags["best_sl"] = new_sl
                            changed = True
                            acted = True
                            summary["trailing"].append(symbol)

            # --- break-even ---
            if (
                s.move_sl_to_be and not acted and not flags.get("be_done")
                and profit_r >= float(s.breakeven_trigger_r)
            ):
                if dry:
                    log.info("[DRY-RUN] PositionManager: SL %s akan digeser ke break-even %.6f.", symbol, entry_price)
                elif self._replace_sl(symbol, exit_side, entry_price, "be"):
                    flags["be_done"] = True
                    flags["best_sl"] = entry_price
                    changed = True
                    summary["breakeven"].append(symbol)
                    log.info("PositionManager: SL %s digeser ke break-even.", symbol)

        if changed:
            self._save_state(state)
        return summary
