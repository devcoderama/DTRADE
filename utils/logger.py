"""Logging terstruktur: console berwarna + file berotasi di folder logs/.

Semua catatan melewati filter redaksi: pola secret yang dikenal
(token bot Telegram di URL, parameter `signature=` Binance) disamarkan
sebelum ditulis ke mana pun — log tidak boleh pernah memuat secret.
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False

_REDACTIONS = (
    # URL bot Telegram: https://api.telegram.org/bot<id>:<token>/…
    (re.compile(r"(/bot\d+:)[A-Za-z0-9_-]+"), r"\1***"),
    # Parameter tanda tangan request Binance di URL/pesan error.
    (re.compile(r"(signature=)[0-9a-fA-F]+"), r"\1***"),
    # Header otorisasi yang terlanjur masuk pesan.
    (re.compile(r"(Authorization:\s*Bearer\s+)[A-Za-z0-9._-]+", re.IGNORECASE), r"\1***"),
)


def redact(text: str) -> str:
    """Samarkan pola secret yang dikenal di dalam teks log."""
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


class RedactFilter(logging.Filter):
    """Filter logging yang menjalankan redact() pada pesan akhir."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass  # redaksi tidak boleh menjatuhkan logging itu sendiri
        return True


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

    # Filter redaksi dipasang di HANDLER (bukan logger root) agar berlaku
    # untuk record dari semua logger turunan yang terpropagasi ke sini.
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.addFilter(RedactFilter())
    root.addHandler(console)

    try:
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        file_handler.addFilter(RedactFilter())
        root.addHandler(file_handler)
    except OSError:
        # Lingkungan read-only (mis. sebagian perangkat mobile): console saja.
        pass


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
