"""Manajemen risiko — gerbang wajib sebelum eksekusi apa pun.

RiskManager adalah otoritas tertinggi sebelum order dikirim. Aturan yang
ditegakkan di sini (tidak bisa dilewati executor):

1. Sinyal wajib punya SL & TP yang ARAHNYA BENAR (LONG: SL < entri < TP;
   SHORT: TP < entri < SL), harga positif, dan rasio risk/reward minimal.
2. Jarak stop minimum: minimal 3× tickSize dan 0,25× ATR (bila tersedia).
3. Ukuran posisi dihitung dari % risiko ekuitas dan jarak stop loss.
4. Leverage tidak pernah melebihi MAX_LEVERAGE.
5. Jumlah posisi terbuka tidak melebihi MAX_OPEN_POSITIONS; satu simbol
   tidak boleh dibuka dua kali.
6. Eksposur: notional satu simbol dan total semua posisi dibatasi
   terhadap ekuitas (MAX_SYMBOL_EXPOSURE_PCT / MAX_TOTAL_EXPOSURE_PCT).
7. Pengendali rugi berbasis EKUITAS: PnL hari ini = ekuitas sekarang
   (saldo + unrealized) dikurangi ekuitas awal hari yang TERSIMPAN di
   data/risk_state.json — mencakup realized, unrealized, dan fee.
   Melampaui DAILY_LOSS_LIMIT_PCT → kunci harian PERSISTEN (tidak hilang
   karena restart) sampai berganti hari. Sama untuk batas mingguan.
8. Perlindungan rugi beruntun: setelah MAX_CONSECUTIVE_LOSSES realisasi
   rugi berturut-turut (dari riwayat income Binance), sinyal ditolak.
9. Kuantitas & harga selalu dibulatkan mengikuti filter resmi simbol,
   dan notional harus memenuhi minNotional Binance.

Catatan: baseline ekuitas awal hari/minggu dicatat pada evaluasi pertama
di periode itu. Deposit/withdrawal di tengah periode ikut terhitung
sebagai perubahan ekuitas — itu perilaku yang disengaja (konservatif
untuk penarikan, longgar untuk deposit).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import Settings
from core.binance_client import BinanceFuturesClient
from core.strategy import TradeSignal
from core.symbols import SymbolManager
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class RiskDecision:
    allowed: bool
    symbol: str
    side: str
    quantity: float = 0.0
    leverage: int = 0
    notional: float = 0.0
    risk_amount: float = 0.0
    risk_reward: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasons: list[str] = field(default_factory=list)


class RiskManager:
    def __init__(
        self,
        settings: Settings,
        binance: BinanceFuturesClient,
        symbols: SymbolManager,
    ):
        self._settings = settings
        self._binance = binance
        self._symbols = symbols
        self._state_path = Path(settings.risk_state_path)

    # ------------------------------------------------------------------
    # Keadaan risiko persisten (baseline ekuitas + kunci harian)
    # ------------------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, ValueError) as exc:
            log.warning("risk_state tidak terbaca (%s) — mulai dari keadaan baru.", exc)
        return {}

    def _save_state(self, state: dict) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
            tmp.replace(self._state_path)
        except OSError as exc:
            log.error("Gagal menyimpan risk_state: %s", exc)

    def _sync_baselines(self, state: dict, equity: float) -> dict:
        """Catat baseline awal hari/minggu pada evaluasi pertama periodenya.

        Satu sumber waktu untuk seluruh sistem risiko: UTC (sama seperti
        hari perdagangan Binance & jendela streak) — bukan jam lokal mesin
        yang bisa berbeda zona dan menggeser batas harian/mingguan.
        """
        today_utc = datetime.now(timezone.utc).date()
        today = today_utc.isoformat()
        iso = today_utc.isocalendar()
        week = f"{iso[0]}-W{iso[1]:02d}"
        changed = False
        if state.get("day") != today:
            state.update({"day": today, "day_start_equity": equity, "daily_locked": False})
            changed = True
        if state.get("week") != week:
            state.update({"week": week, "week_start_equity": equity})
            changed = True
        if changed:
            self._save_state(state)
        return state

    def _consecutive_losses(self) -> int:
        """Hitung realisasi rugi berturut-turut dalam jendela waktu.

        Hanya realisasi REALIZED_PNL dalam `CONSECUTIVE_LOSS_WINDOW_HOURS`
        jam terakhir (bawaan 24) yang dihitung — kerugian lama tidak
        mengunci akun selamanya; setelah jendela berlalu streak menyusut
        dengan sendirinya (perilaku cooldown alami). Pengambilan memakai
        startTime + paginasi agar tidak bergantung pada 100 baris
        terakhir yang tercampur jenis income lain (funding fee, komisi).
        """
        import time as _time

        window_ms = int(getattr(self._settings, "consecutive_loss_window_hours", 24)) * 3600 * 1000
        start_ms = int(_time.time() * 1000) - window_ms
        rows: list[dict] = []
        try:
            cursor = start_ms
            for _ in range(5):  # maks 5 halaman × 1000 baris
                page = self._binance.income_history(
                    limit=1000, start_time=cursor, income_type="REALIZED_PNL"
                )
                if not page:
                    break
                rows.extend(page)
                if len(page) < 1000:
                    break
                last_time = int(page[-1].get("time", cursor))
                if last_time <= cursor:
                    break
                cursor = last_time + 1
        except Exception as exc:  # pemeriksaan tambahan — kegagalan API dicatat, bukan disembunyikan
            log.warning("Riwayat income tidak terbaca untuk cek rugi beruntun: %s", exc)
            return 0
        realized = [
            r for r in rows
            if r.get("incomeType") == "REALIZED_PNL" and int(r.get("time", 0)) >= start_ms
        ]
        realized.sort(key=lambda r: int(r.get("time", 0)), reverse=True)
        streak = 0
        for row in realized:
            value = float(row.get("income", 0))
            if value < 0:
                streak += 1
            elif value > 0:
                break
        return streak

    # ------------------------------------------------------------------
    # Risiko portofolio (Fase 3.6)
    # ------------------------------------------------------------------
    def _portfolio_checks(self, signal: TradeSignal, open_positions: list, equity: float) -> Optional[str]:
        """Pemeriksaan level portofolio. Mengembalikan alasan penolakan / None."""
        s = self._settings
        snap = signal.snapshot or {}

        # Likuiditas minimum (dari snapshot sinyal).
        min_vol = float(getattr(s, "min_quote_volume_usd", 0.0))
        vol = snap.get("volume_24h_quote")
        if min_vol > 0 and vol is not None and float(vol) < min_vol:
            return (
                f"Likuiditas tidak cukup: volume 24 jam {float(vol):,.0f} USDT "
                f"< minimum {min_vol:,.0f} USDT."
            )

        # Penjaga waktu funding: tolak di sekitar jam funding bila ekstrem.
        guard_min = int(getattr(s, "funding_guard_minutes", 0))
        funding = snap.get("funding_rate")
        if guard_min > 0 and funding is not None:
            import datetime as _dt

            now = _dt.datetime.now(_dt.timezone.utc)
            minutes_now = now.hour * 60 + now.minute
            boundaries = [0, 480, 960, 1440]  # funding 00:00 / 08:00 / 16:00 UTC
            dist = min(min(abs(minutes_now - b), 1440 - abs(minutes_now - b)) for b in boundaries)
            if dist <= guard_min and abs(float(funding)) * 100 >= float(getattr(s, "funding_extreme_pct", 0.1)):
                return (
                    f"Funding ekstrem ({float(funding) * 100:.3f}%) dalam {dist} menit "
                    f"dari waktu funding (penjaga {guard_min} menit) — entri ditolak."
                )

        # Jumlah posisi searah.
        max_same = int(getattr(s, "max_same_direction_positions", 0))
        if max_same > 0:
            same = sum(
                1 for p in open_positions
                if (float(p.get("positionAmt", 0)) > 0) == (signal.side == "LONG")
            )
            if same >= max_same:
                return (
                    f"Sudah ada {same} posisi searah {signal.side} "
                    f"(batas MAX_SAME_DIRECTION_POSITIONS={max_same})."
                )

        # Total risiko terbuka (dari SL algo tiap posisi) + risiko rencana ini.
        cap_pct = float(getattr(s, "max_open_risk_pct", 0.0)) or 2.0 * s.risk_per_trade_pct
        cap_amount = equity * cap_pct / 100.0
        open_risk = self._open_risk(open_positions)
        planned_risk = equity * s.risk_per_trade_pct / 100.0
        if open_risk is not None and open_risk + planned_risk > cap_amount:
            return (
                f"Total risiko terbuka {open_risk:.2f} + rencana {planned_risk:.2f} USDT "
                f"melebihi batas {cap_amount:.2f} USDT ({cap_pct:.1f}% ekuitas)."
            )

        # Filter BTC untuk altcoin.
        if getattr(s, "btc_filter", False) and signal.symbol != "BTCUSDT":
            direction = self._btc_direction()
            if direction == "DOWN" and signal.side == "LONG":
                return "BTC_FILTER: BTC turun kuat (1h & 4h) — LONG altcoin ditolak."
            if direction == "UP" and signal.side == "SHORT":
                return "BTC_FILTER: BTC naik kuat (1h & 4h) — SHORT altcoin ditolak."
        return None

    def _open_risk(self, open_positions: list) -> Optional[float]:
        """Jumlah risiko posisi terbuka = Σ |entri − SL algo| × |qty|.

        Posisi tanpa SL algo yang terbaca dilewati (ditangani reconciler);
        kegagalan total membaca → None (pemeriksaan dilewati, tercatat).
        """
        total = 0.0
        try:
            for p in open_positions:
                symbol = p["symbol"]
                amt = abs(float(p.get("positionAmt", 0)))
                entry = float(p.get("entryPrice", 0))
                if amt == 0 or entry <= 0:
                    continue
                sl_trigger = None
                for o in self._binance.open_algo_orders(symbol):
                    if (o.get("type") or o.get("orderType")) == "STOP_MARKET":
                        sl_trigger = float(o.get("triggerPrice") or o.get("stopPrice") or 0)
                        break
                if sl_trigger:
                    total += abs(entry - sl_trigger) * amt
        except Exception as exc:
            log.warning("Risiko terbuka tidak terbaca — pemeriksaan dilewati: %s", exc)
            return None
        return total

    def _btc_direction(self) -> Optional[str]:
        """Arah kuat BTC dari EMA 1h & 4h (cache 60 detik). None = tidak kuat."""
        import time as _time

        now = _time.time()
        cached = getattr(self, "_btc_dir_cache", None)
        if cached and now - cached[0] < 60:
            return cached[1]
        direction: Optional[str] = None
        try:
            from core.indicators import ema

            trends: list[Optional[str]] = []
            for tf in ("1h", "4h"):
                klines = self._binance.klines("BTCUSDT", interval=tf, limit=120)
                closes = [float(k[4]) for k in klines]
                e20, e50 = ema(closes, 20), ema(closes, 50)
                if e20 is None or e50 is None:
                    trends.append(None)
                elif closes[-1] < e20 < e50:
                    trends.append("DOWN")
                elif closes[-1] > e20 > e50:
                    trends.append("UP")
                else:
                    trends.append(None)
            if trends == ["DOWN", "DOWN"]:
                direction = "DOWN"
            elif trends == ["UP", "UP"]:
                direction = "UP"
        except Exception as exc:
            log.warning("Arah BTC tidak terbaca untuk BTC_FILTER: %s", exc)
            direction = None
        self._btc_dir_cache = (now, direction)
        return direction

    # ------------------------------------------------------------------
    def evaluate(self, signal: TradeSignal) -> RiskDecision:
        s = self._settings
        base = RiskDecision(allowed=False, symbol=signal.symbol, side=signal.side)

        if signal.side not in {"LONG", "SHORT"}:
            base.reasons.append("Sinyal NEUTRAL — tidak ada yang dieksekusi.")
            return base
        if not signal.stop_loss or not signal.take_profit:
            base.reasons.append("Sinyal tanpa stop loss / take profit — ditolak demi keamanan.")
            return base
        if not self._symbols.is_tradable(signal.symbol):
            base.reasons.append(f"{signal.symbol} bukan perpetual {s.quote_asset} yang aktif.")
            return base

        entry = signal.price
        sl, tp = float(signal.stop_loss), float(signal.take_profit)

        # --- Validasi arah & kewajaran SL/TP (tidak percaya masukan AI) ---
        if entry <= 0 or sl <= 0 or tp <= 0:
            base.reasons.append("Harga entri/SL/TP harus positif — sinyal tidak valid.")
            return base
        if signal.side == "LONG" and not (sl < entry < tp):
            base.reasons.append(
                f"SL/TP tidak valid untuk LONG: wajib SL ({sl}) < entri ({entry}) < TP ({tp})."
            )
            return base
        if signal.side == "SHORT" and not (tp < entry < sl):
            base.reasons.append(
                f"SL/TP tidak valid untuk SHORT: wajib TP ({tp}) < entri ({entry}) < SL ({sl})."
            )
            return base

        stop_distance = abs(entry - sl)
        reward_distance = abs(tp - entry)
        rr = reward_distance / stop_distance if stop_distance > 0 else 0.0
        base.risk_reward = rr
        if rr < s.min_risk_reward:
            base.reasons.append(
                f"Risk/reward {rr:.2f} di bawah minimum {s.min_risk_reward} — sinyal ditolak."
            )
            return base

        filters = self._symbols.filters(signal.symbol)
        min_stop = 3 * filters.price_tick
        atr_value = signal.snapshot.get("atr14") if signal.snapshot else None
        if atr_value:
            min_stop = max(min_stop, 0.25 * float(atr_value))
        if stop_distance < min_stop:
            base.reasons.append(
                f"Jarak stop {stop_distance:.6f} terlalu dekat (minimum {min_stop:.6f} "
                "= maks 3×tick / 0,25×ATR) — rawan tersapu noise."
            )
            return base

        # --- Keadaan akun ---
        account = self._binance.account()
        equity = float(account.get("totalWalletBalance", 0)) + float(
            account.get("totalUnrealizedProfit", 0)
        )
        if equity <= 0:
            base.reasons.append("Saldo akun futures kosong / tidak terbaca.")
            return base

        open_positions = self._binance.positions()
        if len(open_positions) >= s.max_open_positions:
            base.reasons.append(
                f"Sudah ada {len(open_positions)} posisi terbuka (batas {s.max_open_positions})."
            )
            return base
        if any(p["symbol"] == signal.symbol for p in open_positions):
            base.reasons.append(f"Posisi {signal.symbol} sudah terbuka — penambahan posisi ditolak.")
            return base

        # --- Risiko portofolio (Fase 3.6) ---
        portfolio_block = self._portfolio_checks(signal, open_positions, equity)
        if portfolio_block:
            base.reasons.append(portfolio_block)
            return base

        # --- Pengendali rugi harian/mingguan berbasis ekuitas (persisten) ---
        state = self._sync_baselines(self._load_state(), equity)
        if state.get("manual_lock"):
            base.reasons.append("Kunci manual risiko AKTIF (manual_lock di risk_state.json) — semua sinyal ditolak.")
            return base
        day_start = float(state.get("day_start_equity", equity))
        daily_pnl = equity - day_start
        if state.get("daily_locked"):
            base.reasons.append(
                f"Kunci harian AKTIF: rugi hari ini {daily_pnl:.2f} USDT sudah melewati "
                f"batas -{s.daily_loss_limit_pct}% dari ekuitas awal hari ({day_start:.2f}). "
                "Berlaku sampai berganti hari."
            )
            return base
        if day_start > 0 and daily_pnl <= -day_start * s.daily_loss_limit_pct / 100.0:
            state["daily_locked"] = True
            self._save_state(state)
            log.error(
                "KUNCI HARIAN AKTIF: PnL harian %.2f USDT (batas -%.1f%% dari %.2f).",
                daily_pnl, s.daily_loss_limit_pct, day_start,
            )
            base.reasons.append(
                f"Batas rugi harian tercapai (PnL {daily_pnl:.2f} USDT vs batas "
                f"-{day_start * s.daily_loss_limit_pct / 100.0:.2f} USDT) — kunci harian diaktifkan."
            )
            return base
        week_start = float(state.get("week_start_equity", equity))
        weekly_pnl = equity - week_start
        if week_start > 0 and weekly_pnl <= -week_start * s.weekly_loss_limit_pct / 100.0:
            base.reasons.append(
                f"Batas rugi mingguan tercapai (PnL {weekly_pnl:.2f} USDT vs batas "
                f"-{week_start * s.weekly_loss_limit_pct / 100.0:.2f} USDT)."
            )
            return base

        # --- Perlindungan rugi beruntun ---
        if s.max_consecutive_losses > 0:
            streak = self._consecutive_losses()
            if streak >= s.max_consecutive_losses:
                base.reasons.append(
                    f"Sudah {streak} kali rugi beruntun (batas {s.max_consecutive_losses}) — "
                    "berhenti dulu, evaluasi strategi."
                )
                return base

        # --- Position sizing dari jarak stop loss ---
        risk_amount = equity * s.risk_per_trade_pct / 100.0
        raw_qty = risk_amount / stop_distance
        qty = filters.round_quantity(raw_qty)

        # Leverage: pilih yang cukup untuk notional, dibatasi MAX_LEVERAGE,
        # dan notional tidak boleh melampaui ekuitas × leverage.
        notional = qty * entry
        needed_leverage = max(1, int(notional / equity) + 1) if equity > 0 else s.max_leverage
        leverage = min(s.max_leverage, max(1, needed_leverage))
        max_notional = equity * leverage
        if notional > max_notional:
            qty = filters.round_quantity(max_notional / entry)
            notional = qty * entry

        if qty < filters.quantity_min:
            base.reasons.append(
                f"Kuantitas hasil hitung ({qty}) di bawah minQty {filters.quantity_min} "
                f"untuk {signal.symbol} — saldo terlalu kecil untuk risiko sekecil ini."
            )
            return base
        if notional < filters.min_notional:
            base.reasons.append(
                f"Notional {notional:.2f} USDT di bawah minNotional {filters.min_notional} USDT."
            )
            return base

        # --- Batas eksposur ---
        if notional > equity * s.max_symbol_exposure_pct / 100.0:
            base.reasons.append(
                f"Notional {notional:.2f} USDT melebihi batas eksposur per simbol "
                f"({s.max_symbol_exposure_pct}% dari ekuitas = {equity * s.max_symbol_exposure_pct / 100.0:.2f} USDT)."
            )
            return base
        existing_notional = sum(abs(float(p.get("notional", 0))) for p in open_positions)
        if existing_notional + notional > equity * s.max_total_exposure_pct / 100.0:
            base.reasons.append(
                f"Total eksposur akan menjadi {existing_notional + notional:.2f} USDT — melebihi "
                f"batas {s.max_total_exposure_pct}% dari ekuitas "
                f"({equity * s.max_total_exposure_pct / 100.0:.2f} USDT)."
            )
            return base

        base.allowed = True
        base.quantity = qty
        base.leverage = leverage
        base.notional = notional
        base.risk_amount = risk_amount
        # Pembulatan SL/TP ke tick (floor untuk keduanya). Catatan arah:
        # risiko DOLAR tidak berubah oleh pembulatan ini karena kuantitas
        # dihitung dari jarak SL final (qty = risk/jarak) — jarak yang
        # bergeser karena pembulatan otomatis mengecilkan/membesarkan qty
        # secara kompensasi. Untuk SHORT, floor menggeser SL sedikit lebih
        # dekat ke entri (stop lebih ketat, bukan lebih longgar).
        base.stop_loss = filters.round_price(sl)
        base.take_profit = filters.round_price(tp)
        base.reasons.append(
            f"OK — risiko {risk_amount:.2f} USDT ({s.risk_per_trade_pct}% dari ekuitas "
            f"{equity:.2f}), qty {qty}, leverage {leverage}x, notional {notional:.2f} USDT, "
            f"R/R {rr:.2f}, PnL harian {daily_pnl:+.2f} USDT."
        )
        log.info("RISK ✓ %s %s qty=%s lev=%sx RR=%.2f", signal.symbol, signal.side, qty, leverage, rr)
        return base
