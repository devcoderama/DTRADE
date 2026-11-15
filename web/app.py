"""Server Web UI binance-ai-future (FastAPI).

Endpoint REST tipis di atas core yang sama dengan CLI:

    GET  /api/health            status server + konfigurasi aman
    GET  /api/config            ringkasan konfigurasi (tanpa secret)
    GET  /api/account           saldo akun futures
    GET  /api/positions         posisi terbuka
    GET  /api/symbols           semua simbol perpetual aktif
    GET  /api/snapshot/{symbol} snapshot indikator satu simbol
    GET  /api/analyze/{symbol}  analisis teknikal + AI
    GET  /api/scan?top=N        pindai semua simbol (teknikal; AI opsional)
    POST /api/trade             {symbol, side?} → risk check → eksekusi
    POST /api/close/{symbol}    tutup posisi
    POST /api/broadcast         satu putaran siaran sinyal ke Telegram
    GET  /api/ai/account        sisa token AI BangunWeb

Selama server berjalan, siaran sinyal juga berjalan OTOMATIS di latar
belakang (lihat BROADCAST_* di .env) — tanpa perlu perintah terpisah.

Jalankan:  python run.py web   →  http://localhost:8000
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import load_settings
from core.ai_choice import (
    KNOWN_MODELS,
    build_provider as build_ai_provider,
    choice_source,
    load_choice,
    save_choice,
    settings_with_choice,
    state_mtime,
)
from core.ai_providers import describe_providers
from core.backtest import BacktestConfig, Backtester
from core.binance_client import BinanceAPIError, BinanceFuturesClient
from core.broadcaster import run_broadcast
from core.database import build_database
from core.executor import OrderExecutor
from core.journal import TradeJournal
from core.market_stream import MarketStream
from core.position_manager import PositionManager
from core.reconciler import Reconciler
from core.service import TradingService
from core.notifier import build_notifier
from core.risk import RiskManager
from core.strategy import SignalEngine
from core.symbols import SymbolManager
from utils.logger import get_logger

log = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

settings = load_settings()
binance = BinanceFuturesClient(settings)
ai_client = build_ai_provider(settings)
notifier = build_notifier(settings)
database = build_database(settings)
journal = TradeJournal(settings.trade_journal_path, db=database)
backtester = Backtester(binance)
symbol_manager = SymbolManager(binance, settings)
engine = SignalEngine(settings, binance, ai_client)

# Pilihan AI (provider+model) dapat diganti saat runtime lewat Web UI / CLI
# dan disimpan di data/ai_settings.json. Server memuat ulang otomatis bila
# file itu berubah dari luar (mis. perintah CLI `ai-model`).
_ai_mtime: list = [state_mtime()]


def _refresh_ai_from_store() -> None:
    global ai_client
    m = state_mtime()
    if m is not None and m != _ai_mtime[0]:
        _ai_mtime[0] = m
        ai_client = build_ai_provider(settings)
        engine.set_ai(ai_client)
        provider, model = load_choice(settings)
        log.info("Pilihan AI dimuat ulang: %s:%s", provider, model)
risk_manager = RiskManager(settings, binance, symbol_manager)
executor = OrderExecutor(settings, binance, symbol_manager, db=database)
executor.set_intent_hook(journal.record_intent)  # write-ahead jurnal (Fase 6-A1)
market_stream = MarketStream(settings)
trading_service = TradingService(settings, risk_manager, executor, binance)
position_manager = PositionManager(settings, binance, executor, journal, symbol_manager)
reconciler = Reconciler(settings, binance, executor, journal, position_manager=position_manager)



@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Pengganti @app.on_event('startup') yang deprecated."""
    _validate_startup()
    _start_auto_broadcast()
    if settings.reconciler_enabled:
        reconciler.start()
    yield
    reconciler.stop()


app = FastAPI(title="binance-ai-future", version="1.0.0", lifespan=_lifespan)

