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
    def __init__(self, path: str):
        self._path = Path(path)

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
