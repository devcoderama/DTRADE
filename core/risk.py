"""Manajemen risiko — gerbang wajib sebelum eksekusi apa pun.

Aturan yang ditegakkan di sini (tidak bisa dilewati executor):

1. Ukuran posisi dihitung dari % risiko saldo dan jarak stop loss,
   bukan dari "feeling" atau margin penuh.
2. Leverage tidak pernah melebihi MAX_LEVERAGE.
3. Jumlah posisi terbuka tidak melebihi MAX_OPEN_POSITIONS.
4. Bila rugi terealisasi hari ini >= DAILY_LOSS_LIMIT_PCT dari saldo awal
   hari, semua sinyal baru ditolak.
5. Kuantitas & harga selalu dibulatkan mengikuti filter resmi simbol,
   dan notional harus memenuhi minNotional Binance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from config.settings import Settings
from core.binance_client import BinanceFuturesClient
from core.strategy import TradeSignal
from core.symbols import SymbolManager
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class RiskDecision:
    allowed: bool
    symbol: str
    side: str
    quantity: float = 0.0
    leverage: int = 0
    notional: float = 0.0
    risk_amount: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasons: list[str] = field(default_factory=list)


class RiskManager:
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
    def evaluate(self, signal: TradeSignal) -> RiskDecision:
        s = self._settings
        base = RiskDecision(allowed=False, symbol=signal.symbol, side=signal.side)

        if signal.side not in {"LONG", "SHORT"}:
            base.reasons.append("Sinyal NEUTRAL — tidak ada yang dieksekusi.")
            return base
        if not signal.stop_loss or not signal.take_profit:
            base.reasons.append("Sinyal tanpa stop loss / take profit — ditolak demi keamanan.")
            return base
        if not self._symbols.is_tradable(signal.symbol):
            base.reasons.append(f"{signal.symbol} bukan perpetual {s.quote_asset} yang aktif.")
            return base

        # --- Keadaan akun ---
        account = self._binance.account()
        equity = float(account.get("totalWalletBalance", 0)) + float(
            account.get("totalUnrealizedProfit", 0)
        )
        if equity <= 0:
            base.reasons.append("Saldo akun futures kosong / tidak terbaca.")
            return base

        open_positions = self._binance.positions()
        if len(open_positions) >= s.max_open_positions:
            base.reasons.append(
                f"Sudah ada {len(open_positions)} posisi terbuka (batas {s.max_open_positions})."
            )
            return base
        if any(p["symbol"] == signal.symbol for p in open_positions):
            base.reasons.append(f"Posisi {signal.symbol} sudah terbuka — penambahan posisi ditolak.")
            return base

        # --- Batas rugi harian ---
        realized_today = self._binance.realized_pnl_today()
        daily_limit_amount = equity * s.daily_loss_limit_pct / 100.0
        if realized_today <= -daily_limit_amount:
            base.reasons.append(
                f"Batas rugi harian tercapai (realized {realized_today:.2f} USDT, "
                f"batas -{daily_limit_amount:.2f} USDT). Berhenti hari ini."
            )
            return base

        # --- Position sizing dari jarak stop loss ---
        entry = signal.price
        stop_distance = abs(entry - signal.stop_loss)
        if stop_distance <= 0:
            base.reasons.append("Jarak stop loss nol — tidak valid.")
            return base

        risk_amount = equity * s.risk_per_trade_pct / 100.0
        raw_qty = risk_amount / stop_distance

        filters = self._symbols.filters(signal.symbol)
        qty = filters.round_quantity(raw_qty)

        # Leverage: pilih yang cukup untuk notional, dibatasi MAX_LEVERAGE,
        # dan notional tidak boleh melampaui ekuitas × leverage.
        notional = qty * entry
        needed_leverage = max(1, int(notional / equity) + 1) if equity > 0 else s.max_leverage
        leverage = min(s.max_leverage, max(1, needed_leverage))
        max_notional = equity * leverage
        if notional > max_notional:
            qty = filters.round_quantity(max_notional / entry)
            notional = qty * entry

        if qty < filters.quantity_min:
            base.reasons.append(
                f"Kuantitas hasil hitung ({qty}) di bawah minQty {filters.quantity_min} "
                f"untuk {signal.symbol} — saldo terlalu kecil untuk risiko sekecil ini."
            )
            return base
        if notional < filters.min_notional:
            base.reasons.append(
                f"Notional {notional:.2f} USDT di bawah minNotional {filters.min_notional} USDT."
            )
            return base

        base.allowed = True
        base.quantity = qty
        base.leverage = leverage
        base.notional = notional
        base.risk_amount = risk_amount
        base.stop_loss = filters.round_price(signal.stop_loss)
        base.take_profit = filters.round_price(signal.take_profit)
        base.reasons.append(
            f"OK — risiko {risk_amount:.2f} USDT ({s.risk_per_trade_pct}% dari ekuitas "
            f"{equity:.2f}), qty {qty}, leverage {leverage}x, notional {notional:.2f} USDT."
        )
        log.info("RISK ✓ %s %s qty=%s lev=%sx", signal.symbol, signal.side, qty, leverage)
        return base