# ---------------------------------------------------------------------------
# Keamanan API (P0):
# - CORS: TIDAK memakai wildcard. Hanya origin yang tercantum di
#   CORS_ORIGINS (.env) yang diizinkan; kosong = tanpa CORS (same-origin).
# - Autentikasi token (bila WEB_API_TOKEN diisi): semua endpoint /api/*
#   selain /api/health wajib membawa token lewat header X-API-Token
#   atau cookie sesi `baf_token` (SameSite=Strict, HttpOnly — proteksi
#   CSRF untuk jalur cookie). Token utama = akses penuh; WEB_VIEWER_TOKEN
#   = hanya baca (GET). Bila token kosong, autentikasi nonaktif dan server
#   memberi peringatan keras saat start — hanya aman untuk lokal sendiri.
# - Rate limit per IP untuk /api/* dan batas ukuran request 1 MB.
# ---------------------------------------------------------------------------
MAX_REQUEST_BYTES = 1_048_576


LOGIN_RATE_PER_MINUTE = 5  # percobaan login per IP — jauh lebih ketat dari batas umum


class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_token: str, viewer_token: str, rate_per_minute: int):
        super().__init__(app)
        self._api_token = api_token
        self._viewer_token = viewer_token
        self._rate = rate_per_minute
        self._hits: dict[str, deque] = {}
        self._login_hits: dict[str, deque] = {}
        self._hits_lock = threading.Lock()

    def _role(self, request: Request) -> Optional[str]:
        import hmac

        token = request.headers.get("x-api-token") or request.cookies.get("baf_token") or ""
        if self._api_token and hmac.compare_digest(token, self._api_token):
            return "admin"
        if self._viewer_token and hmac.compare_digest(token, self._viewer_token):
            return "viewer"
        return None

    @staticmethod
    def _limited(store: dict, client_ip: str, limit: int) -> bool:
        """Jendela geser 60 detik; bucket kosong dibuang agar memori terbatas."""
        if limit <= 0:
            return False
        now = time.time()
        bucket = store.setdefault(client_ip, deque())
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= limit:
            return True
        bucket.append(now)
        if len(store) > 5000:  # sapu bucket yang sudah kosong/kedaluwarsa
            for ip in [ip for ip, b in store.items() if not b or b[-1] < now - 60]:
                store.pop(ip, None)
        return False

    def _rate_limited(self, client_ip: str) -> bool:
        with self._hits_lock:
            return self._limited(self._hits, client_ip, self._rate)

    def _login_limited(self, client_ip: str) -> bool:
        with self._hits_lock:
            return self._limited(self._login_hits, client_ip, LOGIN_RATE_PER_MINUTE)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        length = request.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > MAX_REQUEST_BYTES:
                    return JSONResponse(status_code=413, content={"detail": "Ukuran request melebihi batas 1 MB."})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Content-Length tidak valid."})
        if path == "/api/health":
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        if path == "/api/auth/login":
            # Batas login TERPISAH dan jauh lebih ketat; token diperiksa endpoint.
            if self._login_limited(client_ip):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Terlalu banyak percobaan login — tunggu satu menit."},
                    headers={"Retry-After": "60"},
                )
            return await call_next(request)
        if self._rate_limited(client_ip):
            return JSONResponse(
                status_code=429,
                content={"detail": "Terlalu banyak request — coba lagi sebentar lagi."},
                headers={"Retry-After": "30"},
            )
        if self._api_token:
            role = self._role(request)
            if role is None:
                return JSONResponse(status_code=401, content={"detail": "Token API diperlukan (header X-API-Token)."})
            if role == "viewer" and request.method not in {"GET", "HEAD", "OPTIONS"}:
                return JSONResponse(status_code=403, content={"detail": "Token viewer hanya boleh membaca (GET)."})
        return await call_next(request)


app.add_middleware(
    SecurityMiddleware,
    api_token=settings.web_api_token,
    viewer_token=settings.web_viewer_token,
    rate_per_minute=settings.rate_limit_per_minute,
)
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Token"],
    )

# ---------------------------------------------------------------------------
# Siaran sinyal otomatis ke Telegram — thread latar belakang selama server
# web berjalan. Putaran pertama ±30 detik setelah start, lalu berkala
# sesuai BROADCAST_INTERVAL_MINUTES. Kunci (lock) yang sama dipakai
# endpoint manual /api/broadcast agar dua putaran tidak berjalan bersamaan.
# ---------------------------------------------------------------------------
def _time_sync_loop() -> None:
    """Segarkan offset jam Binance tiap 30 menit (best-effort, daemon)."""
    while True:
        time.sleep(1800)
        try:
            binance.sync_time()
        except Exception as exc:
            log.warning("Sinkronisasi waktu berkala gagal: %s", exc)


