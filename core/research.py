"""Riset strategi: walk-forward, sweep parameter, dan baseline acak.

Tujuan modul ini adalah KEJUJURAN statistik, bukan mencari angka bagus:

- **Walk-forward**: parameter dipilih HANYA dari data latih (train),
  lalu dinilai pada data uji (test) yang tidak pernah dilihat saat
  pemilihan. Hasil yang dilaporkan adalah hasil TEST, bukan train.
- **Baseline acak**: strategi dibandingkan dengan entri acak (sisi &
  waktu acak, aturan keluar & biaya yang sama). Strategi yang tidak
  mengalahkan baseline acak secara konsisten tidak punya edge yang
  terbukti — titik.
- **Tanpa klaim profit**: laporan selalu memuat peringatan bahwa hasil
  historis tidak menjamin hasil masa depan, biaya memakai asumsi, dan
  sampel kecil tidak signifikan.

Semua fungsi murni atas daftar klines (tanpa jaringan) agar teruji.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.backtest import BacktestConfig, BacktestResult, run_backtest
from core.indicators import atr
from utils.logger import get_logger

log = get_logger(__name__)


# ----------------------------------------------------------------------
# Walk-forward
# ----------------------------------------------------------------------
@dataclass
class FoldResult:
    fold: int
    train_candles: int
    test_candles: int
    chosen_min_score: int
    chosen_scoring_version: int
    train_trades: int
    test_trades: int
    test_pnl: float
    test_win_rate: float
    test_expectancy: float


def _trade_stats(trades: list) -> tuple[int, float, float, float]:
    """(jumlah, total pnl, win rate %, expectancy) dari daftar trade."""
    if not trades:
        return 0, 0.0, 0.0, 0.0
    total = sum(t.pnl for t in trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    return len(trades), total, wins / len(trades) * 100.0, total / len(trades)


def walk_forward(
    symbol: str,
    interval: str,
    klines: list[list],
    base_config: BacktestConfig,
    folds: int = 4,
    min_scores: tuple[int, ...] = (25, 40, 55),
    scoring_versions: tuple[int, ...] = (1, 2),
    min_train_trades: int = 3,
) -> list[FoldResult]:
    """Walk-forward expanding-window.

    Data dibagi `folds + 1` segmen berurutan. Fold k: train = semua
    segmen sebelum segmen uji k; parameter terbaik (expectancy tertinggi
    di train, syarat jumlah trade minimum) diterapkan ke segmen uji
    dengan 300 candle ekor train sebagai konteks pemanasan indikator.
    """
    n = len(klines)
    seg = n // (folds + 1)
    results: list[FoldResult] = []
    if seg < base_config.warmup + 10:
        log.warning("Data terlalu pendek untuk walk-forward (%d candle).", n)
        return results
    for k in range(folds):
        train = klines[: seg * (k + 1)]
        test_start_idx = seg * (k + 1)
        test_end_idx = seg * (k + 2) if k < folds - 1 else n
        test = klines[test_start_idx:test_end_idx]
        if len(test) < 10:
            continue
        best: Optional[tuple[float, int, int, int]] = None  # (expectancy, min_score, version, train_trades)
        for ms in min_scores:
            for ver in scoring_versions:
                cfg = replace(base_config, min_score=ms, scoring_version=ver)
                train_res = run_backtest(symbol, interval, train, cfg)
                cnt, _pnl, _wr, exp = _trade_stats(train_res.trades)
                if cnt >= min_train_trades and (best is None or exp > best[0]):
                    best = (exp, ms, ver, cnt)
        if best is None:
            continue
        _exp, chosen_ms, chosen_ver, train_trades = best
        cfg = replace(base_config, min_score=chosen_ms, scoring_version=chosen_ver)
        context = train[-300:]
        test_with_ctx = context + test
        test_start_time = int(test[0][0])
        test_res = run_backtest(symbol, interval, test_with_ctx, cfg)
        test_trades = [t for t in test_res.trades if t.entry_time >= test_start_time]
        cnt, pnl, wr, exp = _trade_stats(test_trades)
        results.append(
            FoldResult(
                fold=k + 1,
                train_candles=len(train),
                test_candles=len(test),
                chosen_min_score=chosen_ms,
                chosen_scoring_version=chosen_ver,
                train_trades=train_trades,
                test_trades=cnt,
                test_pnl=round(pnl, 2),
                test_win_rate=round(wr, 1),
                test_expectancy=round(exp, 2),
            )
        )
    return results


# ----------------------------------------------------------------------
# Baseline acak
# ----------------------------------------------------------------------
@dataclass
class RandomBaseline:
    runs: int
    target_trades: int
    mean_pnl: float
    best_pnl: float
    worst_pnl: float
    mean_win_rate: float
    pct_runs_profitable: float


def _random_run(
    candles: list[dict], config: BacktestConfig, target_trades: int, rng: random.Random
) -> tuple[float, int, int]:
    """Satu lari baseline: entri acak, keluar SL/TP ATR yang sama, biaya sama."""
    balance = config.initial_balance
    n = len(candles)
    if n <= config.warmup + 2 or target_trades <= 0:
        return 0.0, 0, 0
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    slip = config.slippage_bps / 10_000.0
    p_enter = target_trades / max(1, (n - config.warmup - 1))
    trades = wins = 0
    i = config.warmup
    while i < n - 1 and trades < target_trades * 3:  # pengaman loop
        if rng.random() > p_enter:
            i += 1
            continue
        lo = max(0, i - 100)
        atr_v = atr(highs[lo : i + 1], lows[lo : i + 1], closes[lo : i + 1])
        if not atr_v:
            i += 1
            continue
        side = "LONG" if rng.random() < 0.5 else "SHORT"
        direction = 1.0 if side == "LONG" else -1.0
        nxt = candles[i + 1]
        entry = nxt["open"] * (1 + slip) if side == "LONG" else nxt["open"] * (1 - slip)
        sl = entry - direction * config.sl_atr_mult * atr_v
        tp = entry + direction * config.tp_atr_mult * atr_v
        risk_amount = balance * config.risk_pct / 100.0
        qty = risk_amount / abs(entry - sl)
        max_notional = balance * config.max_leverage
        if qty * entry > max_notional:
            qty = max_notional / entry
        entry_fee = entry * qty * config.fee_pct / 100.0
        exit_price, exit_time = closes[-1], candles[-1]["time"]
        j = i + 1
        while j < n:
            c = candles[j]
            if side == "LONG":
                if c["low"] <= sl:
                    exit_price, exit_time = (c["open"] if c["open"] <= sl else sl), c["time"]
                    break
                if c["high"] >= tp:
                    exit_price, exit_time = (c["open"] if c["open"] >= tp else tp), c["time"]
                    break
            else:
                if c["high"] >= sl:
                    exit_price, exit_time = (c["open"] if c["open"] >= sl else sl), c["time"]
                    break
                if c["low"] <= tp:
                    exit_price, exit_time = (c["open"] if c["open"] <= tp else tp), c["time"]
                    break
            j += 1
        exit_fill = exit_price * (1 - slip) if side == "LONG" else exit_price * (1 + slip)
        gross = (exit_fill - entry) * qty * direction
        exit_fee = exit_fill * qty * config.fee_pct / 100.0
        periods = (exit_time // (8 * 3600 * 1000)) - (nxt["time"] // (8 * 3600 * 1000))
        funding = entry * qty * config.funding_rate_8h_pct / 100.0 * max(0, periods)
        pnl = gross - entry_fee - exit_fee - (funding if side == "LONG" else -funding)
        balance += pnl
        trades += 1
        if pnl > 0:
            wins += 1
        i = j + 1
    total = balance - config.initial_balance
    wr = wins / trades * 100.0 if trades else 0.0
    return total, trades, wins if trades else 0


def random_baseline(
    symbol: str,
    interval: str,
    klines: list[list],
    config: BacktestConfig,
    target_trades: int,
    seeds: int = 20,
) -> RandomBaseline:
    """Rata-rata `seeds` lari entri-acak dengan jumlah trade ~ strategi."""
    from core.backtest import _candle

    candles = [_candle(k) for k in klines]
    pnls: list[float] = []
    win_rates: list[float] = []
    for seed in range(1, seeds + 1):
        pnl, trades, wins = _random_run(candles, config, target_trades, random.Random(seed))
        pnls.append(pnl)
        win_rates.append(wins / trades * 100.0 if trades else 0.0)
    if not pnls:
        return RandomBaseline(seeds, target_trades, 0.0, 0.0, 0.0, 0.0, 0.0)
    return RandomBaseline(
        runs=seeds,
        target_trades=target_trades,
        mean_pnl=round(sum(pnls) / len(pnls), 2),
        best_pnl=round(max(pnls), 2),
        worst_pnl=round(min(pnls), 2),
        mean_win_rate=round(sum(win_rates) / len(win_rates), 1),
        pct_runs_profitable=round(sum(1 for p in pnls if p > 0) / len(pnls) * 100.0, 1),
    )


# ----------------------------------------------------------------------
# Laporan
# ----------------------------------------------------------------------
def _fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build_report(
    symbol: str,
    interval: str,
    klines: list[list],
    base_config: BacktestConfig,
    full_result: BacktestResult,
    folds: list[FoldResult],
    baseline: RandomBaseline,
) -> str:
    d = full_result.to_dict()
    start = _fmt_ts(int(klines[0][0])) if klines else "-"
    end = _fmt_ts(int(klines[-1][0])) if klines else "-"
    lines = [
        f"# Laporan Riset — {symbol} {interval}",
        "",
        f"- Data: {len(klines)} candle, {start} → {end}",
        f"- Konfigurasi dasar: risiko {base_config.risk_pct}%/trade, fee {base_config.fee_pct}%/sisi, "
        f"slippage {base_config.slippage_bps} bps, funding {base_config.funding_rate_8h_pct}%/8 jam, "
        f"skor v{base_config.scoring_version}, min_score {base_config.min_score}",
        "",
        "## Backtest penuh (in-sample — angka paling optimistis)",
        "",
        f"- Trade: {d['trades_count']} (win rate {d['win_rate']}%), PnL {d['total_pnl']:+.2f} USDT "
        f"({d['return_pct']:+.2f}%), expectancy {d['expectancy']:+.2f} USDT/trade",
        f"- Profit factor: {d['profit_factor']}, max drawdown {d['max_drawdown_pct']}%, "
        f"Sharpe {d['sharpe_ratio']}, Sortino {d['sortino_ratio']}",
        "",
        "## Walk-forward (out-of-sample — angka yang lebih bisa dipercaya)",
        "",
        "| Fold | Train | Test | Parameter terpilih | Trade uji | PnL uji | Win rate | Expectancy |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in folds:
        lines.append(
            f"| {f.fold} | {f.train_candles} | {f.test_candles} | min_score {f.chosen_min_score}, "
            f"v{f.chosen_scoring_version} | {f.test_trades} | {f.test_pnl:+.2f} | {f.test_win_rate}% | "
            f"{f.test_expectancy:+.2f} |"
        )
    if folds:
        total_test_pnl = round(sum(f.test_pnl for f in folds), 2)
        total_test_trades = sum(f.test_trades for f in folds)
        lines.append("")
        lines.append(f"Total out-of-sample: {total_test_trades} trade, PnL {total_test_pnl:+.2f} USDT.")
    lines += [
        "",
        "## Baseline acak (entri acak, aturan keluar & biaya sama)",
        "",
        f"- {baseline.runs} lari, target ~{baseline.target_trades} trade/lari",
        f"- PnL rata-rata {baseline.mean_pnl:+.2f} USDT (terbaik {baseline.best_pnl:+.2f}, "
        f"terburuk {baseline.worst_pnl:+.2f}), win rate rata-rata {baseline.mean_win_rate}%, "
        f"{baseline.pct_runs_profitable}% lari berakhir profit",
        "",
        "## Cara membaca laporan ini",
        "",
        "- Bandingkan PnL out-of-sample walk-forward dengan baseline acak. Bila strategi tidak "
        "mengalahkan baseline secara konsisten, edge-nya belum terbukti.",
        "- Jumlah trade kecil (< 30) membuat semua metrik tidak stabil secara statistik.",
        "",
        "> PERINGATAN: Hasil historis TIDAK menjamin hasil masa depan. Backtest memakai asumsi "
        "biaya (fee/slippage/funding konstan) dan tidak memodelkan antrean order, likuiditas "
        "nyata, atau perubahan rezim pasar. Jangan memakai dana nyata berdasarkan laporan ini "
        "tanpa forward-test dry-run/testnet berminggu-minggu.",
        "",
    ]
    return "\n".join(lines)


def save_report(markdown: str, symbol: str, interval: str, reports_dir: Optional[Path] = None) -> Path:
    reports_dir = Path(reports_dir) if reports_dir else Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    path = reports_dir / f"research-{symbol.upper()}-{interval}-{stamp}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def run_research(
    symbol: str,
    interval: str,
    klines: list[list],
    base_config: Optional[BacktestConfig] = None,
    folds: int = 4,
    reports_dir: Optional[Path] = None,
) -> tuple[str, Path]:
    """Orkestrasi penuh: backtest + walk-forward + baseline + simpan laporan."""
    base_config = base_config or BacktestConfig()
    full = run_backtest(symbol, interval, klines, base_config)
    fold_results = walk_forward(symbol, interval, klines, base_config, folds=folds)
    baseline = random_baseline(symbol, interval, klines, base_config, target_trades=max(1, len(full.trades)))
    md = build_report(symbol, interval, klines, base_config, full, fold_results, baseline)
    path = save_report(md, symbol, interval, reports_dir)
    log.info("Laporan riset tersimpan: %s", path)
    return md, path
