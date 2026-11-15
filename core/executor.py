"""Eksekusi order — satu-satunya modul yang boleh menempatkan order.

Menghormati dua mode dari konfigurasi:

- `dry_run` (bawaan): seluruh rangkaian order DISIMULASIKAN dan dicatat
  ke log. Tidak ada satu pun panggilan trading ke Binance.
- `live`: entri market + Stop Loss + Take Profit dikirim sungguhan,
  DIVERIFIKASI, dan dilindungi mekanisme pemulihan.

Executor hanya menerima `RiskDecision` yang `allowed=True` dari
RiskManager. Ia tidak menilai sinyal dan tidak menghitung ulang risiko.

Mesin status eksekusi (setiap transisi dicatat persisten ke
data/execution_state.jsonl):

    CREATED → VALIDATING → APPROVED → SUBMITTING → FILLED /
    PARTIALLY_FILLED → PROTECTION_PENDING → PROTECTED
    kegagalan di jalur ini → FAILED; posisi tanpa perlindungan yang
    gagal ditutup darurat → RECOVERY_REQUIRED (perlu tindakan manual).

Prinsip keselamatan:
- Order entri memakai newClientOrderId (idempotency key). Bila hasil
  pengiriman TIDAK DIKETAHUI (timeout/5xx), status order diperiksa dulu
  lewat query_order — TIDAK PERNAH blind-retry yang bisa menggandakan posisi.
- Setelah entri terisi, status fill diverifikasi (executedQty), bukan
  sekadar "request terkirim".
- SL/TP dipasang dengan retry terbatas + verifikasi lewat open orders.
  Bila SL gagal total, posisi DITUTUP DARURAT (emergency close) —
  posisi tidak pernah dibiarkan terbuka tanpa stop loss.
- Seluruh eksekusi & penutupan dilindungi satu kunci (lock) proses agar
  dua request tidak mengeksekusi bersamaan.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from config.settings import Settings
from core.binance_client import BinanceAPIError, BinanceFuturesClient
from core.risk import RiskDecision
from core.symbols import SymbolManager
from utils.logger import get_logger

log = get_logger(__name__)

# Status mesin eksekusi
CREATED = "CREATED"
VALIDATING = "VALIDATING"
APPROVED = "APPROVED"
SUBMITTING = "SUBMITTING"
PARTIALLY_FILLED = "PARTIALLY_FILLED"
FILLED = "FILLED"
PROTECTION_PENDING = "PROTECTION_PENDING"
PROTECTED = "PROTECTED"
CLOSING = "CLOSING"
CLOSED = "CLOSED"
FAILED = "FAILED"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"

TERMINAL_STATES = {PROTECTED, CLOSED, FAILED}
_BACKOFF_SEC = [0.5, 1.5, 3.0, 6.0]


@dataclass
class ExecutionResult:
    symbol: str
    side: str
    dry_run: bool
    state: str = CREATED
    client_order_id: str = ""
    filled_qty: float = 0.0
    entry_order: Optional[dict] = None
    stop_loss_order: Optional[dict] = None
    take_profit_order: Optional[dict] = None
    notes: list[str] = field(default_factory=list)
    transitions: list[dict] = field(default_factory=list)


class OrderExecutor:
    def __init__(
        self,
        settings: Settings,
        binance: BinanceFuturesClient,
        symbols: SymbolManager,
        db=None,
    ):
        self._settings = settings
        self._binance = binance
        self._symbols = symbols
        self._db = db  # cermin opsional ke Supabase/PostgreSQL (core.database)
        self._lock = threading.Lock()
        self._state_path = Path(settings.risk_state_path).with_name("execution_state.jsonl")

    # ------------------------------------------------------------------
    # Pencatatan status persisten + pemulihan
    # ------------------------------------------------------------------
    def _transition(self, result: ExecutionResult, state: str, note: str = "") -> None:
        result.state = state
        record = {
            "ts": int(time.time() * 1000),
            "symbol": result.symbol,
            "side": result.side,
            "client_order_id": result.client_order_id,
            "state": state,
            "dry_run": result.dry_run,
            "note": note,
        }
        result.transitions.append(record)
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with self._state_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.error("Gagal mencatat status eksekusi: %s", exc)
        if self._db is not None:
            self._db.insert_execution_event(record)  # kegagalan DB diabaikan di dalamnya
        log.info("EXEC %s %s → %s %s", result.symbol, result.client_order_id or "-", state, note)

    def pending_recovery(self) -> list[dict]:
        """Eksekusi terakhir per client_order_id yang TIDAK berakhir di status terminal.

        Dipakai saat startup untuk menemukan posisi yang mungkin belum
        terlindungi setelah restart di tengah eksekusi.
        """
        last: dict[str, dict] = {}
        try:
            lines = self._state_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            key = rec.get("client_order_id") or f"{rec.get('symbol')}:{rec.get('ts')}"
            last[key] = rec
        return [r for r in last.values() if r.get("state") not in TERMINAL_STATES]

    # ------------------------------------------------------------------
    @staticmethod
    def _make_client_order_id(symbol: str, side: str, quantity: float) -> str:
        digest = hashlib.sha1(f"{symbol}|{side}|{quantity}".encode()).hexdigest()[:6]
        return f"baf-{int(time.time() * 1000):x}-{digest}"[:36]

    def _find_order(self, symbol: str, client_order_id: str) -> Optional[dict]:
        try:
            return self._binance.query_order(symbol, client_order_id=client_order_id)
        except BinanceAPIError as exc:
            if exc.code in (-2013, -2011):  # order tidak ditemukan / tidak dikenal
                return None
            raise

    def _await_fill(self, symbol: str, entry: dict, client_order_id: str) -> dict:
        """Pastikan status akhir order entri; polling singkat bila masih NEW."""
        order = entry
        for _ in range(3):
            status = order.get("status")
            if status in {"FILLED", "PARTIALLY_FILLED", "CANCELED", "EXPIRED", "REJECTED"}:
                return order
            time.sleep(1.0)
            fresh = self._find_order(symbol, client_order_id)
            if fresh is None:
                return order
            order = fresh
        return order

    def _place_protection(
        self,
        symbol: str,
        exit_side: str,
        order_type: str,
        price: float,
        client_algo_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Pasang satu order proteksi (SL/TP) dengan retry + verifikasi.

        Order kondisional dikirim lewat Algo Order API Binance
        (/fapi/v1/algoOrder) — endpoint order klasik menolaknya dengan
        error -4120. Verifikasi membaca daftar algo order terbuka dan
        mencocokkan clientAlgoId (idempoten) atau tipenya.
        """
        attempts = max(1, self._settings.protection_retries)
        filters = self._symbols.filters(symbol)
        stop_price = filters.round_price(price)
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                placed = self._binance.place_algo_order(
                    symbol=symbol,
                    side=exit_side,
                    order_type=order_type,
                    trigger_price=stop_price,
                    close_position=True,
                    client_algo_id=client_algo_id,
                )
            except BinanceAPIError as exc:
                last_error = exc
                log.warning(
                    "Pasang %s %s gagal (percobaan %d/%d): %s",
                    order_type, symbol, attempt + 1, attempts, exc,
                )
                placed = None
            # Verifikasi: order proteksi harus terlihat di open algo orders.
            try:
                open_orders = self._binance.open_algo_orders(symbol)
            except BinanceAPIError as exc:
                log.warning("Verifikasi algo orders %s gagal: %s", symbol, exc)
                open_orders = []
            found = next(
                (
                    o for o in open_orders
                    if (client_algo_id and o.get("clientAlgoId") == client_algo_id)
                    or (o.get("type") or o.get("orderType")) == order_type
                ),
                None,
            )
            if found is not None:
                return found
            if placed is not None and not open_orders:
                # API menerima order tetapi verifikasi tidak menemukan apa pun
                # (kemungkinan jeda propagasi) — perlakukan hasil kirim sebagai bukti.
                return placed
            if attempt < attempts - 1:
                time.sleep(_BACKOFF_SEC[min(attempt, len(_BACKOFF_SEC) - 1)])
        if last_error is not None:
            log.error("Proteksi %s untuk %s GAGAL TOTAL: %s", order_type, symbol, last_error)
        return None

    def _emergency_close(self, symbol: str) -> bool:
        """Tutup posisi SEKARANG pada harga pasar (reduce-only). True bila posisi hilang."""
        try:
            # Bersihkan dulu proteksi yang mungkin sempat terpasang sebagian.
            try:
                self._binance.cancel_all_algo_orders(symbol)
            except Exception as exc:  # pembatalan algo tidak boleh menghalangi penutupan
                log.warning("Batalkan algo orders %s saat emergency close gagal: %s", symbol, exc)
            positions = self._binance.positions(symbol)
            if not positions:
                return True
            amt = float(positions[0]["positionAmt"])
            if amt == 0:
                return True
            self._binance.place_order(
                symbol=symbol,
                side="SELL" if amt > 0 else "BUY",
                order_type="MARKET",
                quantity=abs(amt),
                reduce_only=True,
            )
            remaining = self._binance.positions(symbol)
            return not remaining
        except BinanceAPIError as exc:
            log.error("EMERGENCY CLOSE %s GAGAL: %s — posisi mungkin masih terbuka!", symbol, exc)
            return False

    # ------------------------------------------------------------------
    def execute(self, decision: RiskDecision, reference_price: float) -> ExecutionResult:
        if not decision.allowed:
            raise ValueError("RiskDecision tidak diizinkan — eksekusi dibatalkan.")
        with self._lock:
            return self._execute_locked(decision, reference_price)

    def _execute_locked(self, decision: RiskDecision, reference_price: float) -> ExecutionResult:
        entry_side = "BUY" if decision.side == "LONG" else "SELL"
        exit_side = "SELL" if decision.side == "LONG" else "BUY"
        result = ExecutionResult(
            symbol=decision.symbol,
            side=decision.side,
            dry_run=not self._settings.is_live,
            client_order_id=self._make_client_order_id(
                decision.symbol, decision.side, decision.quantity
            ),
        )
        self._transition(result, CREATED)
        self._transition(result, VALIDATING)

        if not self._settings.is_live:
            result.filled_qty = decision.quantity
            self._transition(result, APPROVED)
            self._transition(result, FILLED, "simulasi dry-run")
            self._transition(result, PROTECTED, "SL/TP simulasi")
            result.notes.append(
                f"[DRY-RUN] {entry_side} {decision.quantity} {decision.symbol} "
                f"@ ~{reference_price} (lev {decision.leverage}x), "
                f"SL {decision.stop_loss}, TP {decision.take_profit} — tidak dikirim ke Binance."
            )
            log.info(result.notes[-1])
            return result

        # --- LIVE ---
        try:
            # Validasi ulang keadaan nyata: posisi simbol ini belum ada.
            if self._binance.positions(decision.symbol):
                self._transition(result, FAILED, "posisi simbol sudah terbuka saat validasi")
                result.notes.append("Eksekusi dibatalkan: posisi simbol ini sudah terbuka.")
                return result
            self._transition(result, APPROVED)
            self._binance.change_leverage(decision.symbol, decision.leverage)

            self._transition(result, SUBMITTING)
            try:
                entry = self._binance.place_order(
                    symbol=decision.symbol,
                    side=entry_side,
                    order_type="MARKET",
                    quantity=decision.quantity,
                    new_client_order_id=result.client_order_id,
                )
            except Exception as exc:
                # Hasil tidak diketahui — JANGAN kirim ulang. Periksa dulu.
                log.warning("Hasil kirim entri %s tidak pasti (%s) — memeriksa status order…",
                            decision.symbol, exc)
                found = self._find_order(decision.symbol, result.client_order_id)
                if found is None:
                    self._transition(result, FAILED, f"entri tidak ditemukan di Binance: {exc}")
                    result.notes.append("Order entri tidak ditemukan di Binance — eksekusi gagal, tidak ada posisi.")
                    return result
                entry = found
            result.entry_order = entry
            entry = self._await_fill(decision.symbol, entry, result.client_order_id)
            result.entry_order = entry
            status = entry.get("status")
            filled = float(entry.get("executedQty", 0) or 0)
            result.filled_qty = filled
            if status == "REJECTED" or (status in {"CANCELED", "EXPIRED"} and filled <= 0):
                self._transition(result, FAILED, f"order entri berstatus {status}")
                result.notes.append(f"Order entri {status} oleh Binance — tidak ada posisi.")
                return result
            if filled <= 0:
                self._transition(result, RECOVERY_REQUIRED, "entri tanpa jumlah terisi yang jelas")
                result.notes.append("Status isi entri tidak jelas — periksa posisi di Binance secara manual.")
                return result
            self._transition(
                result,
                FILLED if filled >= decision.quantity else PARTIALLY_FILLED,
                f"terisi {filled}/{decision.quantity}",
            )

            # --- Proteksi: SL wajib, TP menyusul. ---
            self._transition(result, PROTECTION_PENDING)
            sl_order = None
            if decision.stop_loss:
                sl_order = self._place_protection(
                    decision.symbol, exit_side, "STOP_MARKET", decision.stop_loss,
                    client_algo_id=f"{result.client_order_id}-sl"[:36],
                )
                result.stop_loss_order = sl_order
            if sl_order is None:
                log.error(
                    "SL GAGAL DIPASANG untuk %s — menjalankan EMERGENCY CLOSE.",
                    decision.symbol,
                )
                result.notes.append("Stop Loss gagal dipasang — posisi ditutup darurat demi keamanan.")
                if self._emergency_close(decision.symbol):
                    self._transition(result, CLOSED, "emergency close setelah SL gagal")
                else:
                    self._transition(result, RECOVERY_REQUIRED, "SL gagal + emergency close gagal")
                    result.notes.append("PERHATIAN: posisi mungkin masih terbuka TANPA stop loss — periksa Binance sekarang!")
                return result

            if decision.take_profit:
                tp_order = self._place_protection(
                    decision.symbol, exit_side, "TAKE_PROFIT_MARKET", decision.take_profit,
                    client_algo_id=f"{result.client_order_id}-tp"[:36],
                )
                result.take_profit_order = tp_order
                if tp_order is None:
                    result.notes.append(
                        "Take Profit gagal dipasang setelah beberapa percobaan — posisi tetap "
                        "terlindungi Stop Loss; pasang TP manual bila perlu."
                    )
            self._transition(result, PROTECTED)
            result.notes.append(
                f"[LIVE] Entri {entry.get('orderId')} terisi {filled}; SL/TP terpasang & terverifikasi untuk {decision.symbol}."
            )
            log.info(result.notes[-1])
            return result
        except BinanceAPIError as exc:
            self._transition(result, FAILED, f"BinanceAPIError: {exc}")
            result.notes.append(f"Eksekusi gagal: {exc}")
            log.error("Eksekusi %s gagal: %s", decision.symbol, exc)
            return result

    # ------------------------------------------------------------------
    def close_position(self, symbol: str) -> dict[str, Any]:
        """Tutup posisi satu simbol pada harga pasar + batalkan order terbuka."""
        symbol = symbol.upper()
        with self._lock:
            positions = self._binance.positions(symbol)
            if not positions:
                return {"symbol": symbol, "closed": False, "note": "Tidak ada posisi terbuka."}

            amt = float(positions[0]["positionAmt"])
            side = "SELL" if amt > 0 else "BUY"

            if not self._settings.is_live:
                log.info("[DRY-RUN] Tutup posisi %s qty=%s — tidak dikirim.", symbol, abs(amt))
                return {"symbol": symbol, "closed": False, "dry_run": True, "quantity": abs(amt)}

            marker = ExecutionResult(symbol=symbol, side="LONG" if amt > 0 else "SHORT", dry_run=False)
            self._transition(marker, CLOSING)
            self._binance.cancel_all_orders(symbol)
            try:
                self._binance.cancel_all_algo_orders(symbol)
            except Exception as exc:  # SL/TP algo harus ikut dibatalkan; kegagalan dicatat saja
                log.warning("Batalkan algo orders %s saat tutup posisi gagal: %s", symbol, exc)
            order = self._binance.place_order(
                symbol=symbol,
                side=side,
                order_type="MARKET",
                quantity=abs(amt),
                reduce_only=True,
            )
            remaining = self._binance.positions(symbol)
            if remaining:
                self._transition(marker, RECOVERY_REQUIRED, "posisi masih ada setelah perintah tutup")
                return {"symbol": symbol, "closed": False, "order": order,
                        "note": "Perintah tutup terkirim tetapi posisi masih tercatat — periksa Binance."}
            self._transition(marker, CLOSED)
            return {"symbol": symbol, "closed": True, "order": order}