_broadcast_lock = threading.Lock()
_broadcast_cache: dict[str, float] = {}
BROADCAST_FIRST_DELAY_SEC = 30


def _auto_broadcast_loop() -> None:
    interval = max(1, settings.broadcast_interval_minutes) * 60
    time.sleep(BROADCAST_FIRST_DELAY_SEC)
    while True:
        try:
            if _broadcast_lock.acquire(blocking=False):
                try:
                    _refresh_ai_from_store()
                    run_broadcast(
                        settings, engine, binance, notifier,
                        symbol_manager.all_symbols(),
                        min_score=settings.broadcast_min_score,
                        max_signals=settings.broadcast_max_signals,
                        sent_cache=_broadcast_cache,
                        cooldown_sec=int(settings.broadcast_cooldown_hours * 3600),
                    )
                finally:
                    _broadcast_lock.release()
        except Exception as exc:  # satu putaran gagal tidak boleh mematikan loop
            log.warning("Siaran otomatis gagal pada putaran ini: %s", exc)
        time.sleep(interval)


def enforce_web_safety() -> None:
    """Tolak konfigurasi web yang berbahaya. Dipanggil sebelum server naik.

    Aturan: tanpa WEB_API_TOKEN, server hanya boleh (a) mode dry-run DAN
    (b) terikat ke loopback. Pengecualian sadar-risiko: ALLOW_INSECURE_WEB=true.
    """
    if settings.web_api_token:
        return
    if settings.allow_insecure_web:
        log.warning(
            "⚠⚠ ALLOW_INSECURE_WEB=true: server berjalan TANPA autentikasi atas "
            "keputusan eksplisit Anda. Jangan pernah memakai ini untuk mode LIVE "
            "atau di jaringan yang tidak sepenuhnya Anda percaya."
        )
        return
    loopback = settings.web_host in {"127.0.0.1", "::1", "localhost"}
    if settings.is_live:
        raise RuntimeError(
            "MENOLAK START: TRADING_MODE=live tanpa WEB_API_TOKEN. Isi WEB_API_TOKEN "
            "di .env (token acak panjang), atau set ALLOW_INSECURE_WEB=true bila Anda "
            "sadar penuh risikonya."
        )
    if not loopback:
        raise RuntimeError(
            f"MENOLAK START: WEB_HOST={settings.web_host} (bukan loopback) tanpa "
            "WEB_API_TOKEN — siapa pun di jaringan dapat mengeksekusi trade. Isi "
            "WEB_API_TOKEN di .env, kembalikan WEB_HOST=127.0.0.1, atau set "
            "ALLOW_INSECURE_WEB=true bila Anda sadar penuh risikonya."
        )
    log.warning(
        "Autentikasi API nonaktif (WEB_API_TOKEN kosong) — diizinkan hanya karena "
        "mode dry-run + bind loopback (%s).", settings.web_host,
    )


