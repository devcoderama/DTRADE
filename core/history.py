"""Pengambil riwayat klines panjang dengan cache disk (Fase 4.1).

Backtest/riset butuh ≥10.000 candle; mengunduh ulang setiap kali lambat
dan membebani API. Cache per simbol+interval disimpan sebagai JSON di
`data/kline_cache/`; pemuatan berikutnya hanya mengambil ekor yang
belum tercache (dari open time terakhir + 1) lalu menggabungkan &
membuang duplikat. Baris terakhir cache selalu diambil ulang karena
mungkin candle yang saat itu belum tertutup.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000,
    "1w": 604_800_000,
}

DEFAULT_CACHE_DIR = Path("data/kline_cache")


def interval_ms(interval: str) -> int:
    return INTERVAL_MS.get(interval, 900_000)


def _cache_path(cache_dir: Path, symbol: str, interval: str) -> Path:
    return cache_dir / f"{symbol.upper()}_{interval}.json"


def _read_cache(path: Path) -> list[list]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write_cache(path: Path, rows: list[list]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("Cache klines tidak tersimpan (%s): %s", path, exc)


def load_history(
    client,
    symbol: str,
    interval: str = "15m",
    days: int = 90,
    cache_dir: Optional[Path] = None,
    now_ms: Optional[int] = None,
) -> list[list]:
    """Klines selama `days` hari ke belakang, memakai cache disk.

    `client` harus menyediakan `klines_paginated(symbol, interval,
    start_time, end_time)`. Mengembalikan baris terurut berdasarkan
    open time, hanya yang >= batas awal rentang.
    """
    if cache_dir:
        cache_dir = Path(cache_dir)
    else:
        # Fase 6-A6: cache mengikuti DATA_DIR bila diatur di environment.
        import os

        base = os.environ.get("DATA_DIR", "").strip()
        cache_dir = (Path(base) / "kline_cache") if base else DEFAULT_CACHE_DIR
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    start = now - days * 86_400_000
    path = _cache_path(cache_dir, symbol, interval)
    cached = _read_cache(path)

    fetch_from = start
    if cached:
        last_cached = int(cached[-1][0])
        # Ambil ulang dari candle terakhir cache (mungkin belum tertutup dulu).
        fetch_from = max(start, last_cached)
    fresh = client.klines_paginated(symbol, interval, fetch_from, now)
    merged: dict[int, list] = {int(r[0]): r for r in cached}
    for row in fresh:
        merged[int(row[0])] = row
    rows = [merged[t] for t in sorted(merged) if t >= start]
    if fresh:
        _write_cache(path, [merged[t] for t in sorted(merged)])
    log.info(
        "Riwayat %s %s: %d candle (%d hari, cache %d + unduhan %d)",
        symbol, interval, len(rows), days, len(cached), len(fresh),
    )
    return rows
