"""REST client untuk Binance USDⓈ-M Futures.

Satu-satunya modul yang berbicara ke `fapi.binance.com` (atau testnet).
Menyediakan dua keluarga method:

- Publik   : exchange_info, ticker, klines, funding — tanpa kunci.
- Bertanda tangan (HMAC-SHA256): akun, saldo, posisi, dan order —
  mengikuti skema keamanan resmi Binance (header `X-MBX-APIKEY`,
  parameter `signature`). Dokumentasi: https://developers.binance.com
  (USDⓈ-M Futures → General Info / Request Security).

Modul ini TIDAK mengandung keputusan trading apa pun; ia hanya
menerjemahkan panggilan Python menjadi panggilan REST yang benar.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config.settings import Settings
from utils.logger import get_logger

log = get_logger(__name__)


class BinanceAPIError(Exception):
    """Error dari Binance API, membawa kode & pesan asli bursa."""

    def __init__(self, status: int, code: Any, msg: str):
        super().__init__(f"Binance API error {status} (code={code}): {msg}")
        self.status = status
        self.code = code
        self.msg = msg


class BinanceFuturesClient:
    def __init__(self, settings: Settings, timeout: float = 15.0):
        self._settings = settings
        self._base = settings.binance_base_url
        self._timeout = timeout
        self._time_offset_ms = 0

        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
        )
        self._session = requests.Session()
        self._session.mount("https://", HTTPAdapter(max_retries=retry))
        self._session.headers.update({"Content-Type": "application/x-www-form-urlencoded"})

    # ------------------------------------------------------------------
    # Transportasi dasar
    # ------------------------------------------------------------------
    def _timestamp(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def sync_time(self) -> None:
        """Samakan jam lokal dengan server Binance untuk menghindari -1021."""
        server = self._request("GET", "/fapi/v1/time")
        if isinstance(server, dict) and "serverTime" in server:
            self._time_offset_ms = int(server["serverTime"]) - int(time.time() * 1000)

    def _sign(self, params: dict[str, Any]) -> str:
        query = urlencode(params)
        return hmac.new(
            self._settings.binance_api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        signed: bool = False,
    ) -> Any:
        params = dict(params or {})
        headers: dict[str, str] = {}
        if signed:
            if not self._settings.has_binance_keys:
                raise BinanceAPIError(
                    0, "NO_KEYS", "BINANCE_API_KEY / BINANCE_API_SECRET belum diisi di .env"
                )
            params["timestamp"] = self._timestamp()
            params["recvWindow"] = 5000
            params["signature"] = self._sign(params)
            headers["X-MBX-APIKEY"] = self._settings.binance_api_key

        url = f"{self._base}{path}"
        try:
            resp = self._session.request(
                method, url, params=params, headers=headers, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise BinanceAPIError(0, "NETWORK", str(exc)) from exc

        try:
            payload = resp.json()
        except ValueError:
            payload = {"msg": resp.text}

        if resp.status_code >= 400:
            if isinstance(payload, dict):
                raise BinanceAPIError(resp.status_code, payload.get("code"), str(payload.get("msg")))
            raise BinanceAPIError(resp.status_code, None, str(payload))
        return payload

    # ------------------------------------------------------------------
    # Endpoint publik
    # ------------------------------------------------------------------
    def ping(self) -> bool:
        self._request("GET", "/fapi/v1/ping")
        return True

    def exchange_info(self) -> dict:
        return self._request("GET", "/fapi/v1/exchangeInfo")

    def ticker_price(self, symbol: Optional[str] = None) -> Any:
        params = {"symbol": symbol} if symbol else {}
        return self._request("GET", "/fapi/v1/ticker/price", params)

    def ticker_24h(self, symbol: str) -> dict:
        return self._request("GET", "/fapi/v1/ticker/24hr", {"symbol": symbol})

    def klines(self, symbol: str, interval: str = "15m", limit: int = 200) -> list[list]:
        """Klines mentah Binance: [open_time, open, high, low, close, volume, ...]."""
        return self._request(
            "GET", "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit}
        )

    def funding_rate(self, symbol: str) -> dict:
        data = self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------
    # Endpoint bertanda tangan — akun & posisi
    # ------------------------------------------------------------------
    def account(self) -> dict:
        return self._request("GET", "/fapi/v2/account", signed=True)

    def balances(self) -> list[dict]:
        return self._request("GET", "/fapi/v2/balance", signed=True)

    def asset_balance(self, asset: str = "USDT") -> dict:
        for row in self.balances():
            if row.get("asset") == asset:
                return row
        return {"asset": asset, "balance": "0", "availableBalance": "0"}

    def positions(self, symbol: Optional[str] = None) -> list[dict]:
        params = {"symbol": symbol} if symbol else {}
        rows = self._request("GET", "/fapi/v2/positionRisk", params, signed=True)
        return [r for r in rows if float(r.get("positionAmt", 0)) != 0.0]

    def open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        params = {"symbol": symbol} if symbol else {}
        return self._request("GET", "/fapi/v1/openOrders", params, signed=True)

    def income_history(self, limit: int = 100) -> list[dict]:
        return self._request("GET", "/fapi/v1/income", {"limit": limit}, signed=True)

    def realized_pnl_today(self) -> float:
        """Total realized PnL sejak 00:00 UTC hari ini (untuk batas rugi harian)."""
        import datetime as _dt

        start = int(
            _dt.datetime.now(_dt.timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
            * 1000
        )
        rows = self._request(
            "GET",
            "/fapi/v1/income",
            {"incomeType": "REALIZED_PNL", "startTime": start, "limit": 1000},
            signed=True,
        )
        return sum(float(r.get("income", 0)) for r in rows)

    # ------------------------------------------------------------------
    # Endpoint bertanda tangan — trading
    # ------------------------------------------------------------------
    def change_leverage(self, symbol: str, leverage: int) -> dict:
        return self._request(
            "POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, signed=True
        )

    def place_order(
        self,
        symbol: str,
        side: str,  # BUY | SELL
        order_type: str,  # MARKET | LIMIT | STOP_MARKET | TAKE_PROFIT_MARKET
        quantity: Optional[float] = None,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        close_position: bool = False,
    ) -> dict:
        params: dict[str, Any] = {"symbol": symbol, "side": side, "type": order_type}
        if close_position:
            params["closePosition"] = "true"
        else:
            if quantity is None:
                raise ValueError("quantity wajib diisi kecuali close_position=True")
            params["quantity"] = quantity
        if price is not None:
            params["price"] = price
            params["timeInForce"] = time_in_force
        if stop_price is not None:
            params["stopPrice"] = stop_price
        if reduce_only and not close_position:
            params["reduceOnly"] = "true"
        log.info("ORDER → %s", {k: v for k, v in params.items()})
        result = self._request("POST", "/fapi/v1/order", params, signed=True)
        log.info("ORDER ✓ orderId=%s status=%s", result.get("orderId"), result.get("status"))
        return result

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._request(
            "DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id}, signed=True
        )

    def cancel_all_orders(self, symbol: str) -> dict:
        return self._request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)
