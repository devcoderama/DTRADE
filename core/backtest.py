"""Mesin backtest: menguji aturan strategi pada data historis.

Menggunakan penilai yang SAMA dengan trading langsung — `technical_score`
dari `core.strategy` atas snapshot `core.indicators` — supaya hasil uji
mencerminkan perilaku sistem yang sebenarnya (tanpa komponen AI, karena
AI tidak deterministik dan berbayar per panggilan).

Aturan eksekusi simulasi:
- Sinyal dinilai pada penutupan candle ke-i dari jendela indikator TETAP
  (300 candle terakhir, sama seperti snapshot live); entri pada pembukaan
  candle berikutnya (tanpa look-ahead).
- Ukuran posisi dari risiko tetap (% saldo per trade), SL/TP kelipatan
  ATR seperti strategi live.
- Bila SL dan TP tersentuh pada candle yang sama, SL dianggap kena lebih
  dulu (asumsi konservatif). Bila candle MEMBUKA melewati SL/TP (gap),
  pengisian memakai harga pembukaan — bukan harga level.
- Biaya: fee taker per sisi (bawaan 0,05%), slippage per pengisian
  (bawaan 5 bps, selalu merugikan), dan funding per periode 8 jam selama
  posisi ditahan (asumsi suku konstan `funding_rate_8h_pct`; LONG membayar
  saat suku positif, SHORT menerima).
- Model keluar PositionManager (break-even / trailing / TP parsial)
  tersedia sebagai opsi dan memakai pemicu R yang sama seperti live;
  penyesuaian SL berlaku mulai candle berikutnya.
- Kurva ekuitas dicatat SETIAP candle (mark-to-market) sehingga
  Sharpe/Sortino ter-annualisasi berbasis waktu dengan benar.
- Posisi yang masih terbuka di akhir data ditutup pada harga terakhir.

PERINGATAN: backtest yang bagus TIDAK menjamin profit di masa depan.
Lihat `core/research.py` untuk validasi walk-forward & baseline acak.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from core.binance_client import BinanceFuturesClient
from core.history import interval_ms
from core.indicators import build_snapshot
from core.strategy import technical_score
from utils.logger import get_logger

log = get_logger(__name__)

_FUNDING_PERIOD_MS = 8 * 3600 * 1000


@dataclass
class BacktestConfig:
    initial_balance: float = 1000.0
    risk_pct: float = 1.0          # % saldo yang dipertaruhkan per trade
    fee_pct: float = 0.05          # fee taker per sisi, dalam %
    min_score: int = 40            # |skor teknikal| minimum untuk entri
    sl_atr_mult: float = 1.5
    tp_atr_mult: float = 2.5
    max_leverage: float = 5.0      # plafon notional = saldo × leverage ini
    warmup: int = 60               # candle awal untuk pemanasan indikator
    # --- Fase 4 ---
    window: int = 300              # jendela indikator tetap (samakan dgn live)
    scoring_version: int = 1       # 1 | 2 — sama seperti SCORING_VERSION live
    slippage_bps: float = 5.0      # slippage per pengisian, basis points
    funding_rate_8h_pct: float = 0.01  # asumsi suku funding per 8 jam (%)
    use_gates: bool = False        # gerbang rezim/ADX seperti live (Fase 3.2)
    adx_min: float = 20.0
    # Model keluar (None = mati), paritas PositionManager live (Fase 3.5):
    breakeven_trigger_r: Optional[float] = None
    trailing_trigger_r: Optional[float] = None
    trailing_atr_mult: float = 1.0
    partial_tp_r: Optional[float] = None
    partial_tp_fraction: float = 0.5


@dataclass
class BacktestTrade:
    side: str
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    quantity: float
    pnl: float
    exit_reason: str  # TP | SL | END — alasan leg penutup (PnL mencakup leg parsial)
    score: int


@dataclass
class BacktestResult:
    symbol: str
    interval: str
    candles: int
    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)  # {time, equity}
    initial_balance: float = 0.0
    final_balance: float = 0.0

    # --- statistik turunan ---
    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.pnl <= 0)

    @property
    def total_pnl(self) -> float:
        return self.final_balance - self.initial_balance

    @property
    def return_pct(self) -> float:
        if not self.initial_balance:
            return 0.0
        return self.total_pnl / self.initial_balance * 100.0

    @property
    def win_rate(self) -> float:
        return (self.wins / len(self.trades) * 100.0) if self.trades else 0.0

    @property
    def profit_factor(self) -> Optional[float]:
        gross_win = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        if gross_loss == 0:
            return None  # tak hingga / belum ada kerugian
        return gross_win / gross_loss

    @property
    def max_drawdown_pct(self) -> float:
        peak = 0.0
        worst = 0.0
        for point in self.equity_curve:
            eq = point["equity"]
            peak = max(peak, eq)
            if peak > 0:
                worst = min(worst, (eq / peak - 1.0) * 100.0)
        return worst

    @property
    def expectancy(self) -> float:
        """Rata-rata PnL per trade (USDT) — nilai harapan satu transaksi."""
        return (self.total_pnl / len(self.trades)) if self.trades else 0.0

    @property
    def avg_win(self) -> float:
        wins = [t.pnl for t in self.trades if t.pnl > 0]
        return (sum(wins) / len(wins)) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [t.pnl for t in self.trades if t.pnl < 0]
        return (sum(losses) / len(losses)) if losses else 0.0

    @property
    def max_consecutive_losses(self) -> int:
        worst = current = 0
        for t in self.trades:
            if t.pnl < 0:
                current += 1
                worst = max(worst, current)
            elif t.pnl > 0:
                current = 0
        return worst

    def _equity_returns(self) -> list[float]:
        out: list[float] = []
        curve = self.equity_curve
        for prev, cur in zip(curve, curve[1:]):
            if prev["equity"]:
                out.append(cur["equity"] / prev["equity"] - 1.0)
        return out

    def _bars_per_year(self) -> float:
        ms = interval_ms(self.interval)
        return (365.25 * 24 * 3600 * 1000) / ms if ms else 0.0

    @property
    def sharpe_ratio(self) -> Optional[float]:
        """Sharpe ter-annualisasi dari return per-bar kurva ekuitas."""
        rets = self._equity_returns()
        bpy = self._bars_per_year()
        if len(rets) < 2 or bpy <= 0:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        if std == 0:
            return None
        return mean / std * math.sqrt(bpy)

    @property
    def sortino_ratio(self) -> Optional[float]:
        """Sortino ter-annualisasi: hanya deviasi sisi turun yang dihitung."""
        rets = self._equity_returns()
        bpy = self._bars_per_year()
        if len(rets) < 2 or bpy <= 0:
            return None
        mean = sum(rets) / len(rets)
        downside = [r for r in rets if r < 0]
        if not downside:
            return None
        dvar = sum(r * r for r in downside) / len(rets)
        dstd = math.sqrt(dvar)
        if dstd == 0:
            return None
        return mean / dstd * math.sqrt(bpy)

    def to_dict(self, max_trades: int = 50) -> dict:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "candles": self.candles,
            "initial_balance": round(self.initial_balance, 2),
            "final_balance": round(self.final_balance, 2),
            "total_pnl": round(self.total_pnl, 2),
            "return_pct": round(self.return_pct, 2),
            "trades_count": len(self.trades),
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 1),
            "profit_factor": (round(self.profit_factor, 2) if self.profit_factor is not None else None),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "expectancy": round(self.expectancy, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "max_consecutive_losses": self.max_consecutive_losses,
            "sharpe_ratio": (round(self.sharpe_ratio, 2) if self.sharpe_ratio is not None else None),
            "sortino_ratio": (round(self.sortino_ratio, 2) if self.sortino_ratio is not None else None),
            "equity_curve": self.equity_curve,
            "trades": [
                {
                    "side": t.side,
                    "entry_time": t.entry_time,
                    "entry_price": t.entry_price,
                    "exit_time": t.exit_time,
                    "exit_price": t.exit_price,
                    "quantity": t.quantity,
                    "pnl": round(t.pnl, 2),
                    "exit_reason": t.exit_reason,
                    "score": t.score,
                }
                for t in self.trades[-max_trades:]
            ],
        }


def _candle(k: list) -> dict:
    return {
        "time": int(k[0]),
        "open": float(k[1]),
        "high": float(k[2]),
        "low": float(k[3]),
        "close": float(k[4]),
    }


def _funding_periods(entry_time: int, exit_time: int) -> int:
    """Jumlah batas periode funding 8 jam yang dilewati antara dua waktu."""
    if exit_time <= entry_time:
        return 0
    return (exit_time // _FUNDING_PERIOD_MS) - (entry_time // _FUNDING_PERIOD_MS)


def run_backtest(symbol: str, interval: str, klines: list[list], config: BacktestConfig) -> BacktestResult:
    """Fungsi murni: jalankan simulasi atas daftar klines mentah Binance."""
    candles = [_candle(k) for k in klines]
    result = BacktestResult(
        symbol=symbol,
        interval=interval,
        candles=len(candles),
        initial_balance=config.initial_balance,
        final_balance=config.initial_balance,
    )
    if len(candles) <= config.warmup + 2:
        return result

    balance = config.initial_balance
    slip = config.slippage_bps / 10_000.0

    def slipped(price: float, side: str, is_entry: bool) -> float:
        """Harga pengisian setelah slippage (selalu merugikan trader)."""
        # Entri LONG = beli (naik), keluar LONG = jual (turun); SHORT sebaliknya.
        buy = (side == "LONG") == is_entry
        return price * (1 + slip) if buy else price * (1 - slip)

    position: Optional[dict] = None

    def funding_cost(pos: dict, qty: float, exit_time: int) -> float:
        """Biaya funding untuk porsi `qty` yang ditutup pada exit_time.

        LONG membayar saat suku positif; SHORT menerima (biaya negatif).
        """
        periods = _funding_periods(pos["entry_time"], exit_time)
        if periods == 0 or config.funding_rate_8h_pct == 0:
            return 0.0
        notional = pos["entry_price"] * qty
        cost = notional * config.funding_rate_8h_pct / 100.0 * periods
        return cost if pos["side"] == "LONG" else -cost

    def close_leg(qty: float, raw_exit: float, exit_time: int, reason: str, final: bool) -> None:
        """Tutup sebagian/seluruh posisi; akumulasi PnL ke trade saat final."""
        nonlocal balance, position
        if position is None:
            return
        direction = 1.0 if position["side"] == "LONG" else -1.0
        exit_price = slipped(raw_exit, position["side"], is_entry=False)
        gross = (exit_price - position["entry_price"]) * qty * direction
        exit_fee = exit_price * qty * config.fee_pct / 100.0
        entry_fee_part = position["entry_fee"] * (qty / position["quantity_total"])
        pnl = gross - exit_fee - entry_fee_part - funding_cost(position, qty, exit_time)
        balance += pnl
        position["realized"] += pnl
        position["quantity"] -= qty
        if final or position["quantity"] <= 1e-12:
            result.trades.append(
                BacktestTrade(
                    side=position["side"],
                    entry_time=position["entry_time"],
                    entry_price=position["entry_price"],
                    exit_time=exit_time,
                    exit_price=exit_price,
                    quantity=position["quantity_total"],
                    pnl=position["realized"],
                    exit_reason=reason,
                    score=position["score"],
                )
            )
            position = None

    for i in range(config.warmup, len(candles) - 1):
        candle = candles[i]

        # 1) Kelola posisi terbuka pada candle ini
        if position is not None:
            side = position["side"]
            direction = 1.0 if side == "LONG" else -1.0
            sl, tp = position["stop_loss"], position["take_profit"]
            if side == "LONG":
                hit_sl, hit_tp = candle["low"] <= sl, candle["high"] >= tp
                gap_sl, gap_tp = candle["open"] <= sl, candle["open"] >= tp
            else:
                hit_sl, hit_tp = candle["high"] >= sl, candle["low"] <= tp
                gap_sl, gap_tp = candle["open"] >= sl, candle["open"] <= tp
            if hit_sl:  # konservatif: SL didahulukan; gap → isi di harga buka
                close_leg(position["quantity"], candle["open"] if gap_sl else sl,
                          candle["time"], "SL", final=True)
            elif hit_tp:
                close_leg(position["quantity"], candle["open"] if gap_tp else tp,
                          candle["time"], "TP", final=True)
            else:
                # TP parsial: harga menyentuh level R parsial pada candle ini
                if (
                    config.partial_tp_r is not None
                    and not position["partial_done"]
                ):
                    level = position["entry_price"] + direction * config.partial_tp_r * position["r_unit"]
                    reached = candle["high"] >= level if side == "LONG" else candle["low"] <= level
                    gapped = candle["open"] >= level if side == "LONG" else candle["open"] <= level
                    if reached:
                        qty = position["quantity"] * config.partial_tp_fraction
                        close_leg(qty, candle["open"] if gapped else level,
                                  candle["time"], "PARTIAL", final=False)
                        if position is not None:
                            position["partial_done"] = True
                # Penyesuaian SL (BE / trailing) — berlaku candle berikutnya
                if position is not None:
                    best = (candle["high"] if side == "LONG" else candle["low"])
                    profit_r = (best - position["entry_price"]) * direction / position["r_unit"]
                    new_sl: Optional[float] = None
                    if config.trailing_trigger_r is not None and profit_r >= config.trailing_trigger_r:
                        atr_now = position["atr"]
                        cand = (candle["close"] - direction * config.trailing_atr_mult * atr_now)
                        if (side == "LONG" and cand > position["stop_loss"]) or (
                            side == "SHORT" and cand < position["stop_loss"]
                        ):
                            new_sl = cand
                    if (
                        new_sl is None
                        and config.breakeven_trigger_r is not None
                        and profit_r >= config.breakeven_trigger_r
                    ):
                        be = position["entry_price"]
                        if (side == "LONG" and be > position["stop_loss"]) or (
                            side == "SHORT" and be < position["stop_loss"]
                        ):
                            new_sl = be
                    if new_sl is not None:
                        position["stop_loss"] = new_sl

        # 2) Cari sinyal baru pada penutupan candle ini
        if position is None:
            start = max(0, i + 1 - config.window)
            window = klines[start : i + 1]
            snapshot = build_snapshot(symbol, window, {}, {}, interval=interval)
            score, _ = technical_score(snapshot, version=config.scoring_version)
            atr_value = snapshot.get("atr14")
            gated = False
            if config.use_gates and abs(score) >= config.min_score:
                regime = snapshot.get("regime")
                adx = snapshot.get("adx14")
                if regime in {"high_volatility", "low_volatility"}:
                    gated = True
                elif (
                    regime in {"bullish_trending", "bearish_trending"}
                    and adx is not None and adx < config.adx_min
                ):
                    gated = True
            if abs(score) >= config.min_score and atr_value and not gated:
                side = "LONG" if score > 0 else "SHORT"
                nxt = candles[i + 1]
                entry = slipped(nxt["open"], side, is_entry=True)
                if side == "LONG":
                    stop_loss = entry - config.sl_atr_mult * atr_value
                    take_profit = entry + config.tp_atr_mult * atr_value
                else:
                    stop_loss = entry + config.sl_atr_mult * atr_value
                    take_profit = entry - config.tp_atr_mult * atr_value
                risk_amount = balance * config.risk_pct / 100.0
                per_unit_risk = abs(entry - stop_loss)
                if per_unit_risk <= 0:
                    continue
                quantity = risk_amount / per_unit_risk
                max_notional = balance * config.max_leverage
                if quantity * entry > max_notional:
                    quantity = max_notional / entry
                if quantity <= 0:
                    continue
                position = {
                    "side": side,
                    "entry_price": entry,
                    "entry_time": nxt["time"],
                    "quantity": quantity,
                    "quantity_total": quantity,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "score": score,
                    "entry_fee": entry * quantity * config.fee_pct / 100.0,
                    "r_unit": per_unit_risk,
                    "atr": atr_value,
                    "realized": 0.0,
                    "partial_done": False,
                }

        # 3) Titik kurva ekuitas per candle (mark-to-market)
        equity = balance
        if position is not None:
            direction = 1.0 if position["side"] == "LONG" else -1.0
            equity += (candle["close"] - position["entry_price"]) * position["quantity"] * direction
        result.equity_curve.append({"time": candle["time"], "equity": round(equity, 2)})

    # 4) Tutup posisi tersisa pada harga terakhir
    if position is not None:
        last = candles[-1]
        close_leg(position["quantity"], last["close"], last["time"], "END", final=True)

    result.final_balance = round(balance, 2)
    result.equity_curve.append({"time": candles[-1]["time"], "equity": result.final_balance})
    return result


class Backtester:
    """Pengambil data + pelari backtest (lapisan I/O tipis di atas fungsi murni)."""

    def __init__(self, binance: BinanceFuturesClient):
        self._binance = binance

    def run(
        self,
        symbol: str,
        interval: Optional[str] = "15m",
        limit: int = 1000,
        config: Optional[BacktestConfig] = None,
        days: Optional[int] = None,
    ) -> BacktestResult:
        config = config or BacktestConfig()
        interval = interval or "15m"
        if days:
            from core.history import load_history

            klines = load_history(self._binance, symbol.upper(), interval, days=days)
        else:
            klines = self._binance.klines(symbol.upper(), interval=interval, limit=min(limit, 1500))
        log.info("Backtest %s %s atas %d candle", symbol, interval, len(klines))
        return run_backtest(symbol.upper(), interval, klines, config)
