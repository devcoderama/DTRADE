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
from decimal import Decimal
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
        _retried_1021: bool = False,
    ) -> Any:
        try:
            return self._request_once(method, path, params, signed)
        except BinanceAPIError as exc:
            # -1021: timestamp di luar recvWindow. Request DITOLAK sebelum
            # diproses bursa, jadi mengulanginya setelah sinkronisasi jam
            # aman — termasuk untuk penempatan order.
            if getattr(exc, "code", None) == -1021 and not _retried_1021:
                log.warning("Error -1021 (jam tidak sinkron) — sinkronisasi waktu lalu ulangi sekali.")
                try:
                    self.sync_time()
                except Exception as sync_exc:  # sinkronisasi gagal: teruskan error asli
                    log.warning("Sinkronisasi waktu gagal: %s", sync_exc)
                    raise exc
                return self._request(method, path, params, signed, _retried_1021=True)
            raise

    @staticmethod
    def _plain_number(value: Any) -> Any:
        """Serialisasi float TANPA notasi ilmiah (Fase 6-A2).

        str(0.0000123) = '1.23e-05' ditolak Binance (-1100) pada koin
        berharga mikro. Decimal + format 'f' menghasilkan string polos;
        normalisasi terjadi SEBELUM penandatanganan agar tanda tangan
        dihitung atas string yang persis dikirim.
        """
        if isinstance(value, float):
            return format(Decimal(str(value)), "f")
        return value

    def _request_once(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        signed: bool = False,
    ) -> Any:
        params = {k: self._plain_number(v) for k, v in dict(params or {}).items()}
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
    def server_time(self) -> dict:
        """Waktu server Binance (GET /fapi/v1/time) — untuk ukur drift jam."""
        return self._request("GET", "/fapi/v1/time")

    def dual_side_position(self) -> dict:
        """Mode posisi akun (GET /fapi/v1/positionSide/dual, bertanda tangan).

        {"dualSidePosition": true} = Hedge Mode — TIDAK didukung kode ini
        (tidak pernah mengirim positionSide), lihat Fase 6-A7.
        """
        return self._request("GET", "/fapi/v1/positionSide/dual", signed=True)

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

    def klines(
        self,
        symbol: str,
        interval: str = "15m",
        limit: int = 200,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> list[list]:
        """Klines mentah Binance: [open_time, open, high, low, close, volume, ...]."""
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return self._request("GET", "/fapi/v1/klines", params)

    def klines_paginated(
        self, symbol: str, interval: str, start_time: int, end_time: int, page: int = 1500
    ) -> list[list]:
        """Ambil klines rentang panjang dengan paginasi startTime (≥10rb candle).

        Berhenti saat halaman kosong/kurang dari ukuran halaman atau
        melewati end_time. Duplikat pada batas halaman dibuang.
        """
        rows: list[list] = []
        cursor = start_time
        seen: set[int] = set()
        while cursor <= end_time:
            batch = self.klines(
                symbol, interval=interval, limit=page, start_time=cursor, end_time=end_time
            )
            if not batch:
                break
            for row in batch:
                t = int(row[0])
                if t not in seen:
                    seen.add(t)
                    rows.append(row)
            last = int(batch[-1][0])
            if len(batch) < page or last <= cursor:
                break
            cursor = last + 1
        rows.sort(key=lambda r: int(r[0]))
        return rows

    def funding_rate(self, symbol: str) -> dict:
        data = self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
        return data if isinstance(data, dict) else {}

    def tickers_24h_all(self) -> list[dict]:
        """Ticker 24 jam SEMUA simbol dalam satu panggilan (untuk pemindaian)."""
        data = self._request("GET", "/fapi/v1/ticker/24hr")
        return data if isinstance(data, list) else []

    def funding_all(self) -> list[dict]:
        """Premium index (funding rate) SEMUA simbol dalam satu panggilan."""
        data = self._request("GET", "/fapi/v1/premiumIndex")
        return data if isinstance(data, list) else []

    def open_interest(self, symbol: str) -> float:
        """Open interest terkini (dalam unit aset dasar)."""
        data = self._request("GET", "/fapi/v1/openInterest", {"symbol": symbol})
        if isinstance(data, dict):
            return float(data.get("openInterest", 0) or 0)
        return 0.0

    # ------------------------------------------------------------------
    # Endpoint bertanda tangan — akun & posisi
    # ------------------------------------------------------------------
    def _request_v3_first(self, path_v3: str, path_v2: str, params=None):
        """Fase 6-A7: utamakan endpoint v3, fallback ke v2 bila v3 gagal.

        Bukti migrasi: panduan resmi Binance Futures Connector — sejak
        Des 2024 /fapi/v2/account & /fapi/v2/balance digantikan
        /fapi/v3/*, dan /fapi/v2/positionRisk deprecated (Jan 2025)
        demi /fapi/v3/positionRisk. Fallback menjaga kompatibilitas
        bila suatu lingkungan belum menyediakan v3; verifikasi perilaku
        testnet tercatat di laporan E2E Fase 6.
        """
        try:
            return self._request("GET", path_v3, params, signed=True)
        except BinanceAPIError:
            return self._request("GET", path_v2, params, signed=True)

    def account(self) -> dict:
        return self._request_v3_first("/fapi/v3/account", "/fapi/v2/account")

    def balances(self) -> list[dict]:
        return self._request_v3_first("/fapi/v3/balance", "/fapi/v2/balance")

    def asset_balance(self, asset: str = "USDT") -> dict:
        for row in self.balances():
            if row.get("asset") == asset:
                return row
        return {"asset": asset, "balance": "0", "availableBalance": "0"}

    def positions(self, symbol: Optional[str] = None) -> list[dict]:
        params = {"symbol": symbol} if symbol else {}
        rows = self._request_v3_first("/fapi/v3/positionRisk", "/fapi/v2/positionRisk", params)
        return [r for r in rows if float(r.get("positionAmt", 0)) != 0.0]

    def open_orders(self, symbol: Optional[str] = None) -> list[dict]:
        params = {"symbol": symbol} if symbol else {}
        return self._request("GET", "/fapi/v1/openOrders", params, signed=True)

    def income_history(
        self,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        income_type: Optional[str] = None,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if start_time is not None:
            params["startTime"] = int(start_time)
        if end_time is not None:
            params["endTime"] = int(end_time)
        if income_type:
            params["incomeType"] = income_type
        return self._request("GET", "/fapi/v1/income", params, signed=True)

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
        new_client_order_id: Optional[str] = None,
    ) -> dict:
        params: dict[str, Any] = {"symbol": symbol, "side": side, "type": order_type}
        if new_client_order_id:
            params["newClientOrderId"] = new_client_order_id
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

    def query_order(
        self,
        symbol: str,
        order_id: Optional[int] = None,
        client_order_id: Optional[str] = None,
    ) -> dict:
        """Periksa status satu order (rekonsiliasi & idempotency).

        Dipakai executor untuk memastikan hasil order yang statusnya
        tidak diketahui (mis. timeout) TANPA mengirim order duplikat.
        """
        params: dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id
        if "orderId" not in params and "origClientOrderId" not in params:
            raise ValueError("query_order membutuhkan order_id atau client_order_id")
        return self._request("GET", "/fapi/v1/order", params, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._request(
            "DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id}, signed=True
        )

    def cancel_all_orders(self, symbol: str) -> dict:
        return self._request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)

    # ------------------------------------------------------------------
    # Algo Order API — order kondisional (SL/TP) WAJIB lewat sini.
    # Sejak migrasi Binance, /fapi/v1/order menolak STOP_MARKET dkk.
    # dengan error -4120 ("use the Algo Order API endpoints instead").
    # Terverifikasi langsung di testnet pada 2026-11-16.
    # ------------------------------------------------------------------
    def place_algo_order(
        self,
        symbol: str,
        side: str,
        order_type: str,  # STOP_MARKET | TAKE_PROFIT_MARKET | STOP | TAKE_PROFIT
        trigger_price: float,
        quantity: Optional[float] = None,
        close_position: bool = False,
        client_algo_id: Optional[str] = None,
        working_type: Optional[str] = None,
    ) -> dict:
        params: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "triggerPrice": trigger_price,
        }
        # TERVERIFIKASI (Fase 6, E2E testnet 2026-11-16): nama parameter
        # `workingType` dengan nilai MARK_PRICE diterima Binance Futures
        # testnet — algo order SL terbaca kembali dengan
        # workingType='MARK_PRICE' (skenario W9 scripts/e2e_web_testnet.py).
        if working_type:
            params["workingType"] = working_type
        if client_algo_id:
            params["clientAlgoId"] = client_algo_id
        if close_position:
            params["closePosition"] = "true"
        elif quantity is not None:
            params["quantity"] = quantity
            params["reduceOnly"] = "true"
        log.info("ALGO ORDER → %s", params)
        result = self._request("POST", "/fapi/v1/algoOrder", params, signed=True)
        log.info("ALGO ORDER ✓ algoId=%s status=%s", result.get("algoId"), result.get("algoStatus"))
        return result

    def open_algo_orders(self, symbol: Optional[str] = None) -> list[dict]:
        params = {"symbol": symbol} if symbol else {}
        result = self._request("GET", "/fapi/v1/openAlgoOrders", params, signed=True)
        if isinstance(result, list):
            return result
        return result.get("orders", []) if isinstance(result, dict) else []

    def cancel_algo_order(
        self,
        symbol: str,
        algo_id: Optional[int] = None,
        client_algo_id: Optional[str] = None,
    ) -> dict:
        params: dict[str, Any] = {"symbol": symbol}
        if algo_id is not None:
            params["algoId"] = algo_id
        if client_algo_id:
            params["clientAlgoId"] = client_algo_id
        return self._request("DELETE", "/fapi/v1/algoOrder", params, signed=True)

    def cancel_all_algo_orders(self, symbol: str) -> int:
        """Batalkan satu per satu — tidak ada endpoint massal yang andal."""
        count = 0
        for order in self.open_algo_orders(symbol):
            try:
                self.cancel_algo_order(symbol, algo_id=order.get("algoId"))
                count += 1
            except BinanceAPIError as exc:
                log.warning("Gagal membatalkan algo order %s: %s", order.get("algoId"), exc)
        return count
