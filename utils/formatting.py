"""Fungsi bantu format tampilan: angka, persen, dan harga adaptif."""

from __future__ import annotations


def fmt_price(value: float) -> str:
    """Format harga dengan jumlah desimal yang masuk akal per besarannya."""
    if value == 0:
        return "0"
    abs_v = abs(value)
    if abs_v >= 1000:
        return f"{value:,.2f}"
    if abs_v >= 1:
        return f"{value:,.4f}"
    if abs_v >= 0.01:
        return f"{value:,.6f}"
    return f"{value:,.8f}"


def fmt_usd(value: float) -> str:
    return f"{value:,.2f} USDT"


def fmt_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def fmt_qty(value: float) -> str:
    return f"{value:,.6f}".rstrip("0").rstrip(".") or "0"


def trend_arrow(pct_change: float) -> str:
    if pct_change > 0:
        return "▲"
    if pct_change < 0:
        return "▼"
    return "■"