def _validate_startup() -> None:
    """Validasi keamanan & pemulihan saat server mulai."""
    enforce_web_safety()
    # Fase 6-A6: DATA_DIR wajib bisa ditulis saat LIVE (state risiko
    # harus persisten), dan selfcheck dijalankan otomatis sebagai
    # laporan kesiapan di log startup.
    from core.selfcheck import assert_data_writable, hedge_mode_active, run_selfcheck

    assert_data_writable(settings)
    try:
        for c in run_selfcheck(settings, binance=binance, database=database, ai=ai_client):
            if c.ok is False:
                log.warning("SELFCHECK %s: GAGAL — %s", c.name, c.detail)
            elif c.ok is True:
                log.info("SELFCHECK %s: OK — %s", c.name, c.detail)
    except Exception as exc:
        log.warning("Selfcheck startup gagal dijalankan: %s", exc)
    # Fase 6-A7: Hedge Mode tidak didukung — tolak trading saat live.
    if settings.is_live and settings.has_binance_keys:
        if hedge_mode_active(binance):
            raise RuntimeError(
                "MENOLAK START: akun Binance berada di Hedge Mode — bot tidak "
                "mendukung positionSide. Ubah akun ke One-way Mode dulu."
            )
    if settings.is_live:
        log.warning("⚠ MODE LIVE AKTIF — order akan dikirim dengan dana sungguhan.")
    if settings.is_live and not settings.has_binance_keys:
        log.error("MODE LIVE tetapi kunci Binance kosong — eksekusi pasti gagal.")
    if settings.has_binance_keys:
        # Sinkronkan jam dengan Binance (best-effort) + segarkan berkala,
        # agar request bertanda tangan tidak ditolak dengan error -1021.
        try:
            binance.sync_time()
            log.info("Sinkronisasi waktu Binance selesai.")
        except Exception as exc:
            log.warning("Sinkronisasi waktu awal gagal (dilanjutkan): %s", exc)
        threading.Thread(target=_time_sync_loop, name="time-sync", daemon=True).start()
    pending = executor.pending_recovery()
    for rec in pending:
        log.warning(
            "Pemulihan: eksekusi %s (%s) berakhir di status %s — periksa posisi & "
            "perlindungannya di Binance.",
            rec.get("symbol"), rec.get("client_order_id"), rec.get("state"),
        )


def _start_auto_broadcast() -> None:
    if not settings.broadcast_enabled:
        log.info("Siaran otomatis nonaktif (BROADCAST_ENABLED=false).")
        return
    if not notifier.enabled:
        log.warning(
            "Siaran otomatis nonaktif: Telegram belum dikonfigurasi "
            "(isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID di .env)."
        )
        return
    threading.Thread(target=_auto_broadcast_loop, name="auto-broadcast", daemon=True).start()
    log.info(
        "Siaran otomatis AKTIF: putaran pertama ±%d detik, lalu tiap %d menit "
        "(skor ≥%d, maks %d sinyal/putaran, jeda siar ulang %.0f jam).",
        BROADCAST_FIRST_DELAY_SEC,
        settings.broadcast_interval_minutes,
        settings.broadcast_min_score,
        settings.broadcast_max_signals,
        settings.broadcast_cooldown_hours,
    )


def _guard(fn):
    try:
        return fn()
    except BinanceAPIError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        # Jangan bocorkan detail internal ke klien; catat lengkap di log server.
        log.exception("Kesalahan internal saat memproses request")
        raise HTTPException(status_code=500, detail="Kesalahan internal server — lihat log server.") from None


def _signal_payload(signal) -> dict:
    return {
        "symbol": signal.symbol,
        "side": signal.side,
        "confidence": signal.confidence,
        "price": signal.price,
        "stop_loss": signal.stop_loss,
        "take_profit": signal.take_profit,
        "technical_score": signal.technical_score,
        "regime": getattr(signal, "regime", None),
        "mtf": getattr(signal, "mtf", {}),
        "snapshot": signal.snapshot,
        "ai": (
            {
                "signal": signal.ai.signal,
                "confidence": signal.ai.confidence,
                "reasoning": signal.ai.reasoning,
                "risks": signal.ai.risks,
                "model_used": signal.ai.model_used,
            }
            if signal.ai
            else None
        ),
        "reasons": signal.reasons,
    }


# ----------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    # Sengaja minimal: endpoint publik ini tidak membocorkan konfigurasi.
    # Ringkasan konfigurasi & status komponen ada di /api/config (bertoken).
    return {"ok": True, "service": "binance-ai-future"}


