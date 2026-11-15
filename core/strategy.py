"""Mesin sinyal: skor teknikal lokal + konfirmasi AI.

Dua penilai independen menilai snapshot yang sama:

1. Skor teknikal (deterministik, aturan transparan di `technical_score`).
2. Penilaian AI BangunWeb (JSON terstruktur dari `ai_client`).

Keduanya digabung menjadi satu `TradeSignal`. Bila keduanya tidak sepakat,
sinyal diturunkan menjadi NEUTRAL — ketidaksepakatan tidak pernah
dipertaruhkan dengan uang.
"""

from __future__ import annotations

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


def technical_score(snapshot: dict) -> tuple[int, list[str]]:
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

    def snapshot_for(self, symbol: str, interval: Optional[str] = None) -> dict:
        interval = interval or self._settings.default_interval
        klines = self._binance.klines(symbol, interval=interval, limit=200)
        ticker = self._binance.ticker_24h(symbol)
        funding = self._binance.funding_rate(symbol)
        try:
            oi = self._binance.open_interest(symbol)
        except Exception as exc:  # OI bersifat pelengkap; kegagalannya tidak fatal
            log.debug("Open interest %s tidak tersedia: %s", symbol, exc)
            oi = None
        return build_snapshot(symbol, klines, ticker, funding, open_interest=oi)

    def analyze(self, symbol: str, interval: Optional[str] = None, use_ai: bool = True) -> TradeSignal:
        symbol = symbol.upper()
        snapshot = self.snapshot_for(symbol, interval)
        score, reasons = technical_score(snapshot)
        price = float(snapshot["price"])

        tech_side = "NEUTRAL"
        if score >= MIN_TECH_SCORE_FOR_TRADE:
            tech_side = "LONG"
        elif score <= -MIN_TECH_SCORE_FOR_TRADE:
            tech_side = "SHORT"

        ai_result: Optional[AIAnalysis] = None
        if use_ai and self._ai is not None and self._ai.is_configured():
            try:
                ai_result = self._ai.analyze_market(symbol, snapshot)
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
            elif tech_side == "NEUTRAL" and ai_result.signal != "NEUTRAL" and ai_result.confidence >= 75:
                side = ai_result.signal
                confidence = int(ai_result.confidence * 0.7)
                reasons.append(f"Teknikal netral; AI sangat yakin ({ai_result.confidence}) — diambil dengan bobot tereduksi")
            else:
                side = "NEUTRAL"
                confidence = int(min(abs(score), ai_result.confidence) * 0.5)
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
            if (
                ai_result is not None
                and ai_result.stop_loss
                and ai_result.take_profit
                and ((side == "LONG" and ai_result.stop_loss < price < ai_result.take_profit)
                     or (side == "SHORT" and ai_result.stop_loss > price > ai_result.take_profit))
            ):
                stop_loss, take_profit = ai_result.stop_loss, ai_result.take_profit
                reasons.append("Level SL/TP dari AI (lolos validasi arah)")
            else:
                stop_loss, take_profit = _atr_levels(price, snapshot.get("atr14"), side)
                reasons.append("Level SL/TP dari ATR (1.5× / 2.5×)")

        return TradeSignal(
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
        )

    def scan(
        self,
        symbols: list[str],
        interval: Optional[str] = None,
        use_ai: bool = False,
        min_abs_score: int = 1,
    ) -> list[TradeSignal]:
        """Nilai banyak simbol; AI hanya untuk kandidat teratas agar hemat token.

        Dua tahap: (1) skor teknikal untuk semua simbol (gratis),
        (2) bila use_ai, lima kandidat teratas dinilai AI.
        """
        staged: list[TradeSignal] = []
        for symbol in symbols:
            try:
                snapshot = self.snapshot_for(symbol, interval)
            except Exception as exc:
                log.debug("Lewati %s saat scan: %s", symbol, exc)
                continue
            score, reasons = technical_score(snapshot)
            if abs(score) < min_abs_score:
                continue
            side = "LONG" if score > 0 else ("SHORT" if score < 0 else "NEUTRAL")
            sl, tp = _atr_levels(float(snapshot["price"]), snapshot.get("atr14"), side)
            staged.append(
                TradeSignal(
                    symbol=symbol,
                    side=side,
                    confidence=abs(score),
                    price=float(snapshot["price"]),
                    stop_loss=sl,
                    take_profit=tp,
                    technical_score=score,
                    snapshot=snapshot,
                    reasons=reasons,
                )
            )
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
