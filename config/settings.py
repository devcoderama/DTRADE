"""Konfigurasi terpusat untuk binance-ai-future.

Seluruh modul WAJIB membaca konfigurasi dari objek `Settings` di sini.
Tidak ada modul lain yang boleh menyentuh `os.environ` secara langsung,
agar perilaku aplikasi selalu konsisten dan mudah diuji.

Nilai dibaca dari file `.env` di root proyek (bila ada) dan/atau dari
environment variabel proses. Secret tidak pernah diberi nilai bawaan
dan tidak pernah dicetak ke log.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # python-dotenv bersifat opsional saat runtime bila env sudah diset OS
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover - dotenv tidak terpasang
    pass


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Konfigurasi aplikasi yang immutable setelah dimuat."""

    # --- Binance Futures ---
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_testnet: bool = False

    # --- AI Router BangunWeb ---
    bangunweb_api_key: str = ""
    bangunweb_base_url: str = "https://router.bangunweb.com/v1"
    bangunweb_model: str = "bw-focus"

    # --- Mode trading ---
    trading_mode: str = "dry_run"  # "dry_run" | "live"

    # --- Manajemen risiko ---
    risk_per_trade_pct: float = 1.0
    max_leverage: int = 5
    max_open_positions: int = 3
    daily_loss_limit_pct: float = 3.0
    default_interval: str = "15m"
    quote_asset: str = "USDT"

    # --- Web UI ---
    web_host: str = "0.0.0.0"
    web_port: int = 8000

    # ------------------------------------------------------------------
    @property
    def binance_base_url(self) -> str:
        if self.binance_testnet:
            return "https://testnet.binancefuture.com"
        return "https://fapi.binance.com"

    @property
    def is_live(self) -> bool:
        return self.trading_mode.strip().lower() == "live"

    @property
    def has_binance_keys(self) -> bool:
        return bool(self.binance_api_key and self.binance_api_secret)

    @property
    def has_ai_key(self) -> bool:
        return bool(self.bangunweb_api_key)

    def public_summary(self) -> dict:
        """Ringkasan konfigurasi yang AMAN ditampilkan (tanpa secret)."""
        return {
            "binance_network": "testnet" if self.binance_testnet else "mainnet",
            "binance_keys_configured": self.has_binance_keys,
            "ai_configured": self.has_ai_key,
            "ai_model": self.bangunweb_model,
            "trading_mode": "live" if self.is_live else "dry_run",
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_leverage": self.max_leverage,
            "max_open_positions": self.max_open_positions,
            "daily_loss_limit_pct": self.daily_loss_limit_pct,
            "default_interval": self.default_interval,
            "quote_asset": self.quote_asset,
        }


def load_settings() -> Settings:
    """Bangun objek Settings dari environment saat ini."""
    return Settings(
        binance_api_key=os.environ.get("BINANCE_API_KEY", "").strip(),
        binance_api_secret=os.environ.get("BINANCE_API_SECRET", "").strip(),
        binance_testnet=_get_bool("BINANCE_TESTNET", False),
        bangunweb_api_key=os.environ.get("BANGUNWEB_API_KEY", "").strip(),
        bangunweb_base_url=os.environ.get(
            "BANGUNWEB_BASE_URL", "https://router.bangunweb.com/v1"
        ).strip().rstrip("/"),
        bangunweb_model=os.environ.get("BANGUNWEB_MODEL", "bw-focus").strip(),
        trading_mode=os.environ.get("TRADING_MODE", "dry_run").strip().lower(),
        risk_per_trade_pct=_get_float("RISK_PER_TRADE_PCT", 1.0),
        max_leverage=_get_int("MAX_LEVERAGE", 5),
        max_open_positions=_get_int("MAX_OPEN_POSITIONS", 3),
        daily_loss_limit_pct=_get_float("DAILY_LOSS_LIMIT_PCT", 3.0),
        default_interval=os.environ.get("DEFAULT_INTERVAL", "15m").strip(),
        quote_asset=os.environ.get("QUOTE_ASSET", "USDT").strip().upper(),
        web_host=os.environ.get("WEB_HOST", "0.0.0.0").strip(),
        web_port=_get_int("WEB_PORT", 8000),
    )