@app.websocket("/api/ws/klines/{symbol}")
async def ws_klines(websocket: WebSocket, symbol: str, interval: Optional[str] = None) -> None:
    """Dorong pembaruan candle real-time ke grafik Web UI.

    Riwayat tetap dimuat lewat REST; endpoint ini hanya meneruskan
    candle yang sedang berjalan dari MarketStream (Binance WebSocket).
    Autentikasi diperiksa manual di sini (middleware HTTP tidak menjangkau
    WebSocket): token dari query `?token=` atau cookie sesi.
    """
    import asyncio

    if settings.web_api_token:
        import hmac

        # Urutan kepercayaan: cookie sesi → header → query (?token= DEPRECATED,
        # tetap diterima untuk kompatibilitas; jangan dipakai — token di URL
        # mudah bocor lewat log & riwayat peramban).
        token = (
            websocket.cookies.get("baf_token")
            or websocket.headers.get("x-api-token")
            or websocket.query_params.get("token")
            or ""
        )
        ok = hmac.compare_digest(token, settings.web_api_token) or (
            bool(settings.web_viewer_token) and hmac.compare_digest(token, settings.web_viewer_token)
        )
        if not ok:
            await websocket.close(code=4401)
            return
    await websocket.accept()
    if not settings.market_ws_enabled:
        await websocket.send_json({"type": "disabled"})
        await websocket.close()
        return

    symbol = symbol.upper()
    interval = interval or settings.default_interval
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _listener(kind: str, payload: dict) -> None:
        if kind == "kline" and payload.get("symbol") == symbol and payload.get("interval") == interval:
            loop.call_soon_threadsafe(queue.put_nowait, payload)

    market_stream.add_listener(_listener)
    market_stream.subscribe_kline(symbol, interval)
    try:
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        market_stream.remove_listener(_listener)
        market_stream.unsubscribe_kline(symbol, interval)


@app.get("/api/config")
def config() -> dict:
    summary = settings.public_summary()
    provider, model = load_choice(settings)
    summary["ai_provider"] = provider
    summary["ai_model"] = model
    summary["ai_configured"] = bool(ai_client is not None and ai_client.is_configured())
    summary["database"] = database.health() if database is not None else {"configured": False, "ok": None}
    summary["market_stream"] = market_stream.health()
    return summary


@app.get("/api/account")
def account() -> dict:
    def _load():
        data = binance.account()
        usdt = binance.asset_balance(settings.quote_asset)
        return {
            "wallet_balance": float(data.get("totalWalletBalance", 0)),
            "unrealized_pnl": float(data.get("totalUnrealizedProfit", 0)),
            "available": float(usdt.get("availableBalance", 0)),
            "positions": binance.positions(),
        }

    return _guard(_load)


@app.get("/api/positions")
def positions() -> list:
    return _guard(lambda: binance.positions())


@app.get("/api/symbols")
def symbols() -> dict:
    return _guard(lambda: {"symbols": symbol_manager.all_symbols()})


