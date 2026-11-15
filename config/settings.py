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

    # --- Multi-provider AI (dipilih lewat ai_provider) ---
    ai_provider: str = "bangunweb"  # bangunweb|openai|anthropic|deepseek|grok|nous|custom
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_base_url: str = "https://api.anthropic.com"
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    xai_api_key: str = ""
    xai_model: str = "grok-4"
    xai_base_url: str = "https://api.x.ai/v1"
    nous_api_key: str = ""
    nous_model: str = "Hermes-4-70B"
    nous_base_url: str = "https://inference-api.nousresearch.com/v1"
    ai_custom_base_url: str = ""
    ai_custom_api_key: str = ""
    ai_custom_model: str = ""

    # --- Jurnal perdagangan ---
    trade_journal_path: str = "logs/trades.jsonl"

    # --- Notifikasi Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_notifications: bool = True

    # Cache hasil analisis AI (detik) agar Web UI, siaran Telegram, dan CLI
    # menampilkan sinyal yang sama untuk momen yang sama; 0 = nonaktif.
    analysis_cache_ttl_sec: int = 180

    # --- Siaran sinyal otomatis ke Telegram (berjalan selama server web) ---
    broadcast_enabled: bool = True
    broadcast_interval_minutes: int = 30
    broadcast_min_score: int = 50
    broadcast_max_signals: int = 3
    broadcast_cooldown_hours: float = 4.0

    # --- Mode trading ---
    trading_mode: str = "dry_run"  # "dry_run" | "live"

    # --- Manajemen risiko ---
    risk_per_trade_pct: float = 1.0
    max_leverage: int = 5
    max_open_positions: int = 3
    daily_loss_limit_pct: float = 3.0
    weekly_loss_limit_pct: float = 6.0
    max_symbol_exposure_pct: float = 150.0  # notional satu simbol ≤ % ekuitas ini
    max_total_exposure_pct: float = 300.0   # total notional semua posisi ≤ % ekuitas ini
    min_risk_reward: float = 1.5            # rasio TP:SL minimum agar sinyal diterima
    max_consecutive_losses: int = 3         # 0 = nonaktif; berhenti setelah N rugi beruntun
    risk_state_path: str = "data/risk_state.json"  # baseline ekuitas harian/mingguan + kunci harian (persisten)
    protection_retries: int = 3             # percobaan pasang SL/TP sebelum emergency close
    default_interval: str = "15m"
    quote_asset: str = "USDT"

    # --- Keamanan Web UI / API ---
    # Kosong = autentikasi NONAKTIF (hanya aman untuk akses lokal sendiri).
    # Isi WEB_API_TOKEN untuk mewajibkan token pada semua endpoint /api/*:
    # token utama = akses penuh (operator), WEB_VIEWER_TOKEN = hanya baca.
    web_api_token: str = ""
    web_viewer_token: str = ""
    # Daftar origin CORS yang diizinkan, pisah koma. Kosong = tanpa CORS
    # (hanya same-origin). JANGAN gunakan "*" bersamaan dengan token.
    cors_origins: str = ""
    rate_limit_per_minute: int = 240  # batas request /api per IP per menit; 0 = nonaktif
    mtf_enabled: bool = True          # analisis multi-timeframe (1H/4H/1D) pada sinyal
    market_ws_enabled: bool = True    # WebSocket data pasar real-time untuk grafik Web UI
    database_url: str = ""            # Supabase/PostgreSQL (DATABASE_URL); kosong = JSONL saja

    # --- Web UI ---
    web_host: str = "127.0.0.1"  # aman secara default: hanya mesin ini sendiri
    web_port: int = 8000
    allow_insecure_web: bool = False  # True = izinkan bind publik/LIVE tanpa token (BERBAHAYA)
    max_entry_deviation_pct: float = 0.3  # tolak eksekusi bila harga bergeser > % ini dari sinyal

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
            "ai_provider": self.ai_provider,
            "telegram_configured": bool(self.telegram_bot_token and self.telegram_chat_id),
            "broadcast_enabled": self.broadcast_enabled,
            "broadcast_interval_minutes": self.broadcast_interval_minutes,
            "broadcast_min_score": self.broadcast_min_score,
            "trading_mode": "live" if self.is_live else "dry_run",
            "web_auth_enabled": bool(self.web_api_token),
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_leverage": self.max_leverage,
            "max_open_positions": self.max_open_positions,
            "daily_loss_limit_pct": self.daily_loss_limit_pct,
            "weekly_loss_limit_pct": self.weekly_loss_limit_pct,
            "min_risk_reward": self.min_risk_reward,
            "database_configured": bool(self.database_url),
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
        ai_provider=os.environ.get("AI_PROVIDER", "bangunweb").strip().lower(),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o").strip(),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5").strip(),
        anthropic_base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").strip().rstrip("/"),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip(),
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip(),
        deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip().rstrip("/"),
        xai_api_key=(os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY", "")).strip(),
        xai_model=os.environ.get("XAI_MODEL", os.environ.get("GROK_MODEL", "grok-4")).strip(),
        xai_base_url=os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1").strip().rstrip("/"),
        nous_api_key=(os.environ.get("NOUS_API_KEY") or os.environ.get("NOUS_PORTAL_API_KEY", "")).strip(),
        nous_model=os.environ.get("NOUS_MODEL", "Hermes-4-70B").strip(),
        nous_base_url=os.environ.get("NOUS_BASE_URL", "https://inference-api.nousresearch.com/v1").strip().rstrip("/"),
        ai_custom_base_url=os.environ.get("AI_CUSTOM_BASE_URL", "").strip().rstrip("/"),
        ai_custom_api_key=os.environ.get("AI_CUSTOM_API_KEY", "").strip(),
        ai_custom_model=os.environ.get("AI_CUSTOM_MODEL", "").strip(),
        trade_journal_path=os.environ.get("TRADE_JOURNAL_PATH", "logs/trades.jsonl").strip(),
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        telegram_notifications=_get_bool("TELEGRAM_NOTIFICATIONS", True),
        analysis_cache_ttl_sec=_get_int("ANALYSIS_CACHE_TTL_SEC", 180),
        broadcast_enabled=_get_bool("BROADCAST_ENABLED", True),
        broadcast_interval_minutes=_get_int("BROADCAST_INTERVAL_MINUTES", 30),
        broadcast_min_score=_get_int("BROADCAST_MIN_SCORE", 50),
        broadcast_max_signals=_get_int("BROADCAST_MAX_SIGNALS", 3),
        broadcast_cooldown_hours=_get_float("BROADCAST_COOLDOWN_HOURS", 4.0),
        trading_mode=os.environ.get("TRADING_MODE", "dry_run").strip().lower(),
        risk_per_trade_pct=_get_float("RISK_PER_TRADE_PCT", 1.0),
        max_leverage=_get_int("MAX_LEVERAGE", 5),
        max_open_positions=_get_int("MAX_OPEN_POSITIONS", 3),
        daily_loss_limit_pct=_get_float("DAILY_LOSS_LIMIT_PCT", 3.0),
        weekly_loss_limit_pct=_get_float("WEEKLY_LOSS_LIMIT_PCT", 6.0),
        max_symbol_exposure_pct=_get_float("MAX_SYMBOL_EXPOSURE_PCT", 150.0),
        max_total_exposure_pct=_get_float("MAX_TOTAL_EXPOSURE_PCT", 300.0),
        min_risk_reward=_get_float("MIN_RISK_REWARD", 1.5),
        max_consecutive_losses=_get_int("MAX_CONSECUTIVE_LOSSES", 3),
        risk_state_path=os.environ.get("RISK_STATE_PATH", "data/risk_state.json").strip(),
        protection_retries=_get_int("PROTECTION_RETRIES", 3),
        web_api_token=os.environ.get("WEB_API_TOKEN", "").strip(),
        web_viewer_token=os.environ.get("WEB_VIEWER_TOKEN", "").strip(),
        cors_origins=os.environ.get("CORS_ORIGINS", "").strip(),
        rate_limit_per_minute=_get_int("RATE_LIMIT_PER_MINUTE", 240),
        mtf_enabled=_get_bool("MTF_ENABLED", True),
        market_ws_enabled=_get_bool("MARKET_WS_ENABLED", True),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        default_interval=os.environ.get("DEFAULT_INTERVAL", "15m").strip(),
        quote_asset=os.environ.get("QUOTE_ASSET", "USDT").strip().upper(),
        web_host=os.environ.get("WEB_HOST", "127.0.0.1").strip(),
        web_port=_get_int("WEB_PORT", 8000),
        allow_insecure_web=_get_bool("ALLOW_INSECURE_WEB", False),
        max_entry_deviation_pct=_get_float("MAX_ENTRY_DEVIATION_PCT", 0.3),
    )
