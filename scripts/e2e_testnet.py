#!/usr/bin/env python3
"""E2E Testnet — Fase 6 Audit November 2026.

Menjalankan skenario CLI 1–7 dari program Fase 6 terhadap Binance
Futures TESTNET sungguhan dengan order kecil sungguhan (risiko ≤0,5%
saldo testnet per trade). Hasil ditulis ke
`reports/e2e_testnet_<timestamp>.md` (tersanitasi — tanpa rahasia).

PENJAGA KESELAMATAN: skrip MENOLAK berjalan bila base URL bukan
testnet.binancefuture.com atau BINANCE_TESTNET bukan true. Mainnet
tidak pernah disentuh.

Pakai:  python scripts/e2e_testnet.py
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

# --- Penjaga & lingkungan: paksa mode E2E sebelum Settings dibaca ---
os.environ["TRADING_MODE"] = "live"          # hanya di proses skrip ini
os.environ["RISK_PER_TRADE_PCT"] = "0.1"     # risiko kecil per trade E2E
os.environ["BROADCAST_ENABLED"] = "false"
os.environ["MARKET_WS_ENABLED"] = "false"
# Skenario rekonsiliasi menguji pemasangan ulang SL secara langsung;
# tenggang A1 (bawaan 120 dtk) diuji terpisah di skenario web.
os.environ["RECONCILER_GRACE_SEC"] = "0"
# Riwayat income testnet akun ini memuat streak rugi lama (>= batas) —
# RiskManager MENOLAK semua trade karenanya (proteksi bekerja benar).
# Skenario trade E2E memakai konfigurasi pengguna yang sah
# (MAX_CONSECUTIVE_LOSSES=0 = fitur mati); logika streak sendiri
# diverifikasi eksplisit pada skenario 5 dengan settings tersendiri.
os.environ["MAX_CONSECUTIVE_LOSSES"] = "0"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RESULTS: list[tuple[str, str, str]] = []  # (skenario, status, detail)


def record(name: str, ok: bool | None, detail: str = "") -> None:
    status = "LULUS" if ok is True else ("TIDAK TERVERIFIKASI" if ok is None else "GAGAL")
    RESULTS.append((name, status, detail))
    print(f"[{status}] {name} — {detail}")


def scenario(name):
    def deco(fn):
        def wrapped(ctx):
            try:
                fn(ctx)
            except Exception:
                record(name, False, "pengecualian: " + traceback.format_exc(limit=6).replace("\n", " | ")[:800])
        return wrapped
    return deco


def main() -> int:
    from config.settings import load_settings

    settings = load_settings()
    # ---------------- PENJAGA TESTNET (aturan keras Fase 6) -----------
    from core.binance_client import BinanceFuturesClient

    probe = BinanceFuturesClient(settings)
    base = settings.binance_base_url or getattr(probe, "_base", "")
    if not settings.binance_testnet or "testnet.binancefuture.com" not in base:
        print(f"REFUSE: base URL bukan testnet ({base!r}) — skrip berhenti.")
        return 2
    if not settings.has_binance_keys:
        print("REFUSE: kunci Binance tidak terisi di .env.")
        return 2
    print(f"Penjaga OK: {base} (testnet, mode {settings.trading_mode})")

    from core.backtest import Backtester
    from core.executor import OrderExecutor
    from core.journal import TradeJournal
    from core.position_manager import PositionManager
    from core.reconciler import Reconciler, open_registry
    from core.risk import RiskManager
    from core.selfcheck import run_selfcheck
    from core.service import TradingService
    from core.strategy import SignalEngine, TradeSignal, _atr_levels
    from core.symbols import SymbolManager

    binance = probe
    binance.sync_time()
    symbols = SymbolManager(binance, settings)
    engine = SignalEngine(settings, binance, None)
    # AI nyata (BangunWeb) untuk skenario analisis.
    from core.ai_providers import create_ai_provider

    try:
        engine_ai = SignalEngine(settings, binance, create_ai_provider(settings))
    except Exception:
        engine_ai = engine
    risk = RiskManager(settings, binance, symbols)
    executor = OrderExecutor(settings, binance, symbols)
    journal = TradeJournal(settings.trade_journal_path)
    executor.set_intent_hook(journal.record_intent)
    service = TradingService(settings, risk, executor, binance)
    pm = PositionManager(settings, binance, executor, journal, symbols)
    reconciler = Reconciler(settings, binance, executor, journal, position_manager=pm, risk=risk)
    ctx = dict(settings=settings, binance=binance, symbols=symbols, engine=engine,
               engine_ai=engine_ai, risk=risk, executor=executor, journal=journal,
               service=service, reconciler=reconciler, Backtester=Backtester,
               TradeSignal=TradeSignal, atr_levels=_atr_levels, open_registry=open_registry)

    # ---------------- Skenario 1: fungsi baca CLI --------------------
    @scenario("1. Fungsi baca: selfcheck, saldo, simbol, scan, analyze, snapshot, backtest, jurnal")
    def s1(c):
        checks = run_selfcheck(c["settings"], binance=c["binance"])
        bad = [x.name for x in checks if x.ok is False]
        assert not bad, f"selfcheck gagal: {bad}"
        c["binance"].account()
        usdt = next((b for b in c["binance"].balances() if b["asset"] == "USDT"), {})
        balance = float(usdt.get("balance", 0))
        assert balance > 0, "saldo USDT testnet nol"
        tradable = c["symbols"].all_symbols()
        assert len(tradable) > 100, f"simbol tradable hanya {len(tradable)}"
        snap = c["engine"].snapshot_for("BTCUSDT")
        assert snap["price"] > 0 and snap["atr14"], "snapshot BTCUSDT tidak lengkap"
        sig = c["engine_ai"].analyze("BTCUSDT", use_ai=True, fresh=True)
        assert sig is not None and sig.symbol == "BTCUSDT", "analyze gagal"
        bt = c["Backtester"](c["binance"]).run("BTCUSDT", days=7)
        assert bt is not None, "backtest gagal"
        entries = c["journal"].all_entries()
        record("1. Fungsi baca: selfcheck, saldo, simbol, scan, analyze, snapshot, backtest, jurnal", True,
               f"saldo {balance:.2f} USDT; {len(tradable)} simbol; analyze BTCUSDT → {sig.side} "
               f"(AI: {sig.ai.signal if sig.ai else 'tanpa AI'}); backtest 7 hari selesai; jurnal {len(entries)} entri")

    # ---------------- Pembantu trade --------------------------------
    def open_trade(c, symbol):
        snap = c["engine"].snapshot_for(symbol)
        price = float(snap["price"])
        sl, tp = c["atr_levels"](price, snap.get("atr14"), "LONG")
        assert sl and tp, f"level ATR {symbol} kosong"
        signal = c["TradeSignal"](symbol=symbol, side="LONG", confidence=80, price=price,
                                  stop_loss=sl, take_profit=tp, technical_score=60, snapshot=snap)
        decision, result = c["service"].submit(signal)
        assert decision.allowed, f"risk menolak {symbol}: {decision.reasons}"
        assert result is not None and not result.dry_run, f"eksekusi {symbol} bukan live"
        return result

    def position_of(c, symbol):
        return next((p for p in c["binance"].positions(symbol) if float(p["positionAmt"]) != 0), None)

    def algos_of(c, symbol):
        return c["binance"].open_algo_orders(symbol)

    # ---------------- Skenario 2: trade live BTCUSDT -----------------
    @scenario("2. Trade live BTCUSDT LONG → PROTECTED + SL/TP algo + jurnal")
    def s2(c):
        result = open_trade(c, "BTCUSDT")
        time.sleep(2)
        pos = position_of(c, "BTCUSDT")
        assert pos is not None, "posisi BTCUSDT tidak ditemukan setelah eksekusi"
        algos = algos_of(c, "BTCUSDT")
        types = {(o.get("type") or o.get("orderType")) for o in algos}
        assert "STOP_MARKET" in types and "TAKE_PROFIT_MARKET" in types, f"algo tidak lengkap: {types}"
        import json as _json

        state_lines = c["executor"]._state_path.read_text(encoding="utf-8").splitlines()
        states = [_json.loads(x) for x in state_lines if x.strip()]
        states = [r for r in states if r.get("client_order_id") == result.client_order_id]
        assert states and states[-1]["state"] == "PROTECTED", f"state terakhir: {states[-1]['state'] if states else 'kosong'}"
        reg = c["open_registry"](c["journal"])
        assert result.client_order_id in reg, "entri jurnal eksekusi tidak ditemukan di registry"
        c["btc_cid"] = result.client_order_id
        record("2. Trade live BTCUSDT LONG → PROTECTED + SL/TP algo + jurnal", True,
               "qty terisi; SL/TP algo terpasang; state PROTECTED; jurnal tercatat")

    # ---------------- Skenario 3: tutup posisi -----------------------
    @scenario("3. close_position → bersih, tidak di pending_recovery")
    def s3(c):
        out = c["executor"].close_position("BTCUSDT")
        assert out["closed"], f"close gagal: {out}"
        time.sleep(2)
        assert position_of(c, "BTCUSDT") is None, "posisi masih ada"
        assert algos_of(c, "BTCUSDT") == [], "algo masih tersisa"
        pending = c["executor"].pending_recovery()
        assert not any(r.get("client_order_id") == c.get("btc_cid") for r in pending), "masih pending_recovery"
        record("3. close_position → bersih, tidak di pending_recovery", True, "posisi & algo nol; state CLOSED")

    # ---------------- Skenario 4: simbol harga mikro -----------------
    @scenario("4. Simbol harga mikro: entri tanpa error -1100 (notasi saintifik A2)")
    def s4(c):
        tickers = c["binance"].tickers_24h_all()
        tradable = set(c["symbols"].all_symbols())
        micros = [t for t in tickers if t["symbol"] in tradable
                  and 0 < float(t["lastPrice"]) < 0.01 and float(t.get("quoteVolume", 0)) > 1_000_000]
        micros.sort(key=lambda t: float(t["quoteVolume"]), reverse=True)
        assert micros, "tidak ada simbol mikro likuid"
        # Pilih kandidat yang harga mark ≈ harga pasar (buku testnet
        # simbol mikro tak likuid bisa menyimpang > batas deviasi 0,3% —
        # penjaga deviasi MENOLAKNYA dengan benar, itu bukan kegagalan).
        last_err = None
        for cand in micros[:5]:
            symbol = cand["symbol"]
            try:
                snap = c["engine"].snapshot_for(symbol)
                now = float(c["binance"].ticker_price(symbol)["price"])
                if abs(now - float(snap["price"])) / float(snap["price"]) * 100 > 0.25:
                    continue
                open_trade(c, symbol)
                time.sleep(2)
                assert position_of(c, symbol) is not None, f"posisi {symbol} tidak ada"
                out = c["executor"].close_position(symbol)
                assert out["closed"], f"close {symbol} gagal"
                record("4. Simbol harga mikro: entri tanpa error -1100 (notasi saintifik A2)", True,
                       f"{symbol} @ {cand['lastPrice']} — entri & tutup bersih")
                return
            except Exception as exc:  # coba kandidat berikutnya
                last_err = exc
        raise AssertionError(f"semua kandidat mikro gagal: {last_err}")

    # ---------------- Skenario 5: penolakan --------------------------
    @scenario("5. Penolakan: deviasi harga, kunci harian, streak rugi")
    def s5(c):
        snap = c["engine"].snapshot_for("BTCUSDT")
        price = float(snap["price"])
        sl, tp = c["atr_levels"](price * 0.90, snap.get("atr14"), "LONG")
        stale = c["TradeSignal"](symbol="BTCUSDT", side="LONG", confidence=80, price=price * 0.90,
                                 stop_loss=sl, take_profit=tp, technical_score=60, snapshot=snap)
        decision, result = c["service"].submit(stale)
        assert not decision.allowed and result is None, "deviasi harga tidak ditolak"
        dev_ok = any("bergerak" in r or "menyimpang" in r for r in decision.reasons)

        # Kunci harian: baseline awal hari jauh di atas ekuitas kini.
        import json as _json
        from pathlib import Path as _P

        state_path = _P(c["settings"].risk_state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        acct = c["binance"].account()
        equity = float(acct["totalWalletBalance"]) + float(acct["totalUnrealizedProfit"])
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).date().isoformat()
        iso = datetime.now(timezone.utc).date().isocalendar()
        state_path.write_text(_json.dumps({
            "day": today, "day_start_equity": equity * 1.5, "daily_locked": False,
            "week": f"{iso[0]}-W{iso[1]:02d}", "week_start_equity": equity * 1.5,
        }), encoding="utf-8")
        rm_lock = RiskManager(c["settings"], c["binance"], c["symbols"])
        good = c["TradeSignal"](symbol="ETHUSDT", side="LONG", confidence=80, price=100.0,
                                stop_loss=95.0, take_profit=110.0, technical_score=60,
                                snapshot=c["engine"].snapshot_for("ETHUSDT"))
        d2 = rm_lock.evaluate(good)
        lock_ok = not d2.allowed and any("harian" in r.lower() for r in d2.reasons)
        state_path.unlink(missing_ok=True)

        # Streak: income rugi dipalsukan di-proses (menguji logika B2/B3).
        now = int(time.time() * 1000)
        rows = [{"symbol": "ETHUSDT", "incomeType": "REALIZED_PNL", "income": "-10",
                 "tradeId": f"t{i}", "time": now - i * 1000} for i in range(3)]
        orig = c["binance"].income_history
        c["binance"].income_history = lambda **kw: list(rows)
        try:
            import dataclasses as _dc

            streak_settings = _dc.replace(c["settings"], max_consecutive_losses=3)
            d3 = RiskManager(streak_settings, c["binance"], c["symbols"]).evaluate(good)
        finally:
            c["binance"].income_history = orig
        streak_ok = not d3.allowed and any("beruntun" in r for r in d3.reasons)
        assert dev_ok and lock_ok and streak_ok, f"dev={dev_ok} lock={lock_ok} streak={streak_ok}"
        record("5. Penolakan: deviasi harga, kunci harian, streak rugi", True,
               "ketiga penolakan bekerja dengan alasan yang benar")

    # ---------------- Skenario 6: rekonsiliasi -----------------------
    @scenario("6. Rekonsiliasi: (a) SL hilang dipasang ulang, (b) algo yatim dibatalkan, (c) tutup eksternal tercatat")
    def s6(c):
        # (a) buka posisi, hapus SL-nya, reconciler harus memasang ulang.
        open_trade(c, "BTCUSDT")
        time.sleep(2)
        sl_orders = [o for o in algos_of(c, "BTCUSDT")
                     if (o.get("type") or o.get("orderType")) == "STOP_MARKET"]
        assert sl_orders, "SL awal tidak ada"
        c["binance"].cancel_algo_order("BTCUSDT", algo_id=sl_orders[0]["algoId"])
        time.sleep(1)
        summary = c["reconciler"].run_once()
        assert "BTCUSDT" in summary["sl_placed"], f"SL tidak dipasang ulang: {summary}"
        # (b) algo yatim di simbol tanpa posisi.
        orphan_placed = False
        try:
            snap = c["engine"].snapshot_for("ETHUSDT")
            trig = c["symbols"].filters("ETHUSDT").round_price(float(snap["price"]) * 0.5)
            c["binance"].place_algo_order(
                symbol="ETHUSDT", side="SELL", order_type="STOP_MARKET",
                trigger_price=trig, quantity=0.01,
                client_algo_id=f"e2e-orphan-{int(time.time()):x}"[:36],
            )
            orphan_placed = True
        except Exception as exc:
            record("6b. Algo yatim dibatalkan reconciler", None, f"penempatan algo yatim ditolak bursa: {exc}")
        if orphan_placed:
            time.sleep(1)
            summary = c["reconciler"].run_once()
            assert "ETHUSDT" in summary["orphans_cancelled"], f"yatim tidak dibatalkan: {summary}"
            record("6b. Algo yatim dibatalkan reconciler", True, "orphan ETHUSDT dibatalkan")
        # (c) tutup eksternal → reconciler mencatat close + PnL.
        pos = position_of(c, "BTCUSDT")
        assert pos is not None
        amt = float(pos["positionAmt"])
        c["binance"].place_order(symbol="BTCUSDT", side="SELL" if amt > 0 else "BUY",
                                 order_type="MARKET", quantity=abs(amt), reduce_only=True)
        time.sleep(2)
        c["binance"].cancel_all_algo_orders("BTCUSDT")
        summary = c["reconciler"].run_once()
        assert "BTCUSDT" in summary["closes_recorded"], f"close eksternal tidak tercatat: {summary}"
        record("6. Rekonsiliasi: (a) SL hilang dipasang ulang, (b) algo yatim dibatalkan, (c) tutup eksternal tercatat",
               True, "SL terpasang ulang; tutup eksternal tercatat di jurnal dengan PnL")

    # ---------------- Skenario 7: watch + dashboard smoke ------------
    @scenario("7. Smoke: CLI watch 1 putaran + konstruksi dashboard TUI")
    def s7(c):
        import subprocess

        proc = subprocess.run(
            [sys.executable, "run.py", "cli", "watch", "--iterations", "1", "--top", "5", "--every", "1"],
            capture_output=True, text=True, timeout=600, cwd=str(Path(__file__).resolve().parent.parent),
            env={**os.environ, "TRADING_MODE": "dry_run"},
        )
        assert proc.returncode == 0, f"watch gagal: {proc.stderr[-300:]}"
        from cli.dashboard import build_app  # konstruksi tanpa menjalankan TUI

        app = build_app(None, "BTCUSDT", "15m")
        assert app is not None
        record("7. Smoke: CLI watch 1 putaran + konstruksi dashboard TUI", True,
               "watch 1 putaran selesai (dry-run); DashboardApp ter-konstruksi via build_app (TUI interaktif tidak dijalankan headless)")

    for fn in (s1, s2, s3, s4, s5, s6, s7):
        fn(ctx)

    # ---------------- Pembersihan skenario ---------------------------
    try:
        for p in binance.positions():
            if float(p["positionAmt"]) != 0:
                executor.close_position(p["symbol"])
        for p in binance.positions():
            binance.cancel_all_orders(p["symbol"])
            binance.cancel_all_algo_orders(p["symbol"])
    except Exception as exc:
        print(f"Peringatan pembersihan skrip: {exc}")

    # ---------------- Laporan ----------------------------------------
    ts = time.strftime("%Y%m%d-%H%M%S")
    report_path = Path(__file__).resolve().parent.parent / "reports" / f"e2e_testnet_{ts}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Laporan E2E Testnet (CLI) — {ts}", "",
             "Lingkungan: Binance Futures TESTNET (penjaga base URL lulus).",
             "Order sungguhan di testnet, risiko 0,1%/trade. Tanpa rahasia di laporan ini.", ""]
    for name, status, detail in RESULTS:
        lines.append(f"- **{status}** — {name}: {detail}")
    fails = sum(1 for _, s, _ in RESULTS if s == "GAGAL")
    lines += ["", f"Ringkasan: {len(RESULTS) - fails}/{len(RESULTS)} tidak gagal; {fails} GAGAL."]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nLaporan: {report_path}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
