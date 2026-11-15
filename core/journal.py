"""Jurnal perdagangan: catatan permanen setiap eksekusi.

Setiap hasil eksekusi (live maupun dry-run) ditambahkan sebagai satu
baris JSON ke berkas jurnal (bawaan `logs/trades.jsonl`). Jurnal adalah
jejak audit lokal: apa yang diputuskan, pada harga berapa, dan catatan
executor — berguna untuk evaluasi dan pelacakan kesalahan.

Format satu baris = satu objek JSON dengan kunci:
time, symbol, side, quantity, leverage, reference_price, stop_loss,
take_profit, notional, risk_amount, dry_run, notes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from utils.logger import get_logger

log = get_logger(__name__)


class TradeJournal:
    def __init__(self, path: str, db=None):
        self._path = Path(path)
        self._db = db  # cermin opsional ke Supabase/PostgreSQL (core.database)

    @property
    def path(self) -> Path:
        return self._path

    def record(self, entry: dict[str, Any]) -> None:
        """Tambahkan satu entri; kegagalan tulis tidak pernah fatal."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            row = {"time": int(time.time() * 1000), **entry}
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:
            log.warning("Gagal menulis jurnal perdagangan: %s", exc)
            row = {"time": int(time.time() * 1000), **entry}
        if self._db is not None:
            self._db.insert_journal(row)  # kegagalan DB diabaikan di dalam Database

    def record_execution(self, decision: Any, result: Any, reference_price: float) -> None:
        self.record(
            {
                "symbol": decision.symbol,
                "side": decision.side,
                "quantity": decision.quantity,
                "leverage": decision.leverage,
                "reference_price": reference_price,
                "stop_loss": decision.stop_loss,
                "take_profit": decision.take_profit,
                "notional": round(decision.notional, 2),
                "risk_amount": round(decision.risk_amount, 2),
                "dry_run": bool(getattr(result, "dry_run", True)),
                "execution_state": getattr(result, "state", None),
                "client_order_id": getattr(result, "client_order_id", None),
                "filled_qty": getattr(result, "filled_qty", None),
                "notes": [str(n) for n in getattr(result, "notes", [])],
            }
        )

    def record_close(
        self,
        *,
        symbol: str,
        side: str,
        exit_price: float | None,
        exit_reason: str,
        realized_pnl: float | None,
        client_order_id: str | None = None,
        fee: float | None = None,
        opened_at_ms: int | None = None,
        risk_amount: float | None = None,
    ) -> None:
        """Catat penutupan posisi (SL/TP/manual) — pasangan entri eksekusi.

        Dihubungkan ke entri lewat `client_order_id`; R-multiple dihitung
        terhadap risk_amount entri bila diketahui.
        """
        now_ms = int(time.time() * 1000)
        r_multiple = None
        if realized_pnl is not None and risk_amount:
            r_multiple = realized_pnl / risk_amount
        self.record(
            {
                "type": "close",
                "symbol": symbol,
                "side": side,
                "client_order_id": client_order_id,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "realized_pnl": realized_pnl,
                "fee": fee,
                "duration_sec": (
                    round((now_ms - opened_at_ms) / 1000, 1) if opened_at_ms else None
                ),
                "r_multiple": r_multiple,
            }
        )

    def record_ai_decision(self, signal: Any, provider: str | None = None) -> None:
        """Catat keputusan AI per sinyal (untuk menilai nilai tambah AI)."""
        ai = getattr(signal, "ai", None)
        if ai is None:
            return
        self.record(
            {
                "type": "ai_decision",
                "symbol": signal.symbol,
                "provider": provider,
                "model": getattr(ai, "model_used", None),
                "ai_signal": getattr(ai, "signal", None),
                "ai_confidence": getattr(ai, "confidence", None),
                "final_side": signal.side,
                "final_confidence": signal.confidence,
                "technical_score": signal.technical_score,
            }
        )

    def all_entries(self) -> list[dict[str, Any]]:
        """Seluruh isi jurnal (urutan kronologis)."""
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def performance(self) -> dict[str, Any]:
        """Metrik performa dari event penutupan di jurnal.

        Win rate, profit factor, expectancy, R rata-rata, dan drawdown
        maksimum dari kurva PnL kumulatif — hanya dari trade yang sudah
        TUTUP (punya realized PnL), bukan dari entri yang masih terbuka.
        """
        closes = [
            e for e in self.all_entries()
            if e.get("type") == "close" and e.get("realized_pnl") is not None
        ]
        pnls = [float(e["realized_pnl"]) for e in closes]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        rs = [float(e["r_multiple"]) for e in closes if e.get("r_multiple") is not None]
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            equity += p
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)
        return {
            "closed_trades": len(pnls),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / len(pnls) * 100, 2) if pnls else None,
            "total_pnl": round(sum(pnls), 4),
            "profit_factor": (round(gross_profit / gross_loss, 3) if gross_loss > 0 else None),
            "expectancy": (round(sum(pnls) / len(pnls), 4) if pnls else None),
            "avg_r": (round(sum(rs) / len(rs), 3) if rs else None),
            "max_drawdown": round(max_dd, 4),
        }

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        """Baca entri terakhir (terbaru di akhir)."""
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
