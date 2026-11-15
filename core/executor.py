"""Eksekusi order — satu-satunya modul yang boleh menempatkan order.

Menghormati dua mode dari konfigurasi:

- `dry_run` (bawaan): seluruh rangkaian order DISIMULASIKAN dan dicatat
  ke log. Tidak ada satu pun panggilan trading ke Binance.
- `live`: entri market + Stop Loss + Take Profit dikirim sungguhan.

Executor hanya menerima `RiskDecision` yang `allowed=True` dari
RiskManager. Ia tidak menilai sinyal dan tidak menghitung ulang risiko.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from config.settings import Settings
from core.binance_client import BinanceFuturesClient
from core.risk import RiskDecision
from core.symbols import SymbolManager
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class ExecutionResult:
    symbol: str
    side: str
    dry_run: bool
    entry_order: Optional[dict] = None
    stop_loss_order: Optional[dict] = None
    take_profit_order: Optional[dict] = None
    notes: list[str] = field(default_factory=list)


class OrderExecutor:
    def __init__(
        self,
        settings: Settings,
        binance: BinanceFuturesClient,
        symbols: SymbolManager,
    ):
        self._settings = settings
        self._binance = binance
        self._symbols = symbols

    # ------------------------------------------------------------------
    def execute(self, decision: RiskDecision, reference_price: float) -> ExecutionResult:
        if not decision.allowed:
            raise ValueError("RiskDecision tidak diizinkan — eksekusi dibatalkan.")

        entry_side = "BUY" if decision.side == "LONG" else "SELL"
        result = ExecutionResult(
            symbol=decision.symbol, side=decision.side, dry_run=not self._settings.is_live
        )

        if not self._settings.is_live:
            result.notes.append(
                f"[DRY-RUN] {entry_side} {decision.quantity} {decision.symbol} "
                f"@ ~{reference_price} (lev {decision.leverage}x), "
                f"SL {decision.stop_loss}, TP {decision.take_profit} — tidak dikirim ke Binance."
            )
            log.info(result.notes[-1])
            return result

        # --- LIVE ---
        self._binance.change_leverage(decision.symbol, decision.leverage)
        entry = self._binance.place_order(
            symbol=decision.symbol,
            side=entry_side,
            order_type="MARKET",
            quantity=decision.quantity,
        )
        result.entry_order = entry

        exit_side = "SELL" if decision.side == "LONG" else "BUY"
        filters = self._symbols.filters(decision.symbol)
        if decision.stop_loss:
            result.stop_loss_order = self._binance.place_order(
                symbol=decision.symbol,
                side=exit_side,
                order_type="STOP_MARKET",
                stop_price=filters.round_price(decision.stop_loss),
                close_position=True,
            )
        if decision.take_profit:
            result.take_profit_order = self._binance.place_order(
                symbol=decision.symbol,
                side=exit_side,
                order_type="TAKE_PROFIT_MARKET",
                stop_price=filters.round_price(decision.take_profit),
                close_position=True,
            )
        result.notes.append(
            f"[LIVE] Entri {entry.get('orderId')} terisi; SL/TP terpasang untuk {decision.symbol}."
        )
        log.info(result.notes[-1])
        return result

    # ------------------------------------------------------------------
    def close_position(self, symbol: str) -> dict[str, Any]:
        """Tutup posisi satu simbol pada harga pasar + batalkan order terbuka."""
        symbol = symbol.upper()
        positions = self._binance.positions(symbol)
        if not positions:
            return {"symbol": symbol, "closed": False, "note": "Tidak ada posisi terbuka."}

        amt = float(positions[0]["positionAmt"])
        side = "SELL" if amt > 0 else "BUY"

        if not self._settings.is_live:
            log.info("[DRY-RUN] Tutup posisi %s qty=%s — tidak dikirim.", symbol, abs(amt))
            return {"symbol": symbol, "closed": False, "dry_run": True, "quantity": abs(amt)}

        self._binance.cancel_all_orders(symbol)
        order = self._binance.place_order(
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=abs(amt),
            reduce_only=True,
        )
        return {"symbol": symbol, "closed": True, "order": order}
