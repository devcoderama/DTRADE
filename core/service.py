"""TradingService — satu-satunya pintu masuk eksekusi untuk Web & CLI.

Dua jaminan yang tidak bisa diberikan bila tiap permukaan merangkai
evaluate+execute sendiri:

1. ATOMISITAS: `RiskManager.evaluate` + `OrderExecutor.execute` berjalan
   di bawah SATU lock level-aplikasi. Dua request paralel tidak bisa
   sama-sama lolos pemeriksaan `max_open_positions`/eksposur/kunci
   harian sebelum salah satunya benar-benar membuka posisi.
2. DATA SEGAR: sebelum evaluasi, harga sinyal dibandingkan dengan harga
   pasar terkini. Penyimpangan di atas `MAX_ENTRY_DEVIATION_PCT`
   (bawaan 0,3%) → eksekusi ditolak. Di dalam toleransi, sinyal dinilai
   ulang terhadap harga terkini (salinan — sinyal asli tidak dimutasi)
   sehingga RiskManager memvalidasi arah SL/TP dan R/R dari harga yang
   benar-benar akan dieksekusi.
"""

from __future__ import annotations

import dataclasses
import threading
from typing import Optional

from config.settings import Settings
from core.executor import ExecutionResult, OrderExecutor
from core.risk import RiskDecision, RiskManager
from core.strategy import TradeSignal
from utils.logger import get_logger

log = get_logger(__name__)


class TradingService:
    def __init__(
        self,
        settings: Settings,
        risk: RiskManager,
        executor: OrderExecutor,
        binance=None,
    ):
        self._settings = settings
        self._risk = risk
        self._executor = executor
        self._binance = binance
        self._lock = threading.Lock()

    def _current_price(self, symbol: str) -> Optional[float]:
        if self._binance is None:
            return None
        try:
            return float(self._binance.ticker_price(symbol)["price"])
        except Exception as exc:
            log.warning("Gagal mengambil harga terkini %s: %s", symbol, exc)
            return None

    def submit(self, signal: TradeSignal) -> tuple[RiskDecision, Optional[ExecutionResult]]:
        """Evaluasi + eksekusi secara atomik. Mengembalikan (keputusan, hasil).

        Hasil None berarti keputusan menolak (atau harga basi) — tidak ada
        order yang dikirim.
        """
        with self._lock:
            effective = signal
            reference_price = signal.price
            current = self._current_price(signal.symbol)
            if current is not None and signal.price:
                deviation_pct = abs(current - signal.price) / signal.price * 100
                limit = float(getattr(self._settings, "max_entry_deviation_pct", 0.3))
                if deviation_pct > limit:
                    reason = (
                        f"Harga bergerak {deviation_pct:.2f}% dari harga sinyal "
                        f"({signal.price} → {current}) melebihi batas {limit}% — "
                        "eksekusi ditolak demi keamanan; analisis ulang diperlukan."
                    )
                    log.warning("TRADE DITOLAK %s: %s", signal.symbol, reason)
                    return (
                        RiskDecision(
                            allowed=False, symbol=signal.symbol, side=signal.side,
                            reasons=[reason],
                        ),
                        None,
                    )
                # Nilai ulang terhadap harga terkini (salinan, asli utuh).
                effective = dataclasses.replace(signal, price=current)
                reference_price = current
            decision = self._risk.evaluate(effective)
            if not decision.allowed:
                return decision, None
            result = self._executor.execute(decision, reference_price)
            return decision, result
