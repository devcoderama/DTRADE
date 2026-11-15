"""Antarmuka terminal binance-ai-future.

Contoh:
    python run.py cli balance
    python run.py cli symbols
    python run.py cli scan --top 10 [--ai]
    python run.py cli analyze BTCUSDT
    python run.py cli positions
    python run.py cli trade BTCUSDT --side LONG
    python run.py cli close BTCUSDT
    python run.py cli ai-account

Semua perintah trading melewati SignalEngine → RiskManager → OrderExecutor,
persis seperti Web UI. Dalam mode live, `trade` meminta konfirmasi manual.
"""

from __future__ import annotations

import argparse
import sys
import time

from config.settings import Settings, load_settings
from core.ai_providers import create_ai_provider, describe_providers
from core.ai_choice import (
    KNOWN_MODELS as AI_KNOWN_MODELS,
    PROVIDERS as AI_PROVIDERS,
    build_provider as build_ai_provider,
    choice_source as ai_choice_source,
    default_model as ai_default_model,
    load_choice as load_ai_choice,
    normalize_provider as normalize_ai_provider,
    save_choice as save_ai_choice,
)
from core.backtest import BacktestConfig, Backtester
from core.binance_client import BinanceAPIError, BinanceFuturesClient
from core.broadcaster import run_broadcast
from core.executor import OrderExecutor
from core.database import build_database
from core.journal import TradeJournal
from core.service import TradingService
from core.notifier import build_notifier
from core.risk import RiskManager
from core.strategy import SignalEngine
from core.symbols import SymbolManager
from utils.formatting import fmt_pct, fmt_price, fmt_qty, fmt_usd
from utils.logger import get_logger

log = get_logger(__name__)

CLI_VERSION = "1.0.0"

_LOGO_LINES = ("  ◆  ", "◆ ◆ ◆", "  ◆  ")


def print_banner(app: "App") -> None:
    """Kepala branding ala mockup CLI: logo, nama, versi, status, jam lokal."""
    s = app.settings
    net = "TESTNET" if s.binance_testnet else "MAINNET"
    mode = "LIVE" if s.is_live else "DRY-RUN"
    now = time.strftime("%H:%M:%S")
    if HAS_RICH:
        from rich.text import Text

        logo = Text("\n".join(_LOGO_LINES), style="bold #f0b90b")
        title = Text.from_markup(
            f"binance-[bold #f0b90b]ai[/bold #f0b90b]-future  [dim]— cli v{CLI_VERSION}[/dim]\n"
            f"[dim]Leveraging AI for Smarter Crypto Trading Decisions.[/dim]\n"
            f"[#0ecb81]●[/] Live Market · {net} · {mode} · {now}"
        )
        from rich.columns import Columns

        console.print(Columns([logo, title], padding=(0, 2)))
    else:
        print(f"binance-ai-future — cli v{CLI_VERSION} · {net} · {mode} · {now}")

try:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    HAS_RICH = True
except Exception:  # pragma: no cover
    HAS_RICH = False
    console = None  # type: ignore


def _print(text: str = "") -> None:
    if HAS_RICH:
        console.print(text)
    else:
        print(text)


