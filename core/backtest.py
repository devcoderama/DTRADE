"""Mesin backtest: menguji aturan strategi pada data historis.

Menggunakan penilai yang SAMA dengan trading langsung — `technical_score`
dari `core.strategy` atas snapshot `core.indicators` — supaya hasil uji
mencerminkan perilaku sistem yang sebenarnya (tanpa komponen AI, karena
AI tidak deterministik dan berbayar per panggilan).

Aturan eksekusi simulasi:
- Sinyal dinilai pada penutupan candle ke-i; entri pada pembukaan candle
  berikutnya (tanpa look-ahead).
- Ukuran posisi dari risiko tetap (% saldo per trade), SL 1.5×ATR dan
  TP 2.5×ATR seperti strategi live.
- Bila SL dan TP tersentuh pada candle yang sama, SL dianggap kena lebih
  dulu (asumsi konservatif).
- Biaya: fee taker per sisi (bawaan 0.05%) atas notional.
- Posisi yang masih terbuka di akhir data ditutup pada harga terakhir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.binance_client import BinanceFuturesClient
from core.indicators import build_snapshot
from core.strategy import technical_score
from utils.logger import get_logger

log = get_logger(__name__)


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


@dataclass
class BacktestTrade:
    side: str
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    quantity: float
    pnl: float
    exit_reason: str  # TP | SL | END
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

    @property
    def sharpe_ratio(self) -> Optional[float]:
        """Sharpe dari return antar-titik kurva ekuitas (per langkah, bukan tahunan)."""
        import math

        rets = self._equity_returns()
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        if std == 0:
            return None
        return mean / std * math.sqrt(len(rets))

    @property
    def sortino_ratio(self) -> Optional[float]:
        """Sortino: seperti Sharpe tetapi hanya memakai deviasi sisi turun."""
        import math

        rets = self._equity_returns()
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        downside = [r for r in rets if r < 0]
        if not downside:
            return None
        dvar = sum(r * r for r in downside) / len(rets)
        dstd = math.sqrt(dvar)
        if dstd == 0:
            return None
        return mean / dstd * math.sqrt(len(rets))

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
    result.equity_curve.append({"time": candles[0]["time"], "equity": round(balance, 2)})

    position: Optional[dict] = None

    def close_position(exit_price: float, exit_time: int, reason: str) -> None:
        nonlocal balance, position
        assert position is not None
        direction = 1.0 if position["side"] == "LONG" else -1.0
        gross = (exit_price - position["entry_price"]) * position["quantity"] * direction
        exit_fee = exit_price * position["quantity"] * config.fee_pct / 100.0
        pnl = gross - position["entry_fee"] - exit_fee
        balance += pnl
        result.trades.append(
            BacktestTrade(
                side=position["side"],
                entry_time=position["entry_time"],
                entry_price=position["entry_price"],
                exit_time=exit_time,
                exit_price=exit_price,
                quantity=position["quantity"],
                pnl=pnl,
                exit_reason=reason,
                score=position["score"],
            )
        )
        result.equity_curve.append({"time": exit_time, "equity": round(balance, 2)})
        position = None

    for i in range(config.warmup, len(candles) - 1):
        candle = candles[i]

        # 1) Kelola posisi terbuka: cek SL/TP pada candle ini
        if position is not None:
            if position["side"] == "LONG":
                hit_sl = candle["low"] <= position["stop_loss"]
                hit_tp = candle["high"] >= position["take_profit"]
            else:
                hit_sl = candle["high"] >= position["stop_loss"]
                hit_tp = candle["low"] <= position["take_profit"]
            if hit_sl:  # konservatif: SL didahulukan bila keduanya kena
                close_position(position["stop_loss"], candle["time"], "SL")
            elif hit_tp:
                close_position(position["take_profit"], candle["time"], "TP")

        # 2) Cari sinyal baru pada penutupan candle ini
        if position is None:
            window = klines[: i + 1]
            snapshot = build_snapshot(symbol, window, {}, {})
            score, _ = technical_score(snapshot)
            atr_value = snapshot.get("atr14")
            if abs(score) >= config.min_score and atr_value:
                side = "LONG" if score > 0 else "SHORT"
                nxt = candles[i + 1]
                entry = nxt["open"]
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
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "score": score,
                    "entry_fee": entry * quantity * config.fee_pct / 100.0,
                }

    # 3) Tutup posisi tersisa pada harga terakhir
    if position is not None:
        last = candles[-1]
        close_position(last["close"], last["time"], "END")

    result.final_balance = round(balance, 2)
    # Samakan titik akhir kurva dengan saldo final
    if result.equity_curve:
        result.equity_curve[-1] = {"time": candles[-1]["time"], "equity": result.final_balance}
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
    ) -> BacktestResult:
        config = config or BacktestConfig()
        interval = interval or "15m"
        klines = self._binance.klines(symbol.upper(), interval=interval, limit=min(limit, 1500))
        log.info("Backtest %s %s atas %d candle", symbol, interval, len(klines))
        return run_backtest(symbol.upper(), interval, klines, config)
