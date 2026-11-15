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
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv bersifat opsional saat runtime bila env sudah diset OS
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover - dotenv tidak terpasang; env OS tetap terbaca
    pass  # nosec B110


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ConfigError(ValueError):
    """Konfigurasi tidak valid — aplikasi harus menolak start, bukan menebak."""


def _warn_bad_value(name: str, raw: str, default) -> None:
    """Peringatan keras untuk env salah format (Fase 6-B5)."""
    import logging

    logging.getLogger(__name__).warning(
        "Env %s=%r tidak valid — memakai nilai bawaan %s. Perbaiki .env Anda.",
        name, raw, default,
    )


def _get_float(name: str, default: float, *, critical: bool = False) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        if critical:
            raise ConfigError(f"{name}: nilai {raw!r} bukan angka yang valid.")
        _warn_bad_value(name, raw, default)  # Fase 6-B5: tidak diam-diam
        return default


def _get_int(name: str, default: int, *, critical: bool = False) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        if critical:
            raise ConfigError(f"{name}: nilai {raw!r} bukan bilangan bulat yang valid.")
        _warn_bad_value(name, raw, default)  # Fase 6-B5: tidak diam-diam
        return default


@dataclass(frozen=True)
class Settings:
    """Konfigurasi aplikasi yang immutable setelah dimuat."""

    # --- Binance Futures ---
    binance_api_key: str = field(default="", repr=False)
    binance_api_secret: str = field(default="", repr=False)
    binance_testnet: bool = False

    # --- AI Router BangunWeb ---
    bangunweb_api_key: str = field(default="", repr=False)
    bangunweb_base_url: str = "https://router.bangunweb.com/v1"
    bangunweb_model: str = "bw-focus"

    # --- Multi-provider AI (dipilih lewat ai_provider) ---
    ai_provider: str = "bangunweb"  # bangunweb|openai|anthropic|deepseek|grok|nous|custom
    openai_api_key: str = field(default="", repr=False)
    openai_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: str = field(default="", repr=False)
    anthropic_model: str = "claude-sonnet-4-5"
    anthropic_base_url: str = "https://api.anthropic.com"
    deepseek_api_key: str = field(default="", repr=False)
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    xai_api_key: str = field(default="", repr=False)
    xai_model: str = "grok-4"
    xai_base_url: str = "https://api.x.ai/v1"
    nous_api_key: str = field(default="", repr=False)
    nous_model: str = "Hermes-4-70B"
    nous_base_url: str = "https://inference-api.nousresearch.com/v1"
    ai_custom_base_url: str = ""
    ai_custom_api_key: str = field(default="", repr=False)
    ai_custom_model: str = ""

    # --- Jurnal perdagangan ---
    trade_journal_path: str = "logs/trades.jsonl"

    # --- Notifikasi Telegram ---
    telegram_bot_token: str = field(default="", repr=False)
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
    consecutive_loss_window_hours: int = 24  # streak hanya dihitung dari realisasi N jam terakhir
    protection_working_type: str = "MARK_PRICE"  # pemicu SL/TP algo: MARK_PRICE | CONTRACT_PRICE
    reconciler_enabled: bool = True   # rekonsiliasi posisi berkala di web server
    reconciler_interval_sec: int = 60  # jeda antar putaran rekonsiliasi
    reconciler_grace_sec: int = 120  # Fase 6-A1: masa tenggang — reconciler melewati simbol dgn transisi eksekusi terbaru

    # --- Fase 3: strategi sinyal (semua bisa dimatikan; bawaan konservatif) ---
    use_closed_candles_only: bool = True  # indikator hanya dari candle tertutup
    scoring_version: int = 1              # 1 = skor lama; 2 = skor per kategori
    ai_can_initiate: bool = False         # False: AI hanya konfirmasi/veto, tak memulai trade
    adx_min: float = 20.0                 # entri trend-following ditolak bila ADX di bawah ini
    mtf_gate_enabled: bool = True         # tolak sinyal yang dilawan tren 1h/4h
    regime_gate_enabled: bool = True      # tolak entri pada rezim ekstrem (volatilitas tinggi/rendah)
    # Manajemen keluar (bawaan SEMUA mati):
    move_sl_to_be: bool = False           # geser SL ke break-even setelah profit 1R
    breakeven_trigger_r: float = 1.0
    breakeven_buffer_pct: float = 0.1  # Fase 6-B1: buffer fee di atas harga entri untuk target BE
    trailing_stop_enabled: bool = False   # trailing stop ATR setelah trailing_trigger_r
    trailing_trigger_r: float = 1.5
    trailing_atr_mult: float = 1.0
    partial_tp_enabled: bool = False      # tutup sebagian di partial_tp_r
    partial_tp_r: float = 1.5
    partial_tp_fraction: float = 0.5
    max_hold_hours: float = 0.0           # 0 = tanpa batas waktu posisi
    # Risiko portofolio:
    max_same_direction_positions: int = 2  # maks posisi searah bersamaan
    max_open_risk_pct: float = 0.0         # 0 = 2× risk_per_trade_pct
    min_quote_volume_usd: float = 0.0      # 0 = filter likuiditas mati
    funding_guard_minutes: int = 0         # 0 = penjaga waktu funding mati
    funding_extreme_pct: float = 0.1       # |funding| >= ini dianggap ekstrem
    btc_filter: bool = False               # altcoin mengikuti tren kuat BTC
    risk_state_path: str = "data/risk_state.json"  # baseline ekuitas harian/mingguan + kunci harian (persisten)
    data_dir: str = ""  # Fase 6-A6: direktori state tunggal; kosong = <root proyek>/data (diisi load_settings)
    protection_retries: int = 3             # percobaan pasang SL/TP sebelum emergency close
    default_interval: str = "15m"
    quote_asset: str = "USDT"

    # --- Keamanan Web UI / API ---
    # Kosong = autentikasi NONAKTIF (hanya aman untuk akses lokal sendiri).
    # Isi WEB_API_TOKEN untuk mewajibkan token pada semua endpoint /api/*:
    # token utama = akses penuh (operator), WEB_VIEWER_TOKEN = hanya baca.
    web_api_token: str = field(default="", repr=False)
    web_viewer_token: str = field(default="", repr=False)
    # Daftar origin CORS yang diizinkan, pisah koma. Kosong = tanpa CORS
    # (hanya same-origin). JANGAN gunakan "*" bersamaan dengan token.
    cors_origins: str = ""
    trusted_proxies: str = ""  # Fase 6-C1: daftar IP proksi tepercaya (koma) untuk X-Forwarded-For
    rate_limit_per_minute: int = 240  # batas request /api per IP per menit; 0 = nonaktif
    mtf_enabled: bool = True          # analisis multi-timeframe (1H/4H/1D) pada sinyal
    market_ws_enabled: bool = True    # WebSocket data pasar real-time untuk grafik Web UI
    database_url: str = field(default="", repr=False)            # Supabase/PostgreSQL (DATABASE_URL); kosong = JSONL saja

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

    # Peta provider → (field kunci API, field model). Mengikuti provider
    # yang dipilih di `ai_provider` agar has_ai_key/ai_model tidak selalu
    # menunjuk BangunWeb.
    _AI_KEY_FIELDS = {
        "bangunweb": ("bangunweb_api_key", "bangunweb_model"),
        "openai": ("openai_api_key", "openai_model"),
        "anthropic": ("anthropic_api_key", "anthropic_model"),
        "deepseek": ("deepseek_api_key", "deepseek_model"),
        "grok": ("xai_api_key", "xai_model"),
        "nous": ("nous_api_key", "nous_model"),
        "custom": ("ai_custom_api_key", "ai_custom_model"),
    }

    @property
    def has_ai_key(self) -> bool:
        fields = self._AI_KEY_FIELDS.get((self.ai_provider or "bangunweb").lower())
        if fields is None:
            return False
        return bool(getattr(self, fields[0], ""))

    @property
    def active_ai_model(self) -> str:
        fields = self._AI_KEY_FIELDS.get((self.ai_provider or "bangunweb").lower())
        if fields is None:
            return self.bangunweb_model
        return str(getattr(self, fields[1], "") or "")

    def __post_init__(self) -> None:
        """Validasi rentang parameter kritis — gagal = ConfigError (tolak start)."""
        errors = []
        if not (0 < self.risk_per_trade_pct <= 5):
            errors.append(f"RISK_PER_TRADE_PCT harus dalam (0, 5], kini {self.risk_per_trade_pct}")
        if not (1 <= self.max_leverage <= 20):
            errors.append(f"MAX_LEVERAGE harus dalam [1, 20], kini {self.max_leverage}")
        if self.daily_loss_limit_pct <= 0:
            errors.append(f"DAILY_LOSS_LIMIT_PCT harus > 0, kini {self.daily_loss_limit_pct}")
        if self.weekly_loss_limit_pct <= 0:
            errors.append(f"WEEKLY_LOSS_LIMIT_PCT harus > 0, kini {self.weekly_loss_limit_pct}")
        if self.min_risk_reward < 1:
            errors.append(f"MIN_RISK_REWARD harus >= 1, kini {self.min_risk_reward}")
        if self.max_open_positions < 1:
            errors.append(f"MAX_OPEN_POSITIONS harus >= 1, kini {self.max_open_positions}")
        if self.max_consecutive_losses < 0:
            errors.append(f"MAX_CONSECUTIVE_LOSSES harus >= 0 (0 = nonaktif), kini {self.max_consecutive_losses}")
        if not (0 < self.max_entry_deviation_pct <= 10):
            errors.append(f"MAX_ENTRY_DEVIATION_PCT harus dalam (0, 10], kini {self.max_entry_deviation_pct}")
        valid_intervals = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"}
        if self.default_interval not in valid_intervals:
            errors.append(f"DEFAULT_INTERVAL tidak dikenal: {self.default_interval!r}")
        if self.scoring_version not in (1, 2):
            errors.append(f"SCORING_VERSION harus 1 atau 2, kini {self.scoring_version}")
        if self.protection_working_type not in {"MARK_PRICE", "CONTRACT_PRICE"}:
            errors.append(f"PROTECTION_WORKING_TYPE tidak dikenal: {self.protection_working_type!r}")
        # Fase 6-B5: validasi parameter yang sebelumnya lolos diam-diam.
        if self.reconciler_interval_sec < 5:
            errors.append(f"RECONCILER_INTERVAL_SEC harus >= 5, kini {self.reconciler_interval_sec}")
        if not (0 < self.partial_tp_fraction < 1):
            errors.append(f"PARTIAL_TP_FRACTION harus dalam (0, 1), kini {self.partial_tp_fraction}")
        if self.max_hold_hours < 0:
            errors.append(f"MAX_HOLD_HOURS harus >= 0, kini {self.max_hold_hours}")
        if self.breakeven_trigger_r <= 0:
            errors.append(f"BREAKEVEN_TRIGGER_R harus > 0, kini {self.breakeven_trigger_r}")
        if self.trailing_trigger_r <= 0:
            errors.append(f"TRAILING_TRIGGER_R harus > 0, kini {self.trailing_trigger_r}")
        if self.trailing_atr_mult <= 0:
            errors.append(f"TRAILING_ATR_MULT harus > 0, kini {self.trailing_atr_mult}")
        if self.max_symbol_exposure_pct <= 0:
            errors.append(f"MAX_SYMBOL_EXPOSURE_PCT harus > 0, kini {self.max_symbol_exposure_pct}")
        if self.max_total_exposure_pct <= 0:
            errors.append(f"MAX_TOTAL_EXPOSURE_PCT harus > 0, kini {self.max_total_exposure_pct}")
        if self.rate_limit_per_minute < 0:
            errors.append(f"RATE_LIMIT_PER_MINUTE harus >= 0, kini {self.rate_limit_per_minute}")
        if self.web_api_token and len(self.web_api_token) < 24:
            errors.append("WEB_API_TOKEN terlalu pendek — minimal 24 karakter bila diisi")
        if errors:
            raise ConfigError("Konfigurasi tidak valid — " + "; ".join(errors))

    def public_summary(self) -> dict:
        """Ringkasan konfigurasi yang AMAN ditampilkan (tanpa secret)."""
        return {
            "binance_network": "testnet" if self.binance_testnet else "mainnet",
            "binance_keys_configured": self.has_binance_keys,
            "ai_configured": self.has_ai_key,
            "ai_model": self.active_ai_model,
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
    # Fase 6-A6: DATA_DIR tunggal & absolut agar state tidak bergantung CWD.
    data_dir = os.environ.get("DATA_DIR", "").strip() or str(
        Path(__file__).resolve().parent.parent / "data"
    )
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
        trade_journal_path=os.environ.get("TRADE_JOURNAL_PATH", "").strip() or f"{data_dir}/trades.jsonl",
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
        risk_per_trade_pct=_get_float("RISK_PER_TRADE_PCT", 1.0, critical=True),
        max_leverage=_get_int("MAX_LEVERAGE", 5, critical=True),
        max_open_positions=_get_int("MAX_OPEN_POSITIONS", 3, critical=True),
        daily_loss_limit_pct=_get_float("DAILY_LOSS_LIMIT_PCT", 3.0, critical=True),
        weekly_loss_limit_pct=_get_float("WEEKLY_LOSS_LIMIT_PCT", 6.0, critical=True),
        max_symbol_exposure_pct=_get_float("MAX_SYMBOL_EXPOSURE_PCT", 150.0, critical=True),
        max_total_exposure_pct=_get_float("MAX_TOTAL_EXPOSURE_PCT", 300.0, critical=True),
        min_risk_reward=_get_float("MIN_RISK_REWARD", 1.5, critical=True),
        max_consecutive_losses=_get_int("MAX_CONSECUTIVE_LOSSES", 3, critical=True),
        consecutive_loss_window_hours=_get_int("CONSECUTIVE_LOSS_WINDOW_HOURS", 24),
        protection_working_type=os.environ.get("PROTECTION_WORKING_TYPE", "MARK_PRICE").strip().upper(),
        reconciler_enabled=_get_bool("RECONCILER_ENABLED", True),
        reconciler_interval_sec=_get_int("RECONCILER_INTERVAL_SEC", 60),
        use_closed_candles_only=_get_bool("USE_CLOSED_CANDLES_ONLY", True),
        scoring_version=_get_int("SCORING_VERSION", 1),
        ai_can_initiate=_get_bool("AI_CAN_INITIATE", False),
        adx_min=_get_float("ADX_MIN", 20.0),
        mtf_gate_enabled=_get_bool("MTF_GATE_ENABLED", True),
        regime_gate_enabled=_get_bool("REGIME_GATE_ENABLED", True),
        move_sl_to_be=_get_bool("MOVE_SL_TO_BE", False),
        breakeven_trigger_r=_get_float("BREAKEVEN_TRIGGER_R", 1.0),
        breakeven_buffer_pct=_get_float("BREAKEVEN_BUFFER_PCT", 0.1),
        trailing_stop_enabled=_get_bool("TRAILING_STOP_ENABLED", False),
        trailing_trigger_r=_get_float("TRAILING_TRIGGER_R", 1.5),
        trailing_atr_mult=_get_float("TRAILING_ATR_MULT", 1.0),
        partial_tp_enabled=_get_bool("PARTIAL_TP_ENABLED", False),
        partial_tp_r=_get_float("PARTIAL_TP_R", 1.5),
        partial_tp_fraction=_get_float("PARTIAL_TP_FRACTION", 0.5),
        max_hold_hours=_get_float("MAX_HOLD_HOURS", 0.0),
        max_same_direction_positions=_get_int("MAX_SAME_DIRECTION_POSITIONS", 2),
        max_open_risk_pct=_get_float("MAX_OPEN_RISK_PCT", 0.0),
        min_quote_volume_usd=_get_float("MIN_QUOTE_VOLUME_USD", 0.0),
        funding_guard_minutes=_get_int("FUNDING_GUARD_MINUTES", 0),
        funding_extreme_pct=_get_float("FUNDING_EXTREME_PCT", 0.1),
        btc_filter=_get_bool("BTC_FILTER", False),
        risk_state_path=os.environ.get("RISK_STATE_PATH", "").strip() or f"{data_dir}/risk_state.json",
        data_dir=data_dir,
        reconciler_grace_sec=_get_int("RECONCILER_GRACE_SEC", 120),
        protection_retries=_get_int("PROTECTION_RETRIES", 3),
        web_api_token=os.environ.get("WEB_API_TOKEN", "").strip(),
        web_viewer_token=os.environ.get("WEB_VIEWER_TOKEN", "").strip(),
        cors_origins=os.environ.get("CORS_ORIGINS", "").strip(),
        trusted_proxies=os.environ.get("TRUSTED_PROXIES", "").strip(),
        rate_limit_per_minute=_get_int("RATE_LIMIT_PER_MINUTE", 240),
        mtf_enabled=_get_bool("MTF_ENABLED", True),
        market_ws_enabled=_get_bool("MARKET_WS_ENABLED", True),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        default_interval=os.environ.get("DEFAULT_INTERVAL", "15m").strip(),
        quote_asset=os.environ.get("QUOTE_ASSET", "USDT").strip().upper(),
        web_host=os.environ.get("WEB_HOST", "127.0.0.1").strip(),
        web_port=_get_int("WEB_PORT", 8000),
        allow_insecure_web=_get_bool("ALLOW_INSECURE_WEB", False),
        max_entry_deviation_pct=_get_float("MAX_ENTRY_DEVIATION_PCT", 0.3, critical=True),
    )
