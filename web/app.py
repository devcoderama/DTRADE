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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.settings import load_settings
from core.ai_providers import create_ai_provider, describe_providers
from core.backtest import BacktestConfig, Backtester
from core.binance_client import BinanceAPIError, BinanceFuturesClient
from core.broadcaster import run_broadcast
from core.executor import OrderExecutor
from core.journal import TradeJournal
from core.notifier import build_notifier
from core.risk import RiskManager
from core.strategy import SignalEngine
from core.symbols import SymbolManager
from utils.logger import get_logger

log = get_logger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

settings = load_settings()
binance = BinanceFuturesClient(settings)
ai_client = create_ai_provider(settings)
notifier = build_notifier(settings)
journal = TradeJournal(settings.trade_journal_path)
backtester = Backtester(binance)
symbol_manager = SymbolManager(binance, settings)
engine = SignalEngine(settings, binance, ai_client)
risk_manager = RiskManager(settings, binance, symbol_manager)
executor = OrderExecutor(settings, binance, symbol_manager)

app = FastAPI(title="binance-ai-future", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Siaran sinyal otomatis ke Telegram — thread latar belakang selama server
# web berjalan. Putaran pertama ±30 detik setelah start, lalu berkala
# sesuai BROADCAST_INTERVAL_MINUTES. Kunci (lock) yang sama dipakai
# endpoint manual /api/broadcast agar dua putaran tidak berjalan bersamaan.
# ---------------------------------------------------------------------------
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


@app.on_event("startup")
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
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _signal_payload(signal) -> dict:
    return {
        "symbol": signal.symbol,
        "side": signal.side,
        "confidence": signal.confidence,
        "price": signal.price,
        "stop_loss": signal.stop_loss,
        "take_profit": signal.take_profit,
        "technical_score": signal.technical_score,
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
    return {"ok": True, "service": "binance-ai-future", "config": settings.public_summary()}


@app.get("/api/config")
def config() -> dict:
    return settings.public_summary()


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
    return _guard(lambda: _signal_payload(engine.analyze(symbol.upper(), interval, use_ai=ai)))


@app.get("/api/scan")
def scan(top: int = Query(10, ge=1, le=50), interval: Optional[str] = None, ai: bool = False) -> dict:
    def _run():
        signals = engine.scan(symbol_manager.all_symbols(), interval=interval, use_ai=ai)
        return {"results": [_signal_payload(s) for s in signals[:top]]}

    return _guard(_run)


class TradeRequest(BaseModel):
    symbol: str
    side: Optional[str] = None  # LONG | SHORT; kosong = ikuti sinyal mesin


@app.post("/api/trade")
def trade(req: TradeRequest) -> dict:
    def _run():
        signal = engine.analyze(req.symbol.upper(), use_ai=True)
        if req.side:
            from core.strategy import _atr_levels

            signal.side = req.side.upper()
            sl, tp = _atr_levels(signal.price, signal.snapshot.get("atr14"), signal.side)
            signal.stop_loss, signal.take_profit = sl, tp
        if signal.side == "NEUTRAL":
            return {"executed": False, "note": "Sinyal NEUTRAL — tidak dieksekusi.", "signal": _signal_payload(signal)}
        decision = risk_manager.evaluate(signal)
        if not decision.allowed:
            return {
                "executed": False,
                "note": "Ditolak RiskManager.",
                "reasons": decision.reasons,
                "signal": _signal_payload(signal),
            }
        result = executor.execute(decision, signal.price)
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
        providers = describe_providers(settings)
        active = next((p for p in providers if p["active"]), None)
        payload: dict = {"providers": providers, "active": active}
        if ai_client is not None and ai_client.name == "bangunweb" and ai_client.is_configured():
            payload["bangunweb_account"] = ai_client.account()
            payload["tokens_remaining"] = payload["bangunweb_account"].get("tokens_remaining")
        return payload

    return _guard(_load)


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

    log.info(
        "Web UI berjalan di http://%s:%d (mode %s)",
        settings.web_host,
        settings.web_port,
        "LIVE" if settings.is_live else "DRY-RUN",
    )
    uvicorn.run(app, host=settings.web_host, port=settings.web_port)


if __name__ == "__main__":
    run()