@app.get("/api/klines/{symbol}")
def klines(symbol: str, interval: Optional[str] = None, limit: int = Query(120, ge=10, le=500)) -> dict:
    """Data OHLC untuk grafik candlestick Web UI."""

    def _load():
        raw = binance.klines(symbol.upper(), interval or settings.default_interval, limit)
        candles = [
            {
                "time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
            for k in raw
        ]
        return {"symbol": symbol.upper(), "interval": interval or settings.default_interval, "candles": candles}

    return _guard(_load)


@app.get("/api/snapshot/{symbol}")
def snapshot(symbol: str, interval: Optional[str] = None) -> dict:
    return _guard(lambda: engine.snapshot_for(symbol.upper(), interval))


@app.get("/api/analyze/{symbol}")
def analyze(symbol: str, interval: Optional[str] = None, ai: bool = True) -> dict:
    _refresh_ai_from_store()

    def _run():
        signal = engine.analyze(symbol.upper(), interval, use_ai=ai)
        journal.record_ai_decision(signal, provider=engine.ai_name)
        return _signal_payload(signal)

    return _guard(_run)


@app.get("/api/performance")
def performance() -> dict:
    """Metrik performa dari jurnal (trade tertutup): win rate, PF, dst."""
    return _guard(lambda: journal.performance())


@app.get("/api/scan")
def scan(top: int = Query(10, ge=1, le=50), interval: Optional[str] = None, ai: bool = False) -> dict:
    def _run():
        _refresh_ai_from_store()
        signals = engine.scan(symbol_manager.all_symbols(), interval=interval, use_ai=ai)
        return {"results": [_signal_payload(s) for s in signals[:top]]}

    return _guard(_run)


class TradeRequest(BaseModel):
    symbol: str
    side: Optional[str] = None  # LONG | SHORT; kosong = ikuti sinyal mesin

    @field_validator("symbol")
    @classmethod
    def _symbol_ok(cls, v: str) -> str:
        v = v.strip().upper()
        if not v or len(v) > 20 or not v.isalnum():
            raise ValueError("Simbol tidak valid")
        return v

    @field_validator("side")
    @classmethod
    def _side_ok(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().upper()
        if v not in {"LONG", "SHORT"}:
            raise ValueError("side harus LONG atau SHORT")
        return v


class AuthRequest(BaseModel):
    token: str


@app.post("/api/auth/login")
def auth_login(req: AuthRequest, request: Request) -> JSONResponse:
    """Tukar token API menjadi cookie sesi (untuk Web UI di peramban)."""
    import hmac

    if not settings.web_api_token:
        return JSONResponse({"ok": True, "role": "admin", "auth_enabled": False})
    if hmac.compare_digest(req.token, settings.web_api_token):
        role = "admin"
    elif settings.web_viewer_token and hmac.compare_digest(req.token, settings.web_viewer_token):
        role = "viewer"
    else:
        raise HTTPException(status_code=401, detail="Token salah.")
    # Cookie Secure hanya pada HTTPS (langsung atau lewat reverse proxy).
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    resp = JSONResponse({"ok": True, "role": role, "auth_enabled": True})
    resp.set_cookie(
        "baf_token", req.token, httponly=True, samesite="strict", secure=is_https,
        path="/api", max_age=7 * 24 * 3600,
    )
    return resp


@app.post("/api/trade")
def trade(req: TradeRequest) -> dict:
    def _run():
        import dataclasses

        # Eksekusi WAJIB analisis segar (melewati cache 180 detik).
        signal = engine.analyze(req.symbol.upper(), use_ai=True, fresh=True)
        if req.side:
            from core.strategy import _atr_levels

            side = req.side.upper()
            sl, tp = _atr_levels(signal.price, signal.snapshot.get("atr14"), side)
            # Sinyal TURUNAN — sinyal asli tidak dimutasi; alasan eksplisit.
            signal = dataclasses.replace(
                signal, side=side, stop_loss=sl, take_profit=tp,
                reasons=[*signal.reasons, f"Override manual pengguna: sisi dipaksa {side}; SL/TP dari ATR."],
            )
        if signal.side == "NEUTRAL":
            return {"executed": False, "note": "Sinyal NEUTRAL — tidak dieksekusi.", "signal": _signal_payload(signal)}
        # evaluate + execute atomik di dalam TradingService (satu lock),
        # termasuk validasi penyimpangan harga terkini.
        decision, result = trading_service.submit(signal)
        if result is None:
            return {
                "executed": False,
                "note": "Ditolak RiskManager / validasi harga segar.",
                "reasons": decision.reasons,
                "signal": _signal_payload(signal),
            }
        journal.record_execution(decision, result, signal.price)
        if notifier.enabled:
            notifier.notify_execution(decision, result, signal.price)
        return {
            "executed": True,
            "dry_run": result.dry_run,
            "notes": result.notes,
            "decision": {
                "quantity": decision.quantity,
                "leverage": decision.leverage,
                "notional": decision.notional,
                "stop_loss": decision.stop_loss,
                "take_profit": decision.take_profit,
            },
        }

    return _guard(_run)


@app.post("/api/close/{symbol}")
def close(symbol: str) -> dict:
    return _guard(lambda: executor.close_position(symbol.upper()))


@app.get("/api/ai/account")
def ai_account() -> dict:
    def _load():
        providers = describe_providers(settings_with_choice(settings))
        active = next((p for p in providers if p["active"]), None)
        payload: dict = {"providers": providers, "active": active}
        if ai_client is not None:
            payload["last_usage"] = getattr(ai_client, "last_usage", None)
            health = getattr(ai_client, "health", None)
            if callable(health):
                try:
                    payload["health"] = health()
                except Exception as exc:  # health bersifat informatif
                    payload["health"] = {"ok": False, "error": str(exc)[:160]}
        if ai_client is not None and ai_client.name == "bangunweb" and ai_client.is_configured():
            payload["bangunweb_account"] = ai_client.account()
            payload["tokens_remaining"] = payload["bangunweb_account"].get("tokens_remaining")
        return payload

    return _guard(_load)


@app.get("/api/ai/models")
def ai_models() -> dict:
    """Daftar provider + saran model untuk panel ganti model di Web UI."""
    provider, model = load_choice(settings)
    live: list[str] = []
    if ai_client is not None and ai_client.name == "bangunweb" and ai_client.is_configured():
        try:
            live = [str(m.get("id")) for m in ai_client.list_models() if m.get("id")]
        except Exception:
            live = []  # daftar live bersifat tambahan; saran statis tetap ada
    return {
        "current": {"provider": provider, "model": model, "source": choice_source()},
        "providers": describe_providers(settings_with_choice(settings)),
        "known_models": KNOWN_MODELS,
        "bangunweb_live_models": live,
    }


class AIModelRequest(BaseModel):
    provider: str
    model: str


@app.post("/api/ai/model")
def ai_set_model(req: AIModelRequest) -> dict:
    """Ganti provider+model AI saat runtime — tanpa edit .env, tanpa restart."""
    global ai_client
    try:
        save_choice(req.provider, req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ai_client = build_ai_provider(settings)
    engine.set_ai(ai_client)
    _ai_mtime[0] = state_mtime()
    provider, model = load_choice(settings)
    configured = bool(ai_client is not None and ai_client.is_configured())
    return {
        "ok": True,
        "provider": provider,
        "model": model,
        "configured": configured,
        "warning": None if configured else (
            f"Provider {provider} belum memiliki kunci API di .env — "
            "analisis berjalan teknikal murni sampai kuncinya diisi."
        ),
    }


@app.get("/api/backtest/{symbol}")
def backtest(
    symbol: str,
    interval: Optional[str] = None,
    limit: int = Query(1000, ge=100, le=1500),
    balance: float = Query(1000.0, gt=0),
    risk: float = Query(1.0, gt=0, le=10),
    min_score: int = Query(40, ge=10, le=100),
) -> dict:
    def _run():
        config = BacktestConfig(initial_balance=balance, risk_pct=risk, min_score=min_score)
        result = backtester.run(
            symbol.upper(), interval=interval or settings.default_interval, limit=limit, config=config
        )
        return result.to_dict()

    return _guard(_run)


@app.get("/api/journal")
def journal_entries(limit: int = Query(20, ge=1, le=200)) -> dict:
    return {"entries": journal.recent(limit)}


@app.post("/api/notify/test")
def notify_test() -> dict:
    if not notifier.enabled:
        raise HTTPException(
            status_code=400,
            detail="Telegram belum dikonfigurasi: isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID di .env",
        )
    return {"sent": notifier.notify_test()}


@app.post("/api/broadcast")
def broadcast(min_score: int = Query(50, ge=10, le=100), max_signals: int = Query(3, ge=1, le=5)) -> dict:
    """Satu putaran siaran sinyal ke Telegram: pindai → saring → AI → foto grafik.

    Hanya sinyal kuat yang dikonfirmasi AI (LONG/SHORT) yang terkirim;
    bila tidak ada yang lolos, tidak ada pesan yang dikirim.
    """
    if not notifier.enabled:
        raise HTTPException(
            status_code=400,
            detail="Telegram belum dikonfigurasi: isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID di .env",
        )

    def _run() -> dict:
        _refresh_ai_from_store()
        with _broadcast_lock:
            return run_broadcast(
                settings, engine, binance, notifier, symbol_manager.all_symbols(),
                min_score=min_score, max_signals=max_signals,
            )

    return _guard(_run)


# ----------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def run() -> None:
    import uvicorn

    enforce_web_safety()  # gagal cepat dengan pesan jelas, sebelum port dibuka
    log.info(
        "Web UI berjalan di http://%s:%d (mode %s)",
        settings.web_host,
        settings.web_port,
        "LIVE" if settings.is_live else "DRY-RUN",
    )
    uvicorn.run(app, host=settings.web_host, port=settings.web_port)


if __name__ == "__main__":
    run()