class App:
    """Merakit semua komponen core sekali pakai untuk seluruh perintah."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.binance = BinanceFuturesClient(settings)
        self.ai = build_ai_provider(settings)
        self.notifier = build_notifier(settings)
        self.database = build_database(settings)
        self.journal = TradeJournal(settings.trade_journal_path, db=self.database)
        self.symbols = SymbolManager(self.binance, settings)
        self.engine = SignalEngine(settings, self.binance, self.ai)
        self.risk = RiskManager(settings, self.binance, self.symbols)
        self.executor = OrderExecutor(settings, self.binance, self.symbols, db=self.database)
        self.service = TradingService(settings, self.risk, self.executor, self.binance)


# ----------------------------------------------------------------------
# Perintah-perintah
# ----------------------------------------------------------------------
def cmd_dashboard(app: App, args) -> int:
    from cli.dashboard import run_dashboard

    return run_dashboard(app, symbol=args.symbol, interval=args.interval)


def cmd_chart(app: App, args) -> int:
    from cli.term_chart import chart_available, render_chart

    if not chart_available():
        _print("[red]Grafik terminal membutuhkan paket 'plotext'.[/red] Pasang: pip install -r requirements.txt")
        return 1
    interval = args.interval or app.settings.default_interval
    klines = app.binance.klines(args.symbol.upper(), interval, args.limit)
    if not klines:
        _print("Tidak ada data kline untuk simbol itu.")
        return 1
    print_banner(app)
    last = klines[-1]
    _print(f"[bold]{args.symbol.upper()}[/bold] {interval} — harga terakhir {fmt_price(float(last[4]))} "
           f"(EMA20 oranye · EMA50 abu-abu · volume di panel bawah)")
    if HAS_RICH:
        from rich.text import Text

        width = max(50, min(console.width - 2, 160))
        console.print(Text.from_ansi(render_chart(klines, width=width, height=30)))
    else:
        print(render_chart(klines, width=100, height=30))
    return 0


def cmd_balance(app: App, _args) -> int:
    print_banner(app)
    account = app.binance.account()
    usdt = app.binance.asset_balance(app.settings.quote_asset)
    _print(f"[bold]Akun Futures[/bold] ({'TESTNET' if app.settings.binance_testnet else 'MAINNET'})")
    _print(f"  Saldo dompet     : {fmt_usd(float(account.get('totalWalletBalance', 0)))}")
    _print(f"  PnL belum realise: {fmt_usd(float(account.get('totalUnrealizedProfit', 0)))}")
    _print(f"  Tersedia (USDT)  : {fmt_usd(float(usdt.get('availableBalance', 0)))}")
    _print(f"  Mode trading     : {'LIVE' if app.settings.is_live else 'DRY-RUN (simulasi)'}")
    return 0


def cmd_symbols(app: App, _args) -> int:
    symbols = app.symbols.all_symbols()
    _print(f"{len(symbols)} simbol perpetual {app.settings.quote_asset} aktif:")
    _print(", ".join(symbols))
    return 0


def cmd_scan(app: App, args) -> int:
    symbols = app.symbols.all_symbols()
    _print(f"Memindai {len(symbols)} simbol (interval {args.interval or app.settings.default_interval})...")
    signals = app.engine.scan(symbols, interval=args.interval, use_ai=args.ai)
    rows = [s for s in signals if s.side != "NEUTRAL"][: args.top]
    if not rows:
        _print("Tidak ada sinyal yang cukup kuat saat ini.")
        return 0
    if HAS_RICH:
        table = Table(title=f"Top {len(rows)} sinyal")
        for col in ("Simbol", "Sisi", "Harga", "Conf", "Skor", "SL", "TP"):
            table.add_column(col)
        for s in rows:
            table.add_row(
                s.symbol,
                s.side,
                fmt_price(s.price),
                str(s.confidence),
                str(s.technical_score),
                fmt_price(s.stop_loss or 0),
                fmt_price(s.take_profit or 0),
            )
        console.print(table)
    else:
        for s in rows:
            _print(
                f"{s.symbol:<14} {s.side:<5} {fmt_price(s.price):>14} "
                f"conf={s.confidence} skor={s.technical_score} "
                f"SL={fmt_price(s.stop_loss or 0)} TP={fmt_price(s.take_profit or 0)}"
            )
    return 0


def cmd_analyze(app: App, args) -> int:
    print_banner(app)
    signal = app.engine.analyze(args.symbol, interval=args.interval, use_ai=not args.no_ai)
    snap = signal.snapshot
    _print(f"[bold]{signal.symbol}[/bold] @ {fmt_price(signal.price)}  ({fmt_pct(snap['change_24h_pct'])} 24 jam)")
    rsi_txt = f"{snap['rsi14']:.1f}" if snap.get("rsi14") is not None else "—"
    _print(f"  Tren {snap['trend']} | RSI {rsi_txt} | ATR {fmt_price(snap['atr14'] or 0)} "
           f"| Funding {snap['funding_rate'] * 100:.4f}%")
    _print(f"  Skor teknikal: {signal.technical_score} (-100..100)")
    if signal.ai:
        _print(f"  AI ({signal.ai.model_used}): {signal.ai.signal}, confidence {signal.ai.confidence}")
        _print(f"  Alasan AI: {signal.ai.reasoning}")
        for risk in signal.ai.risks:
            _print(f"    ⚠ {risk}")
    app.journal.record_ai_decision(signal, provider=app.engine.ai_name)
    _print(f"  → SINYAL: [bold]{signal.side}[/bold] (confidence {signal.confidence})")
    if signal.stop_loss:
        _print(f"    SL {fmt_price(signal.stop_loss)} | TP {fmt_price(signal.take_profit or 0)}")
    for reason in signal.reasons:
        _print(f"    • {reason}")
    return 0


def cmd_positions(app: App, _args) -> int:
    positions = app.binance.positions()
    if not positions:
        _print("Tidak ada posisi terbuka.")
        return 0
    for p in positions:
        amt = float(p["positionAmt"])
        _print(
            f"{p['symbol']:<14} {'LONG' if amt > 0 else 'SHORT':<5} qty={fmt_qty(abs(amt))} "
            f"entry={fmt_price(float(p['entryPrice']))} mark={fmt_price(float(p['markPrice']))} "
            f"uPnL={fmt_usd(float(p['unRealizedProfit']))} lev={p['leverage']}x"
        )
    return 0


def cmd_trade(app: App, args) -> int:
    import dataclasses

    # Eksekusi WAJIB analisis segar (melewati cache).
    signal = app.engine.analyze(args.symbol, interval=args.interval, use_ai=not args.no_ai, fresh=True)
    if args.side:  # override sisi dari user → sinyal TURUNAN; sinyal asli tidak dimutasi
        from core.strategy import _atr_levels

        side = args.side.upper()
        sl, tp = _atr_levels(signal.price, signal.snapshot.get("atr14"), side)
        signal = dataclasses.replace(
            signal, side=side, stop_loss=sl, take_profit=tp,
            reasons=[*signal.reasons, f"Sisi di-override manual oleh user: {side}; SL/TP dari ATR."],
        )

    if signal.side == "NEUTRAL":
        _print("Sinyal NEUTRAL — tidak ada eksekusi. Jalankan `analyze` untuk detail.")
        return 0

    # Pra-cek informatif untuk teks konfirmasi; keputusan OTORITATIF dibuat
    # di dalam TradingService.submit secara atomik (evaluate+execute satu lock).
    pre = app.risk.evaluate(signal)
    for reason in pre.reasons:
        _print(f"  [risk] {reason}")
    if not pre.allowed:
        _print("Eksekusi DITOLAK oleh RiskManager.")
        return 1

    if app.settings.is_live:
        answer = input(
            f"\nMODE LIVE — kirim order {pre.side} {pre.quantity} {pre.symbol} "
            f"(notional {pre.notional:.2f} USDT)? Ketik simbol untuk konfirmasi: "
        )
        if answer.strip().upper() != pre.symbol:
            _print("Dibatalkan.")
            return 1

    decision, result = app.service.submit(signal)
    if result is None:
        for reason in decision.reasons:
            _print(f"  [risk] {reason}")
        _print("Eksekusi DITOLAK pada validasi akhir (harga/risiko).")
        return 1
    for note in result.notes:
        _print(f"  {note}")
    app.journal.record_execution(decision, result, signal.price)
    if app.notifier.enabled:
        sent = app.notifier.notify_execution(decision, result, signal.price)
        _print(f"  [telegram] Notifikasi {'terkirim' if sent else 'gagal terkirim (cek log)'}")
    return 0


def cmd_close(app: App, args) -> int:
    result = app.executor.close_position(args.symbol)
    _print(str(result))
    return 0


def cmd_ai_account(app: App, _args) -> int:
    _print("[bold]Provider AI[/bold]")
    for row in describe_providers(app.settings):
        mark = "→ AKTIF" if row["active"] else ""
        state = "terkonfigurasi" if row["configured"] else "belum diisi"
        _print(f"  {row['provider']:<10} model={row['model']:<18} {state} {mark}")
    provider = app.ai
    if provider is not None and provider.name == "bangunweb" and provider.is_configured():
        info = provider.account()
        _print("[bold]AI Router BangunWeb — akun[/bold]")
        _print(f"  Email          : {info.get('email')}")
        _print(f"  Nama kunci     : {info.get('key_name')}")
        _print(f"  Token tersisa  : {info.get('tokens_remaining')}")
        _print(f"  Terpakai (kunci): {info.get('key_tokens_used')}")
        last7 = info.get("last_7_days") or {}
        _print(f"  7 hari terakhir: {last7.get('requests')} request, {last7.get('billed_tokens')} token")
    return 0


def cmd_backtest(app: App, args) -> int:
    config = BacktestConfig(
        initial_balance=args.balance,
        risk_pct=args.risk,
        min_score=args.min_score,
    )
    result = Backtester(app.binance).run(args.symbol, interval=args.interval, limit=args.limit, config=config)
    d = result.to_dict()
    _print(f"[bold]Backtest {d['symbol']} ({d['interval']}, {d['candles']} candle)[/bold]")
    _print(f"  Saldo awal : {d['initial_balance']:.2f} USDT")
    _print(f"  Saldo akhir: {d['final_balance']:.2f} USDT  ({d['return_pct']:+.2f}%)")
    _print(f"  Trade      : {d['trades_count']}  (menang {d['wins']} / kalah {d['losses']}, win rate {d['win_rate']}%)")
    pf = d["profit_factor"]
    _print(f"  Profit factor: {pf if pf is not None else '∞ (belum ada kerugian)'}")
    _print(f"  Max drawdown : {d['max_drawdown_pct']:.2f}%")
    if result.trades:
        if HAS_RICH:
            table = Table(title="10 trade terakhir", header_style="bold")
            for col in ("Sisi", "Entri", "Keluar", "Alasan", "PnL (USDT)"):
                table.add_column(col)
            for t in result.trades[-10:]:
                table.add_row(
                    t.side,
                    fmt_price(t.entry_price),
                    fmt_price(t.exit_price),
                    t.exit_reason,
                    f"{t.pnl:+.2f}",
                )
            _print(table)
        else:  # tanpa rich: teks polos
            for t in result.trades[-10:]:
                _print(f"  {t.side} {fmt_price(t.entry_price)} → {fmt_price(t.exit_price)} "
                       f"({t.exit_reason}) PnL {t.pnl:+.2f}")
    _print("[dim]Catatan: backtest memakai skor teknikal murni (tanpa AI) agar deterministik.[/dim]")
    return 0


def cmd_watch(app: App, args) -> int:
    symbols = app.symbols.top_by_volume(args.top)
    _print(
        f"[bold]Mode pantau[/bold]: {len(symbols)} simbol, putaran tiap {args.every} menit, "
        f"notifikasi Telegram untuk sinyal ≥{args.min_confidence}%."
    )
    _print("[dim]Tekan Ctrl+C untuk berhenti. Mode ini TIDAK mengeksekusi order — hanya memantau & memberi tahu.[/dim]")
    rounds = 0
    try:
        while True:
            rounds += 1
            signals = app.engine.scan(symbols, use_ai=True)
            _print(f"\n[bold]Putaran {rounds}[/bold] — {time.strftime('%H:%M:%S')}")
            for s in signals[: args.top]:
                _print(
                    f"  {s.symbol:<14} {s.side:<8} keyakinan {s.confidence:>3}%  "
                    f"skor {s.technical_score:+d}  harga {fmt_price(s.price)}"
                )
            strong = [s for s in signals if s.side != "NEUTRAL" and s.confidence >= args.min_confidence]
            for s in strong:
                if app.notifier.enabled:
                    sent = app.notifier.notify_signal(s)
                    _print(f"  [telegram] Sinyal {s.symbol} {'terkirim' if sent else 'gagal terkirim'}")
            if args.iterations and rounds >= args.iterations:
                break
            time.sleep(args.every * 60)
    except KeyboardInterrupt:
        _print("\nMode pantau dihentikan.")
    return 0


def cmd_journal(app: App, args) -> int:
    rows = app.journal.recent(args.limit)
    if not rows:
        _print("Jurnal masih kosong — belum ada eksekusi yang tercatat.")
        return 0
    if not HAS_RICH:  # tanpa rich: teks polos (Table tidak tersedia)
        for r in rows:
            when = time.strftime("%d-%m %H:%M", time.localtime(r.get("time", 0) / 1000))
            _print(
                f"  {when} {r.get('symbol')} {r.get('side')} qty {r.get('quantity')} "
                f"@ {fmt_price(float(r.get('reference_price', 0) or 0))} "
                f"{'DRY-RUN' if r.get('dry_run') else 'LIVE'} notional {float(r.get('notional', 0) or 0):.2f}"
            )
        return 0
    table = Table(title=f"Jurnal perdagangan ({len(rows)} entri terakhir)", header_style="bold")
    for col in ("Waktu", "Simbol", "Sisi", "Qty", "Harga ref", "Mode", "Notional"):
        table.add_column(col)
    for r in rows:
        when = time.strftime("%d-%m %H:%M", time.localtime(r.get("time", 0) / 1000))
        table.add_row(
            when,
            str(r.get("symbol")),
            str(r.get("side")),
            str(r.get("quantity")),
            fmt_price(float(r.get("reference_price", 0) or 0)),
            "DRY-RUN" if r.get("dry_run") else "LIVE",
            f"{float(r.get('notional', 0) or 0):.2f}",
        )
    _print(table)
    return 0


def cmd_performance(app: App, args) -> int:
    perf = app.journal.performance()
    if not perf["closed_trades"]:
        _print("Belum ada trade tertutup di jurnal — metrik performa belum tersedia.")
        return 0
    _print(f"[bold]Performa ({perf['closed_trades']} trade tertutup)[/bold]")
    _print(f"  Win rate     : {perf['win_rate_pct']}%  (menang {perf['wins']} / kalah {perf['losses']})")
    pf = perf["profit_factor"]
    _print(f"  Profit factor: {pf if pf is not None else '∞ (belum ada kerugian)'}")
    _print(f"  Expectancy   : {perf['expectancy']:+.4f} USDT/trade")
    _print(f"  R rata-rata  : {perf['avg_r'] if perf['avg_r'] is not None else '—'}")
    _print(f"  Total PnL    : {perf['total_pnl']:+.4f} USDT")
    _print(f"  Max drawdown : {perf['max_drawdown']:.4f} USDT (dari kurva PnL kumulatif)")
    return 0


def cmd_reconcile(app: App, args) -> int:
    from core.position_manager import PositionManager
    from core.reconciler import Reconciler

    pm = PositionManager(app.settings, app.binance, app.executor, app.journal, app.symbols)
    reconciler = Reconciler(app.settings, app.binance, app.executor, app.journal, position_manager=pm)
    summary = reconciler.run_once()
    _print(f"[bold]Rekonsiliasi selesai[/bold]: {summary}")
    return 0


def cmd_research(app: App, args) -> int:
    from core.backtest import BacktestConfig
    from core.history import load_history
    from core.research import run_research

    interval = args.interval or app.settings.default_interval
    klines = load_history(app.binance, args.symbol.upper(), interval, days=args.days)
    if len(klines) < 500:
        _print(f"[red]Data terlalu pendek untuk riset: {len(klines)} candle.[/red]")
        return 1
    config = BacktestConfig(min_score=args.min_score, scoring_version=args.scoring)
    md, path = run_research(args.symbol.upper(), interval, klines, config, folds=args.folds)
    _print(f"[bold]Riset selesai[/bold] — laporan: [cyan]{path}[/cyan]")
    for line in md.splitlines():
        if line.startswith("- Trade:") or line.startswith("Total out-of-sample") or line.startswith("- PnL rata-rata"):
            _print("  " + line)
    _print("[yellow]Ingat: hasil historis bukan jaminan profit. Baca peringatan di laporan.[/yellow]")
    return 0


def cmd_ai_model(app: App, args) -> int:
    provider, model = load_ai_choice(app.settings)
    if args.provider or args.model:
        new_provider = normalize_ai_provider(args.provider) if args.provider else provider
        if new_provider not in AI_PROVIDERS:
            _print(f"[red]Provider tidak dikenal:[/red] {args.provider} — pilihan: {', '.join(AI_PROVIDERS)}")
            return 1
        new_model = (args.model or "").strip() or (
            model if new_provider == provider else ai_default_model(app.settings, new_provider)
        )
        try:
            save_ai_choice(new_provider, new_model)
        except ValueError as exc:
            _print(f"[red]{exc}[/red]")
            return 1
        _print(f"[green]Pilihan AI disimpan:[/green] {new_provider}:{new_model}")
        _print("[dim]Berlaku untuk perintah berikutnya; server web yang sedang berjalan memuatnya otomatis.[/dim]")
        return 0
    sumber = "data/ai_settings.json" if ai_choice_source() == "file" else ".env (bawaan)"
    configured = bool(app.ai is not None and app.ai.is_configured())
    _print(f"[bold]Provider aktif:[/bold] {provider} · [bold]Model:[/bold] {model}")
    _print(f"Sumber pilihan: {sumber} · Kunci API: {'terkonfigurasi' if configured else 'belum ada (analisis teknikal murni)'}")
    if args.list:
        for p in AI_PROVIDERS:
            models = AI_KNOWN_MODELS.get(p) or []
            _print(f"  {p:<10} {', '.join(models) if models else '(model bebas — ketik manual)'}")
    else:
        _print("[dim]Ganti: ai-model --provider deepseek --model deepseek-chat · Saran model: ai-model --list[/dim]")
    return 0


def cmd_broadcast(app: App, args) -> int:
    symbols = app.symbols.all_symbols()
    cache: dict[str, float] = {}
    putaran = 0
    _print(
        f"[bold]Siaran sinyal ke Telegram[/bold]: pindai {len(symbols)} simbol, "
        f"ambang skor ≥{args.min_score}, maks {args.max} sinyal/putaran"
        + (f", putaran tiap {args.loop} menit, jeda siar ulang {args.cooldown} jam." if args.loop else ".")
    )
    try:
        while True:
            putaran += 1
            summary = run_broadcast(
                app.settings, app.engine, app.binance, app.notifier, symbols,
                min_score=args.min_score, max_signals=args.max,
                sent_cache=cache if args.loop else None,
                cooldown_sec=int(args.cooldown * 3600),
            )
            _print(
                f"\n[bold]Putaran {putaran}[/bold] — kandidat: {summary['candidates']}, "
                f"terkirim: {', '.join(summary['sent']) if summary['sent'] else '—'}"
            )
            for d in summary["details"]:
                _print(f"  {d.get('symbol', '?'):<14} {d.get('status', '')}")
            if not summary["telegram_enabled"]:
                _print("[yellow]Telegram belum dikonfigurasi (.env: TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID) — tidak ada yang terkirim.[/yellow]")
                return 1
            if not args.loop:
                break
            time.sleep(args.loop * 60)
    except KeyboardInterrupt:
        _print("\nSiaran dihentikan.")
    return 0


def cmd_notify_test(app: App, _args) -> int:
    if not app.notifier.enabled:
        _print("Telegram belum dikonfigurasi: isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID di .env")
        return 1
    ok = app.notifier.notify_test()
    _print("Pesan uji Telegram terkirim." if ok else "Pesan uji GAGAL terkirim — periksa token/chat id dan log.")
    return 0 if ok else 1


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="binance-ai-future", description="Trading futures Binance berbantuan AI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("balance", help="Saldo & ringkasan akun futures")
    sub.add_parser("symbols", help="Daftar semua simbol perpetual aktif")

    p = sub.add_parser("dashboard", help="Dashboard terminal interaktif (Textual): overview, grafik, sinyal, metrik")
    p.add_argument("--symbol", default="BTCUSDT", help="Simbol awal (bawaan BTCUSDT)")
    p.add_argument("--interval", default=None, help="Interval awal (bawaan dari .env)")

    p = sub.add_parser("chart", help="Grafik candlestick terminal (plotext) satu simbol")
    p.add_argument("symbol")
    p.add_argument("--interval", default=None)
    p.add_argument("--limit", type=int, default=160, help="Jumlah candle (bawaan 160)")

    p_scan = sub.add_parser("scan", help="Pindai semua simbol & peringkatkan sinyal")
    p_scan.add_argument("--top", type=int, default=10)
    p_scan.add_argument("--interval", default=None)
    p_scan.add_argument("--ai", action="store_true", help="Nilai 5 kandidat teratas dengan AI")

    p_an = sub.add_parser("analyze", help="Analisis teknikal + AI satu simbol")
    p_an.add_argument("symbol")
    p_an.add_argument("--interval", default=None)
    p_an.add_argument("--no-ai", action="store_true")

    sub.add_parser("positions", help="Posisi terbuka saat ini")

    p_tr = sub.add_parser("trade", help="Eksekusi sinyal (dry-run kecuali mode live)")
    p_tr.add_argument("symbol")
    p_tr.add_argument("--side", choices=["LONG", "SHORT"], default=None)
    p_tr.add_argument("--interval", default=None)
    p_tr.add_argument("--no-ai", action="store_true")

    p_cl = sub.add_parser("close", help="Tutup posisi satu simbol")
    p_cl.add_argument("symbol")

    sub.add_parser("ai-account", help="Status provider AI (+ sisa token BangunWeb bila aktif)")

    p = sub.add_parser("ai-model", help="Lihat/ganti provider & model AI (tersimpan, tanpa edit .env)")
    p.add_argument("--provider", default=None, help="bangunweb/openai/anthropic/deepseek/grok/nous/custom")
    p.add_argument("--model", default=None, help="Nama model (bebas, mis. deepseek-v4-flash)")
    p.add_argument("--list", action="store_true", help="Tampilkan saran model per provider")
    sub.add_parser("notify-test", help="Kirim pesan uji ke Telegram")

    p = sub.add_parser("backtest", help="Uji strategi pada data historis (skor teknikal murni)")
    p.add_argument("symbol")
    p.add_argument("--interval", default=None)
    p.add_argument("--limit", type=int, default=1000, help="Jumlah candle (maks 1500)")
    p.add_argument("--balance", type=float, default=1000.0)
    p.add_argument("--risk", type=float, default=1.0, help="%% risiko per trade")
    p.add_argument("--min-score", type=int, default=40)

    p = sub.add_parser("watch", help="Pantau pasar berkala & kirim sinyal kuat ke Telegram (tanpa eksekusi)")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--every", type=int, default=15, help="Jeda antar putaran (menit)")
    p.add_argument("--min-confidence", type=int, default=60)
    p.add_argument("--iterations", type=int, default=0, help="0 = tanpa batas")

    p = sub.add_parser("journal", help="Lihat jurnal eksekusi terakhir")
    p.add_argument("--limit", type=int, default=20)

    sub.add_parser("performance", help="Metrik performa dari jurnal (trade tertutup)")

    sub.add_parser("reconcile", help="Jalankan satu putaran rekonsiliasi posisi ↔ proteksi ↔ jurnal")

    p = sub.add_parser("research", help="Riset walk-forward + baseline acak → laporan Markdown di reports/")
    p.add_argument("symbol")
    p.add_argument("--interval", default=None)
    p.add_argument("--days", type=int, default=90, help="Rentang data historis (hari)")
    p.add_argument("--folds", type=int, default=4)
    p.add_argument("--min-score", type=int, default=40, dest="min_score")
    p.add_argument("--scoring", type=int, default=1, choices=(1, 2), help="Versi skor teknikal")

    p = sub.add_parser("broadcast", help="Pindai semua simbol → AI → kirim sinyal terbaik + grafik ke Telegram")
    p.add_argument("--min-score", type=int, default=50, help="Ambang |skor teknikal| kandidat (bawaan 50)")
    p.add_argument("--max", type=int, default=3, help="Maks sinyal terkirim per putaran (bawaan 3)")
    p.add_argument("--loop", type=int, default=0, help="Ulangi tiap N menit (0 = sekali jalan)")
    p.add_argument("--cooldown", type=float, default=4, help="Jeda siar ulang simbol+sisi yang sama (jam, mode loop)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings()
    app = App(settings)

    handlers = {
        "balance": cmd_balance,
        "symbols": cmd_symbols,
        "dashboard": cmd_dashboard,
        "chart": cmd_chart,
        "scan": cmd_scan,
        "analyze": cmd_analyze,
        "positions": cmd_positions,
        "trade": cmd_trade,
        "close": cmd_close,
        "ai-account": cmd_ai_account,
        "ai-model": cmd_ai_model,
        "notify-test": cmd_notify_test,
        "backtest": cmd_backtest,
        "watch": cmd_watch,
        "journal": cmd_journal,
        "performance": cmd_performance,
        "reconcile": cmd_reconcile,
        "research": cmd_research,
        "broadcast": cmd_broadcast,
    }
    try:
        if settings.has_binance_keys and args.command not in {"ai-account"}:
            try:
                app.binance.sync_time()
            except BinanceAPIError:
                pass  # sinkronisasi waktu bersifat best-effort
        return handlers[args.command](app, args)
    except BinanceAPIError as exc:
        _print(f"[red]Error Binance:[/red] {exc}")
        return 2
    except Exception as exc:  # permukaan error yang rapi untuk user terminal
        _print(f"[red]Error:[/red] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
