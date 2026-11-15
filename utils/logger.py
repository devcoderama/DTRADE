"""Logging terstruktur: console berwarna + file berotasi di folder logs/."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Konfigurasi logging global. Aman dipanggil berkali-kali."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        # Lingkungan read-only (mis. sebagian perangkat mobile): console saja.
        pass


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
