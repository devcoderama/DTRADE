"""Mesin sinyal: skor teknikal lokal + konfirmasi AI.

Dua penilai independen menilai snapshot yang sama:

1. Skor teknikal (deterministik, aturan transparan di `technical_score`).
2. Penilaian AI BangunWeb (JSON terstruktur dari `ai_client`).

Keduanya digabung menjadi satu `TradeSignal`. Bila keduanya tidak sepakat,
sinyal diturunkan menjadi NEUTRAL — ketidaksepakatan tidak pernah
dipertaruhkan dengan uang.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from config.settings import Settings
from core.ai_client import AIAnalysis
from core.ai_providers import BaseAIProvider
from core.binance_client import BinanceFuturesClient
from core.indicators import build_snapshot
from utils.logger import get_logger

log = get_logger(__name__)

# Ambang keputusan
MIN_TECH_SCORE_FOR_TRADE = 25      # |skor| minimum dari penilai teknikal
MIN_AI_CONFIDENCE = 60             # confidence AI minimum untuk konfirmasi
MIN_FINAL_CONFIDENCE = 55          # confidence gabungan minimum untuk sinyal


@dataclass
class TradeSignal:
    symbol: str
    side: str  # LONG | SHORT | NEUTRAL
    confidence: int  # 0..100 (gabungan)
    price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    technical_score: int  # -100..+100
    snapshot: dict
    ai: Optional[AIAnalysis] = None
    reasons: list[str] = field(default_factory=list)
    regime: Optional[str] = None  # rezim pasar timeframe utama (classify_regime)
    mtf: dict = field(default_factory=dict)  # tren per timeframe {interval: {trend, regime}}


def technical_score(snapshot: dict, version: int = 1) -> tuple[int, list[str]]:
    """Skor teknikal: v1 (lama, dipertahankan) atau v2 (per kategori)."""
    if version == 2:
        return technical_score_v2(snapshot)
    return technical_score_v1(snapshot)


def technical_components(snapshot: dict) -> dict[str, tuple[int, list[str]]]:
    """Komponen skor v2 per kategori: trend / mean_reversion / positioning.

    Setiap kategori dibatasi kontribusi maksimumnya agar satu keluarga
    indikator tidak mendominasi (double counting). Mean-reversion hanya
    dinilai pada rezim sideways; komponen trend hanya pada rezim trending.
    """
    regime = snapshot.get("regime") or "uncertain"
    trend_pts = 0
    trend_reasons: list[str] = []
    trend = snapshot.get("trend")
    if trend == "UPTREND":
        trend_pts += 25
        trend_reasons.append("Tren naik +25")
    elif trend == "DOWNTREND":
        trend_pts -= 25
        trend_reasons.append("Tren turun -25")
    hist = snapshot.get("macd_histogram")
    macd_v = snapshot.get("macd")
    if hist is not None and macd_v is not None:
        if hist > 0 and macd_v > 0:
            trend_pts += 15
            trend_reasons.append("MACD bullish +15")
        elif hist < 0 and macd_v < 0:
            trend_pts -= 15
            trend_reasons.append("MACD bearish -15")
        elif hist > 0:
            trend_pts += 6
            trend_reasons.append("Momentum MACD membaik +6")
        elif hist < 0:
            trend_pts -= 6
            trend_reasons.append("Momentum MACD melemah -6")
    change_1h = snapshot.get("change_1h_pct")
    if change_1h is not None:
        if change_1h > 1.0:
            trend_pts += 8
            trend_reasons.append(f"Momentum 1 jam kuat ({change_1h:+.2f}%) +8")
        elif change_1h < -1.0:
            trend_pts -= 8
            trend_reasons.append(f"Momentum 1 jam lemah ({change_1h:+.2f}%) -8")
    if regime not in {"bullish_trending", "bearish_trending", "breakout", "uncertain"}:
        # Di luar rezim trending, komponen trend dilemahkan separuh —
        # sinyal tren pada pasar sideways adalah sumber whipsaw utama.
        trend_pts = int(round(trend_pts * 0.5))
        if trend_pts:
            trend_reasons.append(f"Komponen trend dilemahkan (rezim {regime})")

    mr_pts = 0
    mr_reasons: list[str] = []
    if regime == "sideways":
        rsi = snapshot.get("rsi14")
        if rsi is not None:
            if rsi < 30:
                mr_pts += 15
                mr_reasons.append(f"RSI oversold ({rsi:.0f}) +15")
            elif rsi > 70:
                mr_pts -= 15
                mr_reasons.append(f"RSI overbought ({rsi:.0f}) -15")
        pct_b = snapshot.get("bb_pct_b")
        if pct_b is not None:
            if pct_b < 0.05:
                mr_pts += 10
                mr_reasons.append("Harga di tepi bawah Bollinger +10")
            elif pct_b > 0.95:
                mr_pts -= 10
                mr_reasons.append("Harga di tepi atas Bollinger -10")
    elif snapshot.get("rsi14") is not None:
        # Momentum RSI sehat tetap bernilai kecil di luar sideways.
        rsi = snapshot["rsi14"]
        if 50 <= rsi < 65:
            mr_pts += 5
            mr_reasons.append(f"RSI sehat untuk long ({rsi:.0f}) +5")
        elif 35 < rsi < 50:
            mr_pts -= 5
            mr_reasons.append(f"RSI lemah ({rsi:.0f}) -5")

    pos_pts = 0
    pos_reasons: list[str] = []
    funding = snapshot.get("funding_rate") or 0.0
    if funding >= 0.001:
        pos_pts -= 10
        pos_reasons.append(f"Funding ekstrem positif ({funding * 100:.3f}%) -10")
    elif funding > 0.0005:
        pos_pts -= 5
        pos_reasons.append("Funding sangat positif -5")
    elif funding <= -0.001:
        pos_pts += 10
        pos_reasons.append(f"Funding ekstrem negatif ({funding * 100:.3f}%) +10")
    elif funding < -0.0005:
        pos_pts += 5
        pos_reasons.append("Funding sangat negatif +5")
    volume = snapshot.get("last_volume")
    avg_volume = snapshot.get("avg_volume")
    if volume and avg_volume:
        if volume > 1.5 * avg_volume:
            pos_pts += 5
            pos_reasons.append("Volume di atas rata-rata (partisipasi kuat) +5")

    def _cap(v: int, cap: int) -> int:
        return max(-cap, min(cap, v))

    return {
        "trend": (_cap(trend_pts, 40), trend_reasons),
        "mean_reversion": (_cap(mr_pts, 25), mr_reasons),
        "positioning": (_cap(pos_pts, 20), pos_reasons),
    }


def technical_score_v2(snapshot: dict) -> tuple[int, list[str]]:
    """Skor v2: jumlah kategori terbatas + syarat minimal 2 kategori sepakat.

    Satu indikator/kategori saja tidak boleh melewati ambang entri:
    bila kurang dari 2 kategori searah dengan total, skor dibatasi
    maksimal 24 (di bawah ambang trade 25).
    """
    components = technical_components(snapshot)
    total = sum(v for v, _ in components.values())
    reasons: list[str] = []
    for name, (value, rs) in components.items():
        if value:
            reasons.append(f"[{name} {value:+d}] " + "; ".join(rs))
    total = max(-100, min(100, total))
    if total != 0:
        agreeing = sum(
            1 for v, _ in components.values() if abs(v) >= 8 and (v > 0) == (total > 0)
        )
        if agreeing < 2:
            capped = max(-24, min(24, total))
            reasons.append(
                f"Skor v2 dibatasi {total} → {capped}: hanya {agreeing} kategori sepakat "
                "(syarat minimal 2 kategori)"
            )
            total = capped
    return total, reasons


def technical_score_v1(snapshot: dict) -> tuple[int, list[str]]:
    """Skor -100..+100 dari snapshot indikator, beserta alasan per komponen."""
    score = 0
    reasons: list[str] = []

    trend = snapshot.get("trend")
    if trend == "UPTREND":
        score += 30
        reasons.append("Tren naik (harga > EMA20 > EMA50) +30")
    elif trend == "DOWNTREND":
        score -= 30
        reasons.append("Tren turun (harga < EMA20 < EMA50) -30")

    rsi = snapshot.get("rsi14")
    if rsi is not None:
        if rsi < 30:
            score += 15
            reasons.append(f"RSI oversold ({rsi:.0f}) +15")
        elif rsi > 70:
            score -= 15
            reasons.append(f"RSI overbought ({rsi:.0f}) -15")
        elif 50 <= rsi < 65:
            score += 8
            reasons.append(f"RSI sehat untuk long ({rsi:.0f}) +8")
        elif 35 < rsi < 50:
            score -= 8
            reasons.append(f"RSI lemah ({rsi:.0f}) -8")

    hist = snapshot.get("macd_histogram")
    macd_v = snapshot.get("macd")
    if hist is not None and macd_v is not None:
        if hist > 0 and macd_v > 0:
            score += 20
            reasons.append("MACD bullish (histogram & garis positif) +20")
        elif hist < 0 and macd_v < 0:
            score -= 20
            reasons.append("MACD bearish (histogram & garis negatif) -20")
        elif hist > 0:
            score += 8
            reasons.append("Momentum MACD membaik +8")
        elif hist < 0:
            score -= 8
            reasons.append("Momentum MACD melemah -8")

    pct_b = snapshot.get("bb_pct_b")
    if pct_b is not None:
        if pct_b < 0.05:
            score += 10
            reasons.append("Harga di tepi bawah Bollinger (potensi pantul) +10")
        elif pct_b > 0.95:
            score -= 10
            reasons.append("Harga di tepi atas Bollinger (rawan koreksi) -10")

    change_1h = snapshot.get("change_1h_pct")
    if change_1h is not None:
        if change_1h > 1.0:
            score += 10
            reasons.append(f"Momentum 1 jam kuat ({change_1h:+.2f}%) +10")
        elif change_1h < -1.0:
            score -= 10
            reasons.append(f"Momentum 1 jam lemah ({change_1h:+.2f}%) -10")

    funding = snapshot.get("funding_rate") or 0.0
    if funding >= 0.001:
        score -= 10
        reasons.append(f"Funding ekstrem positif ({funding * 100:.3f}%) — long sangat jenuh -10")
    elif funding > 0.0005:
        score -= 5
        reasons.append("Funding sangat positif (long jenuh) -5")
    elif funding <= -0.001:
        score += 10
        reasons.append(f"Funding ekstrem negatif ({funding * 100:.3f}%) — short sangat jenuh +10")
    elif funding < -0.0005:
        score += 5
        reasons.append("Funding sangat negatif (short jenuh) +5")

    return max(-100, min(100, score)), reasons


def _atr_levels(price: float, atr_value: Optional[float], side: str) -> tuple[Optional[float], Optional[float]]:
    """SL = 1.5×ATR, TP = 2.5×ATR dari harga acuan (rasio R:R ≈ 1 : 1.67)."""
    if not atr_value or price <= 0:
        return None, None
    if side == "LONG":
        return price - 1.5 * atr_value, price + 2.5 * atr_value
    if side == "SHORT":
        return price + 1.5 * atr_value, price - 2.5 * atr_value
    return None, None


class SignalEngine:
    def __init__(
        self,
        settings: Settings,
        binance: BinanceFuturesClient,
        ai: Optional[BaseAIProvider] = None,
    ):
        self._settings = settings
        self._binance = binance
        self._ai = ai
        # Cache hasil analisis terakhir per simbol — dibagi Web UI, siaran
        # Telegram, dan CLI agar sinyal yang sama tampil konsisten di semua
        # permukaan (AI adalah model bahasa: dua panggilan terpisah bisa
        # memberi confidence berbeda di sekitar ambang batas).
        self._analysis_cache: dict[tuple, tuple[float, TradeSignal]] = {}
        self._cache_lock = threading.Lock()

    def set_ai(self, ai) -> None:
        """Ganti provider AI saat runtime (Web UI / CLI) tanpa restart."""
        self._ai = ai

    @property
    def ai_name(self) -> Optional[str]:
        """Nama provider AI aktif (untuk pencatatan jurnal)."""
        return getattr(self._ai, "name", None) if self._ai is not None else None

    def _closed_only(self, klines: list) -> tuple[list, bool]:
        """Buang candle terakhir yang belum tertutup (kebijakan Fase 3.1).

        Mengembalikan (klines_untuk_indikator, dibuang?). Harga referensi
        entri TIDAK ikut dibuang — pemanggil menggantinya dengan mark
        price terkini. Backtest tidak memakai jalur ini (data historis
        sudah tertutup semua).
        """
        if not getattr(self._settings, "use_closed_candles_only", True):
            return klines, False
        if klines and len(klines) > 50 and len(klines[-1]) > 6:
            import time as _time

            if int(klines[-1][6]) > int(_time.time() * 1000):
                return klines[:-1], True
        return klines, False

    def snapshot_for(self, symbol: str, interval: Optional[str] = None) -> dict:
        interval = interval or self._settings.default_interval
        # 300 candle agar EMA200 terhitung dari data yang cukup (bukan pas-pasan).
        klines = self._binance.klines(symbol, interval=interval, limit=300)
        ticker = self._binance.ticker_24h(symbol)
        funding = self._binance.funding_rate(symbol)
        try:
            oi = self._binance.open_interest(symbol)
        except Exception as exc:  # OI bersifat pelengkap; kegagalannya tidak fatal
            log.debug("Open interest %s tidak tersedia: %s", symbol, exc)
            oi = None
        klines, dropped = self._closed_only(klines)
        snapshot = build_snapshot(symbol, klines, ticker, funding, open_interest=oi, interval=interval)
        if dropped:
            # Indikator dari candle tertutup; harga acuan tetap harga kini.
            mark = float(funding.get("markPrice") or 0)
            if mark > 0:
                snapshot["price"] = mark
        return snapshot

    def analyze(
        self,
        symbol: str,
        interval: Optional[str] = None,
        use_ai: bool = True,
        fresh: bool = False,
    ) -> TradeSignal:
        """Analisis satu simbol.

        Hasil analisis di-cache selama `analysis_cache_ttl_sec` detik
        (bawaan 180) agar semua permukaan — siaran Telegram, Web UI, CLI —
        memakai sinyal yang sama untuk momen yang sama (dan hemat token AI).

        Dua jaminan untuk jalur eksekusi:
        - Yang dikembalikan SELALU salinan (deepcopy): pemanggil boleh
          memutasi hasilnya tanpa meracuni cache.
        - `fresh=True` melewati cache sepenuhnya — WAJIB untuk eksekusi,
          agar keputusan trading tidak memakai harga basi.
        """
        import copy

        symbol = symbol.upper()
        interval = interval or self._settings.default_interval
        ttl = max(0, int(getattr(self._settings, "analysis_cache_ttl_sec", 180)))
        ai_key = ""
        if use_ai and self._ai is not None:
            ai_key = f"{getattr(self._ai, 'name', '?')}:{getattr(self._ai, 'model', '?')}"
        key = (symbol, interval, use_ai, ai_key)
        if ttl > 0 and not fresh:
            with self._cache_lock:
                hit = self._analysis_cache.get(key)
                if hit is not None and time.time() - hit[0] < ttl:
                    return copy.deepcopy(hit[1])
        result = self._analyze_fresh(symbol, interval, use_ai=use_ai)
        if ttl > 0:
            with self._cache_lock:
                # Sapu entri kedaluwarsa + batasi ukuran (buang terlama)
                # agar cache tidak tumbuh tanpa batas pada pemindaian massal.
                now = time.time()
                expired = [k for k, (ts, _) in self._analysis_cache.items() if now - ts >= ttl]
                for k in expired:
                    self._analysis_cache.pop(k, None)
                if len(self._analysis_cache) >= 512:
                    oldest = sorted(self._analysis_cache, key=lambda k: self._analysis_cache[k][0])
                    for k in oldest[: len(self._analysis_cache) - 511]:
                        self._analysis_cache.pop(k, None)
                self._analysis_cache[key] = (now, result)
        return copy.deepcopy(result)

    def _analyze_fresh(self, symbol: str, interval: Optional[str] = None, use_ai: bool = True) -> TradeSignal:
        symbol = symbol.upper()
        snapshot = self.snapshot_for(symbol, interval)
        score, reasons = technical_score(snapshot, version=int(getattr(self._settings, "scoring_version", 1)))
        price = float(snapshot["price"])

        tech_side = "NEUTRAL"
        if score >= MIN_TECH_SCORE_FOR_TRADE:
            tech_side = "LONG"
        elif score <= -MIN_TECH_SCORE_FOR_TRADE:
            tech_side = "SHORT"

        # Konteks MTF + BTC dihitung SEBELUM AI agar ikut memperkaya prompt.
        mtf: dict = {}
        if self._settings.mtf_enabled:
            mtf = self._multi_timeframe(symbol, interval or self._settings.default_interval)
        btc_ctx = self._btc_context(symbol) if use_ai else None

        ai_result: Optional[AIAnalysis] = None
        if use_ai and self._ai is not None and self._ai.is_configured():
            try:
                ai_snapshot = dict(snapshot)
                if mtf:
                    ai_snapshot["mtf"] = mtf
                if btc_ctx:
                    ai_snapshot["btc_context"] = btc_ctx
                ai_result = self._ai.analyze_market(symbol, ai_snapshot)
            except Exception as exc:  # AI bersifat penasihat; kegagalannya tidak fatal
                log.warning("Analisis AI gagal untuk %s: %s", symbol, exc)

        # --- Penggabungan keputusan ---
        if ai_result is not None:
            if ai_result.signal == tech_side and tech_side != "NEUTRAL" and ai_result.confidence >= MIN_AI_CONFIDENCE:
                side = tech_side
                confidence = int(round((abs(score) + ai_result.confidence) / 2))
                reasons.append(
                    f"AI sepakat ({ai_result.signal}, confidence {ai_result.confidence}) — {ai_result.reasoning}"
                )
            elif (
                tech_side == "NEUTRAL" and ai_result.signal != "NEUTRAL"
                and ai_result.confidence >= 75
                and getattr(self._settings, "ai_can_initiate", False)
            ):
                side = ai_result.signal
                confidence = int(ai_result.confidence * 0.7)
                reasons.append(f"Teknikal netral; AI sangat yakin ({ai_result.confidence}) — diambil dengan bobot tereduksi")
            else:
                side = "NEUTRAL"
                confidence = int(min(abs(score), ai_result.confidence) * 0.5)
                if (
                    tech_side == "NEUTRAL" and ai_result.signal != "NEUTRAL"
                    and ai_result.confidence >= 75
                    and not getattr(self._settings, "ai_can_initiate", False)
                ):
                    reasons.append(
                        f"AI sangat yakin ({ai_result.signal}, {ai_result.confidence}) tetapi teknikal "
                        "NEUTRAL dan AI_CAN_INITIATE=false — AI hanya boleh mengonfirmasi/memveto → NEUTRAL"
                    )
                elif ai_result.signal == tech_side and tech_side != "NEUTRAL":
                    reasons.append(
                        f"AI searah ({ai_result.signal}) tetapi confidence {ai_result.confidence} "
                        f"di bawah minimum konfirmasi {MIN_AI_CONFIDENCE} → NEUTRAL"
                    )
                elif tech_side == "NEUTRAL" and ai_result.signal != "NEUTRAL":
                    reasons.append(
                        f"Teknikal NEUTRAL; AI ({ai_result.signal}, confidence {ai_result.confidence}) "
                        "belum cukup yakin untuk mengambil alih (perlu ≥ 75) → NEUTRAL"
                    )
                else:
                    reasons.append(
                        f"Teknikal ({tech_side}) dan AI ({ai_result.signal}) tidak sepakat → NEUTRAL"
                    )
        else:
            side = tech_side
            confidence = abs(score)
            if ai_result is None and use_ai:
                reasons.append("Tanpa konfirmasi AI (kunci tidak ada / AI gagal) — hanya skor teknikal")

        if confidence < MIN_FINAL_CONFIDENCE:
            if side != "NEUTRAL":
                reasons.append(f"Confidence gabungan {confidence} < {MIN_FINAL_CONFIDENCE} → NEUTRAL")
            side = "NEUTRAL"

        # --- Level SL/TP: utamakan usulan AI yang valid, jika tidak pakai ATR ---
        stop_loss = take_profit = None
        if side != "NEUTRAL":
            atr_v = snapshot.get("atr14")
            ai_levels_ok = (
                ai_result is not None
                and ai_result.stop_loss
                and ai_result.take_profit
                and ((side == "LONG" and ai_result.stop_loss < price < ai_result.take_profit)
                     or (side == "SHORT" and ai_result.stop_loss > price > ai_result.take_profit))
            )
            if ai_levels_ok and atr_v:
                # Validasi jarak vs ATR: SL dalam 0,5–3× ATR, TP ≤ 5× ATR.
                sl_dist = abs(price - float(ai_result.stop_loss))
                tp_dist = abs(float(ai_result.take_profit) - price)
                if not (0.5 * atr_v <= sl_dist <= 3.0 * atr_v and tp_dist <= 5.0 * atr_v):
                    ai_levels_ok = False
                    reasons.append(
                        f"Level AI di luar rentang ATR (SL {sl_dist / atr_v:.2f}×ATR, "
                        f"TP {tp_dist / atr_v:.2f}×ATR) — diganti level ATR"
                    )
            if ai_levels_ok:
                stop_loss, take_profit = ai_result.stop_loss, ai_result.take_profit
                reasons.append("Level SL/TP dari AI (lolos validasi arah & rentang ATR)")
            else:
                stop_loss, take_profit = _atr_levels(price, snapshot.get("atr14"), side)
                reasons.append("Level SL/TP dari ATR (1.5× / 2.5×)")

        signal = TradeSignal(
            symbol=symbol,
            side=side,
            confidence=confidence if side != "NEUTRAL" else 0,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            technical_score=score,
            snapshot=snapshot,
            ai=ai_result,
            reasons=reasons,
            regime=snapshot.get("regime"),
        )

        # --- Konteks multi-timeframe (sudah dihitung sebelum AI) ---
        if self._settings.mtf_enabled:
            signal.mtf = mtf
            if signal.mtf and side != "NEUTRAL":
                want = "UPTREND" if side == "LONG" else "DOWNTREND"
                aligned = [tf for tf, info in signal.mtf.items() if info.get("trend") == want]
                opposed = [
                    tf for tf, info in signal.mtf.items()
                    if info.get("trend") in {"UPTREND", "DOWNTREND"} and info.get("trend") != want
                ]
                if aligned:
                    reasons.append(f"MTF selaras: {', '.join(aligned)} searah {side}")
                if opposed:
                    reasons.append(f"MTF berlawanan: {', '.join(opposed)} melawan {side} — hati-hati")

        # --- Gerbang keras Fase 3.2: MTF berlawanan / rezim / ADX ---
        if signal.side != "NEUTRAL":
            blocks = self._gate_blocks(snapshot, signal.side, signal.mtf)
            if blocks:
                reasons.extend(blocks)
                reasons.append("Sinyal dibatalkan gerbang keras → NEUTRAL")
                signal.side = "NEUTRAL"
                signal.confidence = 0
                signal.stop_loss = None
                signal.take_profit = None
        return signal

    def _gate_blocks(self, snapshot: dict, side: str, mtf: Optional[dict] = None) -> list[str]:
        """Gerbang keras Fase 3.2 — daftar alasan penolakan (kosong = lolos).

        - MTF: tren 1h/4h yang BERLAWANAN dengan arah sinyal menolak entri.
        - ADX: entri pengikut-tren pada rezim trending ditolak bila
          adx14 < ADX_MIN (tren terlalu lemah untuk diikuti).
        - Rezim: high_volatility / low_volatility menolak semua entri arah.
        """
        blocks: list[str] = []
        if side == "NEUTRAL":
            return blocks
        s = self._settings
        if getattr(s, "mtf_gate_enabled", True) and mtf:
            want_against = "DOWNTREND" if side == "LONG" else "UPTREND"
            for tf in ("1h", "4h"):
                info = mtf.get(tf)
                if info and info.get("trend") == want_against:
                    blocks.append(
                        f"Gerbang MTF: tren {tf} {want_against} melawan sinyal {side} — entri ditolak"
                    )
        if getattr(s, "regime_gate_enabled", True):
            regime = snapshot.get("regime")
            if regime in {"high_volatility", "low_volatility"}:
                blocks.append(f"Gerbang rezim: {regime} — entri arah ditolak")
            adx = snapshot.get("adx14")
            if (
                regime in {"bullish_trending", "bearish_trending"}
                and adx is not None
                and adx < float(getattr(s, "adx_min", 20.0))
            ):
                blocks.append(
                    f"Gerbang ADX: adx14 {adx:.1f} < ADX_MIN {float(getattr(s, 'adx_min', 20.0)):.0f} "
                    "pada rezim trending — tren terlalu lemah"
                )
        return blocks

    def _btc_context(self, symbol: str) -> Optional[dict]:
        """Konteks tren BTC (cache 60 dtk) untuk memperkaya penilaian AI."""
        if symbol.upper() == "BTCUSDT" or self._binance is None:
            return None
        import time as _time

        now = _time.time()
        cached = getattr(self, "_btc_ctx_cache", None)
        if cached and now - cached[0] < 60:
            return cached[1]
        try:
            snap = self.snapshot_for("BTCUSDT", "1h")
            ctx = {
                "trend_1h": snap.get("trend"),
                "regime_1h": snap.get("regime"),
                "change_24h_pct": snap.get("change_24h_pct"),
            }
        except Exception as exc:
            log.debug("Konteks BTC tidak tersedia: %s", exc)
            return None
        self._btc_ctx_cache = (now, ctx)
        return ctx

    def _multi_timeframe(self, symbol: str, primary_interval: str) -> dict:
        """Tren + rezim timeframe yang lebih tinggi dari timeframe utama."""
        out: dict = {}
        for tf in ("1h", "4h", "1d"):
            if tf == primary_interval:
                continue
            try:
                snap = self.snapshot_for(symbol, tf)
            except Exception as exc:  # konteks tambahan; kegagalan satu TF tidak fatal
                log.debug("Snapshot MTF %s %s gagal: %s", symbol, tf, exc)
                continue
            out[tf] = {"trend": snap.get("trend"), "regime": snap.get("regime")}
        return out

    def snapshot_light(
        self, symbol: str, interval: Optional[str], ticker: dict, funding: dict
    ) -> dict:
        """Snapshot hemat untuk pemindaian: hanya klines yang diambil per simbol;
        ticker & funding memakai hasil panggilan massal, open interest dilewati."""
        interval = interval or self._settings.default_interval
        klines = self._binance.klines(symbol, interval=interval, limit=200)
        klines, dropped = self._closed_only(klines)
        snapshot = build_snapshot(symbol, klines, ticker, funding, open_interest=None, interval=interval)
        if dropped:
            mark = float(funding.get("markPrice") or 0)
            if mark > 0:
                snapshot["price"] = mark
        return snapshot

    def _score_snapshot(self, snapshot: dict, min_abs_score: int) -> Optional[TradeSignal]:
        """Ubah snapshot menjadi TradeSignal teknikal (None bila skor terlalu kecil)."""
        score, reasons = technical_score(snapshot, version=int(getattr(self._settings, "scoring_version", 1)))
        if abs(score) < min_abs_score:
            return None
        side = "LONG" if score > 0 else ("SHORT" if score < 0 else "NEUTRAL")
        if side != "NEUTRAL":
            # Gerbang rezim/ADX juga berlaku di jalur pindai (tanpa MTF).
            blocks = self._gate_blocks(snapshot, side, None)
            if blocks:
                reasons.extend(blocks)
                return None
        sl, tp = _atr_levels(float(snapshot["price"]), snapshot.get("atr14"), side)
        return TradeSignal(
            symbol=str(snapshot["symbol"]),
            side=side,
            confidence=abs(score),
            price=float(snapshot["price"]),
            stop_loss=sl,
            take_profit=tp,
            technical_score=score,
            snapshot=snapshot,
            reasons=reasons,
        )

    def _scan_snapshots(
        self, symbols: list[str], interval: Optional[str], min_abs_score: int
    ) -> list[TradeSignal]:
        """Tahap 1 pemindaian: saring 150 simbol terlikuid dari data massal,
        lalu nilai klines-nya secara paralel (10 pekerja). Bila panggilan
        massal gagal, jatuh kembali ke cara lama (berurutan, snapshot penuh)
        agar pemindaian tidak pernah mati total."""
        try:
            tickers = {t["symbol"]: t for t in self._binance.tickers_24h_all()}
            funding = {f["symbol"]: f for f in self._binance.funding_all()}
            if not tickers:
                raise RuntimeError("ticker massal kosong")
        except Exception as exc:
            log.warning("Pemindaian massal tidak tersedia (%s); pakai cara berurutan.", exc)
            staged: list[TradeSignal] = []
            for symbol in symbols:
                try:
                    sig = self._score_snapshot(
                        self.snapshot_for(symbol, interval), min_abs_score
                    )
                except Exception as exc2:
                    log.debug("Lewati %s saat scan: %s", symbol, exc2)
                    continue
                if sig is not None:
                    staged.append(sig)
            return staged

        def _quote_volume(sym: str) -> float:
            try:
                return float(tickers.get(sym, {}).get("quoteVolume", 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        universe = sorted(
            (s for s in symbols if s in tickers), key=_quote_volume, reverse=True
        )[:150]

        def _work(sym: str) -> Optional[TradeSignal]:
            try:
                snap = self.snapshot_light(sym, interval, tickers[sym], funding.get(sym, {}))
                return self._score_snapshot(snap, min_abs_score)
            except Exception as exc:
                log.debug("Lewati %s saat scan: %s", sym, exc)
                return None

        with ThreadPoolExecutor(max_workers=10) as pool:
            return [s for s in pool.map(_work, universe) if s is not None]

    def scan(
        self,
        symbols: list[str],
        interval: Optional[str] = None,
        use_ai: bool = False,
        min_abs_score: int = 1,
    ) -> list[TradeSignal]:
        """Nilai banyak simbol; AI hanya untuk kandidat teratas agar hemat token.

        Dua tahap: (1) skor teknikal untuk 150 simbol terlikuid
        (data massal + klines paralel — selesai dalam hitungan detik),
        (2) bila use_ai, lima kandidat teratas dinilai AI.
        """
        staged = self._scan_snapshots(symbols, interval, min_abs_score)
        staged.sort(key=lambda s: abs(s.technical_score), reverse=True)

        if use_ai and self._ai is not None and self._ai.is_configured():
            refined: list[TradeSignal] = []
            for candidate in staged[:5]:
                refined.append(self.analyze(candidate.symbol, interval, use_ai=True))
            rest = staged[5:]
            merged = refined + rest
            merged.sort(key=lambda s: (s.side != "NEUTRAL", s.confidence), reverse=True)
            return merged
        return staged
