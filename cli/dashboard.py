"""Dashboard terminal interaktif (Textual) — tampilan ala mockup CLI.

Jalankan:  python run.py cli dashboard [--symbol BTCUSDT] [--interval 1h]

Panel: Market Overview (analisis + AI), Market Chart (candlestick
plotext: EMA20/EMA50 + volume), Analysis Signal (tabel backtest /
hasil pindai), dan Key Metrics (saldo, uPnL, token AI, update terakhir).

Semua data memakai komponen core yang sama dengan Web UI & CLI biasa
(SignalEngine, Backtester, BinanceFuturesClient) — tidak ada jalur
eksekusi order di dashboard ini; sifatnya memantau saja.

Textual adalah dependensi opsional: bila belum terpasang, perintah
`dashboard` memberi petunjuk instalasi dan keluar dengan rapi.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

from utils.formatting import fmt_pct, fmt_price, fmt_usd

CLI_VERSION = "1.0.0"

LOGO_LINES = [
    "  ◆  ",
    "◆ ◆ ◆",
    "  ◆  ",
]

INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"]


def textual_available() -> bool:
    try:
        import textual  # noqa: F401

        return True
    except Exception:
        return False


def _tz_label() -> str:
    offset = datetime.now().astimezone().utcoffset()
    hours = (offset.total_seconds() / 3600) if offset else 0
    return f"UTC{hours:+g}"


def _clock() -> str:
    return time.strftime("%H:%M:%S")


# ----------------------------------------------------------------------
# Aplikasi Textual (diimpor malas agar modul ini aman tanpa textual)
# ----------------------------------------------------------------------
def build_app(cli_app, symbol: str, interval: str):
    from rich.text import Text
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Grid, Horizontal, Vertical
    from textual.screen import ModalScreen
    from textual.widgets import Button, DataTable, Input, Static

    from cli.term_chart import render_chart

    class _InfoScreen(ModalScreen):
        """Layar modal sederhana berisi teks (Bantuan / Pengaturan)."""

        DEFAULT_CSS = """
        _InfoScreen { align: center middle; }
        #info-box {
            width: 76; max-width: 92%; height: auto; max-height: 88%;
            background: #10141a; border: round #f0b90b; padding: 1 2;
        }
        #info-close { margin-top: 1; width: 100%; }
        """

        def __init__(self, title: str, body: str):
            super().__init__()
            self._title = title
            self._body = body

        def compose(self) -> ComposeResult:
            with Vertical(id="info-box"):
                yield Static(f"[bold #f0b90b]{self._title}[/bold #f0b90b]")
                yield Static(self._body)
                yield Button("Tutup (Esc)", id="info-close")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss()

        def key_escape(self) -> None:
            self.dismiss()

    class DashboardApp(App):
        TITLE = "binance-ai-future"
        CSS = """
        Screen { background: #07090c; color: #eaecef; }
        #titlebar { height: 1; background: #10141a; }
        #tb-dots { width: 12; padding-left: 1; }
        #tb-title { width: 1fr; text-align: center; color: #8a94a6; }
        #tb-icons { width: 10; text-align: right; padding-right: 1; color: #8a94a6; }
        #brand { height: 5; padding: 0 1; }
        #logo { width: 7; color: #f0b90b; text-style: bold; content-align: center middle; }
        #brand-mid { width: 1fr; padding-left: 1; }
        #brand-name { text-style: bold; }
        #brand-tag { color: #8a94a6; }
        #brand-status { width: 34; text-align: right; color: #8a94a6; }
        #nav { height: 3; padding: 0 1; }
        #nav Button { margin-right: 1; }
        #nav Button.active { background: #f0b90b; color: #141414; text-style: bold; }
        #clock { width: 1fr; text-align: right; content-align: right middle; }
        #main { grid-size: 2 2; grid-gutter: 1; padding: 0 1; }
        .panel { border: round #6b5510; border-title-color: #f0b90b; background: #10141a; padding: 0 1; }
        #chartbox { border: round #6b5510; border-title-color: #f0b90b; background: #10141a; padding: 0 1; }
        #chart-head { height: 1; }
        #chart-title { width: 1fr; color: #f0b90b; text-style: bold; }
        .tf-btn { min-width: 5; height: 1; border: none; background: #171d26; color: #8a94a6; margin-left: 1; }
        .tf-btn.active { background: #f0b90b; color: #141414; text-style: bold; }
        #chart { height: 1fr; }
        #sigtable { height: 1fr; background: #10141a; }
        #metrics { grid-size: 2 2; grid-gutter: 1; }
        .metric { border: round #2a2f38; background: #171d26; padding: 0 1; }
        #footer { height: 3; padding: 0 1; }
        #prompt-label { width: 22; content-align: left middle; }
        #symbol-input { width: 1fr; }
        #hints { width: 46; text-align: right; content-align: right middle; color: #8a94a6; }
        """

        BINDINGS = [
            Binding("f1", "help", "Help"),
            Binding("f2", "focus_symbol", "Analysis"),
            Binding("f3", "settings", "Settings"),
            Binding("f5", "refresh", "Refresh"),
            Binding("ctrl+c", "quit", "Exit"),
            Binding("ctrl+q", "quit", "Exit"),
        ]

        def __init__(self):
            super().__init__()
            self.cli = cli_app
            self.symbol = symbol.upper()
            self.interval = interval
            self._klines: list[list] = []
            self._signal = None
            self._table_mode = "backtest"
            self._busy = False

        # ---------------- tata letak ----------------
        def compose(self) -> ComposeResult:
            with Horizontal(id="titlebar"):
                yield Static("[#f6465d]●[/] [#f0b90b]●[/] [#0ecb81]●[/]", id="tb-dots")
                yield Static(f"binance-ai-future — cli v{CLI_VERSION}", id="tb-title")
                yield Static("⚙  ⛶", id="tb-icons")
            with Horizontal(id="brand"):
                yield Static("\n".join(LOGO_LINES), id="logo")
                with Vertical(id="brand-mid"):
                    yield Static(Text.from_markup(
                        "binance-[bold #f0b90b]ai[/bold #f0b90b]-future"), id="brand-name")
                    yield Static("Leveraging AI for Smarter Crypto Trading Decisions.", id="brand-tag")
                yield Static("", id="brand-status")
            with Horizontal(id="nav"):
                yield Button("Dashboard", id="nav-dashboard", classes="active")
                yield Button("Analysis", id="nav-analysis")
                yield Button("Trade Signals", id="nav-signals")
                yield Button("Market Overview", id="nav-market")
                yield Button("Settings", id="nav-settings")
                yield Static("", id="clock")
            with Grid(id="main"):
                yield Static("Memuat data…", id="overview", classes="panel")
                with Vertical(id="chartbox"):
                    with Horizontal(id="chart-head"):
                        yield Static("", id="chart-title")
                        for iv in INTERVALS:
                            yield Button(iv, id=f"tf-{iv}", classes="tf-btn" + (" active" if iv == self.interval else ""))
                    yield Static("", id="chart")
                with Vertical(id="signalbox", classes="panel"):
                    yield Static("[bold #f0b90b]Analysis Signal[/bold #f0b90b]", id="sig-title")
                    yield DataTable(id="sigtable", cursor_type="row")
                with Grid(id="metrics"):
                    yield Static("", id="m-wallet", classes="metric")
                    yield Static("", id="m-upnl", classes="metric")
                    yield Static("", id="m-ai", classes="metric")
                    yield Static("", id="m-updated", classes="metric")
            with Horizontal(id="footer"):
                yield Static("[#0ecb81]●[/] [bold #f0b90b]binance-ai-future[/] >", id="prompt-label")
                yield Input(placeholder="Ketik simbol lalu Enter — cth: BTCUSDT", id="symbol-input")
                yield Static("F1 Help · F2 Analysis · F3 Settings · Ctrl+C Exit", id="hints")

        # ---------------- siklus hidup ----------------
        def on_mount(self) -> None:
            ov = self.query_one("#overview", Static)
            ov.border_title = "Market Overview"
            self.query_one("#chartbox", Vertical).border_title = "Market Chart"
            for wid, title in (("#m-wallet", "Saldo Dompet"), ("#m-upnl", "PNL Belum Realisasi"),
                               ("#m-ai", "Token AI Tersisa"), ("#m-updated", "Terakhir Update")):
                self.query_one(wid, Static).border_title = title
            self._update_brand_status()
            self.set_interval(1, self._tick_clock)
            self.set_interval(60, self._auto_light)
            self.run_worker(self._load_full(), group="load", exclusive=True)
            self.call_after_refresh(self._render_chart)

        def _update_brand_status(self) -> None:
            from core.ai_choice import load_choice

            s = self.cli.settings
            net = "TESTNET" if s.binance_testnet else "MAINNET"
            mode = "LIVE" if s.is_live else "DRY-RUN"
            provider, model = load_choice(s)
            ai_txt = f"{provider}:{model}" if self.cli.ai else "AI nonaktif"
            self.query_one("#brand-status", Static).update(
                f"{net} · {mode}\n{ai_txt}\nInterval {self.interval}"
            )

        def _tick_clock(self) -> None:
            self.query_one("#clock", Static).update(
                f"[#0ecb81]●[/] [bold]Live Market[/]  |  {_clock()}  {_tz_label()}"
            )

        # ---------------- pengambilan data (thread) ----------------
        async def _load_full(self) -> None:
            if self._busy:
                return
            self._busy = True
            try:
                data = await asyncio.to_thread(self._fetch_full, self.symbol, self.interval)
                self._apply_full(data)
            except Exception as exc:  # tampilkan error di panel, jangan jatuhkan aplikasi
                self.query_one("#overview", Static).update(f"[red]Gagal memuat data:[/] {exc}")
                self.notify(f"Gagal memuat data: {exc}", severity="error")
            finally:
                self._busy = False

        def _fetch_full(self, symbol: str, interval: str) -> dict:
            cli = self.cli
            signal = cli.engine.analyze(symbol, interval=interval, use_ai=True)
            klines = cli.binance.klines(symbol, interval, 160)
            trades = []
            try:
                from core.backtest import Backtester

                result = Backtester(cli.binance).run(symbol, interval=interval, limit=500)
                trades = result.trades[-12:]
            except Exception:
                trades = []
            account = None
            try:
                if cli.settings.has_binance_keys:
                    account = cli.binance.account()
            except Exception:
                account = None
            tokens = None
            try:
                if cli.ai is not None and cli.ai.name == "bangunweb" and cli.ai.is_configured():
                    tokens = cli.ai.account().get("tokens_remaining")
            except Exception:
                tokens = None
            return {"signal": signal, "klines": klines, "trades": trades,
                    "account": account, "tokens": tokens}

        async def _load_light(self) -> None:
            try:
                data = await asyncio.to_thread(self._fetch_light, self.symbol, self.interval)
                self._klines = data["klines"]
                self._render_chart()
                self._apply_metrics(data["account"], None, keep_updated=False)
            except Exception:
                # Penyegaran ringan bersifat best-effort; kegagalan diabaikan.
                pass  # nosec B110

        def _fetch_light(self, symbol: str, interval: str) -> dict:
            account = None
            try:
                if self.cli.settings.has_binance_keys:
                    account = self.cli.binance.account()
            except Exception:
                account = None
            return {"klines": self.cli.binance.klines(symbol, interval, 160), "account": account}

        def _auto_light(self) -> None:
            if not self._busy:
                self.run_worker(self._load_light(), group="light", exclusive=True)

        async def _load_scan(self) -> None:
            self._busy = True
            try:
                rows = await asyncio.to_thread(self._fetch_scan)
                self._apply_scan(rows)
            except Exception as exc:
                self.notify(f"Pindai gagal: {exc}", severity="error")
            finally:
                self._busy = False

        def _fetch_scan(self) -> list:
            symbols = self.cli.symbols.all_symbols()
            signals = self.cli.engine.scan(symbols, interval=self.interval, use_ai=False)
            return [s for s in signals if s.side != "NEUTRAL"][:12]

        # ---------------- penerapan data ke widget ----------------
        def _apply_full(self, data: dict) -> None:
            signal = data["signal"]
            self._signal = signal
            self._klines = data["klines"]
            snap = signal.snapshot
            side_color = {"LONG": "#0ecb81", "SHORT": "#f6465d"}.get(signal.side, "#8a94a6")
            lines = [
                f"[bold]{signal.symbol}[/] @ [bold #f0b90b]{fmt_price(signal.price)}[/]  ({fmt_pct(snap['change_24h_pct'])} 24 jam)",
                f"Tren {snap['trend']}  |  RSI {snap['rsi14']:.1f}  |  ATR {fmt_price(snap['atr14'] or 0)}  |  Funding {snap['funding_rate'] * 100:.4f}%",
                f"Skor Teknikal: {signal.technical_score} (-100..100)",
                f"→ SINYAL: [bold {side_color}]{signal.side}[/] (Confidence {signal.confidence})",
            ]
            if signal.ai:
                lines.append(f"AI ({signal.ai.model_used}): {signal.ai.signal} · {signal.ai.confidence}%")
                lines.append(f"[dim]{signal.ai.reasoning}[/]")
            lines.append("")
            for reason in signal.reasons:
                lines.append(f"[#f0b90b]•[/] {reason}")
            self.query_one("#overview", Static).update("\n".join(lines))
            self._apply_trades(data["trades"])
            self._apply_metrics(data["account"], data["tokens"], keep_updated=True)
            self._render_chart()
            self._update_brand_status()

        def _apply_trades(self, trades: list) -> None:
            self._table_mode = "backtest"
            self.query_one("#sig-title", Static).update(
                f"[bold #f0b90b]Analysis Signal[/] — backtest {self.symbol} {self.interval} (teknikal murni)"
            )
            table = self.query_one("#sigtable", DataTable)
            table.clear(columns=True)
            table.add_columns("Sisi", "Entri", "Keluar", "Alasan", "PnL (USDT)")
            if not trades:
                table.add_row("—", "—", "—", "belum ada trade pada backtest ini", "—")
                return
            for t in reversed(trades):
                color = "#0ecb81" if t.side == "LONG" else "#f6465d"
                pnl = Text(f"{t.pnl:+.2f}", style="#0ecb81" if t.pnl >= 0 else "#f6465d")
                table.add_row(Text(t.side, style=color), fmt_price(t.entry_price),
                              fmt_price(t.exit_price), t.exit_reason, pnl)

        def _apply_scan(self, rows: list) -> None:
            self._table_mode = "scan"
            self.query_one("#sig-title", Static).update(
                "[bold #f0b90b]Trade Signals[/] — hasil pindai semua simbol (teknikal)"
            )
            table = self.query_one("#sigtable", DataTable)
            table.clear(columns=True)
            table.add_columns("Simbol", "Sisi", "Harga", "Conf", "Skor")
            if not rows:
                table.add_row("—", "—", "—", "tidak ada sinyal kuat saat ini", "—")
                return
            for s in rows:
                color = "#0ecb81" if s.side == "LONG" else "#f6465d"
                table.add_row(s.symbol, Text(s.side, style=color), fmt_price(s.price),
                              str(s.confidence), str(s.technical_score))

        def _apply_metrics(self, account, tokens, keep_updated: bool) -> None:
            if account is not None:
                wallet = float(account.get("totalWalletBalance", 0))
                upnl = float(account.get("totalUnrealizedProfit", 0))
                self.query_one("#m-wallet", Static).update(f"[bold]{fmt_usd(wallet)}[/]")
                self.query_one("#m-upnl", Static).update(
                    f"[bold {'#0ecb81' if upnl >= 0 else '#f6465d'}]{fmt_usd(upnl)}[/]"
                )
            else:
                self.query_one("#m-wallet", Static).update("[dim]— (kunci API belum diisi)[/]")
                self.query_one("#m-upnl", Static).update("[dim]—[/]")
            if tokens is not None:
                ai_txt = f"[bold]{int(tokens):,}[/]".replace(",", ".")
            elif self.cli.ai is not None and self.cli.ai.is_configured():
                ai_txt = f"[bold]{self.cli.ai.name} aktif[/]"
            else:
                ai_txt = "[dim]—[/]"
            self.query_one("#m-ai", Static).update(ai_txt)
            if keep_updated:
                self.query_one("#m-updated", Static).update(f"[bold]{_clock()}[/]  ({_tz_label()})")

        def _render_chart(self) -> None:
            if not self._klines:
                return
            widget = self.query_one("#chart", Static)
            size = widget.content_size
            if size.width < 30 or size.height < 10:
                return
            try:
                ansi = render_chart(self._klines, width=size.width, height=size.height)
                widget.update(Text.from_ansi(ansi))
            except Exception as exc:
                widget.update(f"[dim]Grafik tidak tersedia: {exc}[/]")

        # ---------------- peristiwa & aksi ----------------
        def on_resize(self, _event) -> None:
            self._render_chart()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            value = event.value.strip().upper()
            if value:
                self.symbol = value
                self.run_worker(self._load_full(), group="load", exclusive=True)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            bid = event.button.id or ""
            if bid.startswith("tf-"):
                iv = bid[3:]
                if iv != self.interval:
                    self.interval = iv
                    for btn in self.query(".tf-btn"):
                        btn.set_class(btn.id == bid, "active")
                    self.run_worker(self._load_full(), group="load", exclusive=True)
            elif bid == "nav-analysis":
                self.query_one("#symbol-input", Input).focus()
            elif bid == "nav-signals":
                self._set_nav("nav-signals")
                self.run_worker(self._load_scan(), group="load", exclusive=True)
            elif bid == "nav-market":
                self._set_nav("nav-market")
                self.run_worker(self._load_full(), group="load", exclusive=True)
            elif bid == "nav-dashboard":
                self._set_nav("nav-dashboard")
                self.run_worker(self._load_full(), group="load", exclusive=True)
            elif bid == "nav-settings":
                self.action_settings()

        def _set_nav(self, active_id: str) -> None:
            for btn in self.query("#nav Button"):
                btn.set_class(btn.id == active_id, "active")

        def action_focus_symbol(self) -> None:
            self.query_one("#symbol-input", Input).focus()

        def action_refresh(self) -> None:
            self.run_worker(self._load_full(), group="load", exclusive=True)

        def action_help(self) -> None:
            body = (
                "[bold]Tombol[/]\n"
                "  F1  Bantuan ini\n"
                "  F2  Fokus ke kolom simbol (Analysis)\n"
                "  F3  Pengaturan (ringkasan konfigurasi)\n"
                "  F5  Muat ulang semua data (termasuk analisis AI)\n"
                "  Ctrl+C / Ctrl+Q  Keluar\n\n"
                "[bold]Alur[/]\n"
                "  Ketik simbol di kolom bawah lalu Enter untuk menganalisis.\n"
                "  Tombol timeframe mengganti interval grafik + analisis.\n"
                "  Trade Signals memindai semua simbol; Market Overview kembali\n"
                "  ke tabel backtest simbol aktif. Grafik & saldo menyegar\n"
                "  otomatis tiap 60 detik (tanpa memanggil AI).\n\n"
                "[dim]Dashboard ini hanya memantau — eksekusi order tetap lewat\n"
                "perintah `trade` di CLI biasa atau Web UI.[/]"
            )
            self.push_screen(_InfoScreen("Bantuan — binance-ai-future CLI", body))

        def action_settings(self) -> None:
            s = self.cli.settings
            from core.ai_choice import choice_source, load_choice

            provider, model = load_choice(s)
            body = (
                f"  Jaringan Binance : {'TESTNET' if s.binance_testnet else 'MAINNET'}\n"
                f"  Mode trading     : {'LIVE' if s.is_live else 'DRY-RUN (simulasi)'}\n"
                f"  Provider AI      : {provider} · {model} (sumber: {choice_source()})\n"
                f"  Siaran Telegram  : {'aktif' if s.broadcast_enabled else 'nonaktif'} "
                f"(tiap {s.broadcast_interval_minutes} mnt, skor ≥{s.broadcast_min_score})\n"
                f"  Risiko/trade     : {s.risk_per_trade_pct}% · leverage maks {s.max_leverage}x · "
                f"posisi maks {s.max_open_positions}\n"
                f"  Batas rugi harian: {s.daily_loss_limit_pct}%\n"
                f"  Jurnal           : {s.trade_journal_path}\n\n"
                "[dim]Ganti provider/model AI tanpa edit .env:\n"
                "  python run.py cli ai-model --provider deepseek --model deepseek-chat[/]"
            )
            self.push_screen(_InfoScreen("Pengaturan — ringkasan konfigurasi", body))

    return DashboardApp()


def run_dashboard(cli_app, symbol: str = "BTCUSDT", interval: str | None = None) -> int:
    """Jalankan dashboard Textual. Mengembalikan kode keluar proses."""
    if not textual_available():
        print("Dashboard interaktif membutuhkan paket 'textual' (dan 'plotext' untuk grafik).")
        print("Pasang dengan: pip install -r requirements.txt")
        return 1
    app = build_app(cli_app, symbol, interval or cli_app.settings.default_interval)
    app.run()
    return 0
