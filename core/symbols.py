"""Penemuan simbol & aturan presisi dari `exchangeInfo` Binance.

Daftar simbol TIDAK pernah di-hardcode: selalu dibaca dari Binance saat
runtime sehingga semua perpetual yang aktif — termasuk yang baru listing —
otomatis terdeteksi. Modul ini juga menyediakan filter presisi resmi
(stepSize, tickSize, minNotional) dan pembulatan yang sesuai aturan bursa,
karena order dengan presisi salah akan ditolak Binance.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from config.settings import Settings
from core.binance_client import BinanceFuturesClient
from utils.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class SymbolFilters:
    symbol: str
    quantity_step: float  # LOT_SIZE.stepSize
    quantity_min: float   # LOT_SIZE.minQty
    quantity_max: float   # LOT_SIZE.maxQty
    price_tick: float     # PRICE_FILTER.tickSize
    min_notional: float   # MIN_NOTIONAL.notional

    def round_quantity(self, qty: float) -> float:
        if self.quantity_step <= 0:
            return qty
        steps = math.floor(qty / self.quantity_step)
        return round(steps * self.quantity_step, 10)

    def round_price(self, price: float) -> float:
        if self.price_tick <= 0:
            return price
        steps = math.floor(price / self.price_tick)
        return round(steps * self.price_tick, 10)


class SymbolManager:
    def __init__(self, client: BinanceFuturesClient, settings: Settings, cache_ttl: int = 900):
        self._client = client
        self._settings = settings
        self._cache_ttl = cache_ttl
        self._filters: dict[str, SymbolFilters] = {}
        self._loaded_at = 0.0

    def _ensure_loaded(self) -> None:
        if self._filters and (time.time() - self._loaded_at) < self._cache_ttl:
            return
        info = self._client.exchange_info()
        quote = self._settings.quote_asset
        loaded: dict[str, SymbolFilters] = {}
        for entry in info.get("symbols", []):
            if (
                entry.get("status") == "TRADING"
                and entry.get("contractType") == "PERPETUAL"
                and entry.get("quoteAsset") == quote
            ):
                f = {flt["filterType"]: flt for flt in entry.get("filters", [])}
                lot = f.get("LOT_SIZE", {})
                price_f = f.get("PRICE_FILTER", {})
                notional = f.get("MIN_NOTIONAL", {})
                loaded[entry["symbol"]] = SymbolFilters(
                    symbol=entry["symbol"],
                    quantity_step=float(lot.get("stepSize", 0)),
                    quantity_min=float(lot.get("minQty", 0)),
                    quantity_max=float(lot.get("maxQty", 0)),
                    price_tick=float(price_f.get("tickSize", 0)),
                    min_notional=float(notional.get("notional", 0)),
                )
        self._filters = loaded
        self._loaded_at = time.time()
        log.info("Dimuat %d simbol perpetual %s yang aktif", len(loaded), quote)

    def all_symbols(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._filters.keys())

    def filters(self, symbol: str) -> SymbolFilters:
        self._ensure_loaded()
        symbol = symbol.upper()
        if symbol not in self._filters:
            raise KeyError(f"Simbol tidak dikenal / tidak aktif: {symbol}")
        return self._filters[symbol]

    def is_tradable(self, symbol: str) -> bool:
        self._ensure_loaded()
        return symbol.upper() in self._filters
