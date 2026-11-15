#!/usr/bin/env python3
"""Gerbang cakupan per berkas untuk CI (Fase 6-C2).

Membaca laporan JSON pytest-cov dan gagal (exit 1) bila berkas yang
terdaftar berada di bawah ambangnya. Ambang ditetapkan SEDIKIT di
bawah cakupan terukur saat Fase 6 agar gerbang menangkap regresi,
bukan menuntut angka yang tidak pernah dicapai.

Pakai: python scripts/check_coverage.py coverage.json
"""

from __future__ import annotations

import json
import sys

GATES = {
    "core/risk.py": 78,
    "core/executor.py": 78,
    "core/journal.py": 70,
    "core/strategy.py": 60,
    "core/reconciler.py": 65,
    "core/position_manager.py": 60,
    "core/indicators.py": 70,
    "core/market_stream.py": 70,
    "config/settings.py": 80,
    "web/app.py": 50,
}


def main() -> int:
    with open(sys.argv[1] if len(sys.argv) > 1 else "coverage.json", encoding="utf-8") as fh:
        report = json.load(fh)
    files = report.get("files", {})
    failures = []
    for path, minimum in GATES.items():
        entry = files.get(path)
        if entry is None:
            failures.append(f"{path}: tidak ada di laporan cakupan")
            continue
        pct = float(entry["summary"]["percent_covered"])
        status = "OK " if pct >= minimum else "FAIL"
        print(f"{status} {path}: {pct:.1f}% (ambang {minimum}%)")
        if pct < minimum:
            failures.append(f"{path}: {pct:.1f}% < {minimum}%")
    if failures:
        print("Gerbang cakupan GAGAL:", "; ".join(failures))
        return 1
    print("Semua gerbang cakupan lulus.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
