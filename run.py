#!/usr/bin/env python3
"""Pintu masuk utama DTRADE.

Penggunaan:
    python run.py web            → jalankan Web UI (http://localhost:8000)
    python run.py cli <perintah> → antarmuka terminal, cth:
        python run.py cli balance
        python run.py cli scan --top 10
        python run.py cli analyze BTCUSDT
        python run.py cli trade BTCUSDT --side LONG
"""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 0

    mode = sys.argv[1]
    if mode == "web":
        from web.app import run

        run()
        return 0
    if mode == "cli":
        from cli.terminal import main as cli_main

        return cli_main(sys.argv[2:])

    print(f"Mode tidak dikenal: {mode}\n")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
