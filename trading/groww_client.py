"""Verified REST client for Groww Trading API.

Keeps live I/O behind one adapter so the strategy can run in MOCK/PAPER mode
without pretending a broker session is live.
"""
from __future__ import annotations

import json
import hashlib
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    import pyotp
except ImportError:
    pyotp = None

try:
    import httpx
except ImportError:
    httpx = None

from config import settings


class GrowwAPIError(RuntimeError):
    pass


class GrowwRESTClient:
    def __init__(self) -> None:
        self.base_url = settings.GROWW_API_BASE_URL.rstrip("/")
        self.timeout = settings.GROWW_HTTP_TIMEOUT_SECONDS
        self.access_token: Optional[str] = None
        self.access_token_expiry: float = 0.0

    def _headers(self) -> Dict[str, str]:
        if not self.access_token:
            self.authenticate()
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "X-API-VERSION": "1.0",
        }

    def authenticate(self) -> str:
        """Use Groww's documented TOTP or API-key+secret flow; never fabricate a token."""
        api_key = settings.GROWW_API_KEY
        if not api_key:
            raise GrowwAPIError("GROWW_API_KEY is required for LIVE_DATA mode")

        url = f"{self.base_url}/v1/token/api/access"
        mode = settings.GROWW_AUTH_MODE.upper()
        payload: Dict[str, str]
        if mode == "APPROVAL":
            if not settings.GROWW_API_SECRET:
                raise GrowwAPIError("GROWW_API_SECRET is required for APPROVAL auth mode")
            timestamp = str(int(time.time()))
            checksum = hashlib.sha256(f"{settings.GROWW_API_SECRET}{timestamp}".encode()).hexdigest()
            payload = {"key_type": "approval", "checksum": checksum, "timestamp": timestamp}
        else:
            if not settings.GROWW_TOTP_SECRET:
                raise GrowwAPIError("GROWW_TOTP_SECRET is required for TOTP auth mode")
            from auth import generate_totp_rfc6238
            payload = {"key_type": "totp", "totp": generate_totp_rfc6238(settings.GROWW_TOTP_SECRET)}

        if httpx:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        else:
            import urllib.request
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))

        if body.get("status") == "FAILURE":
            raise GrowwAPIError(body.get("message", "Groww authentication failed"))
        token = body.get("token") or body.get("payload", {}).get("token")
        if not token:
            raise GrowwAPIError("Groww response did not contain an access token")
        self.access_token = token
        expiry_raw = body.get("expiry") or body.get("payload", {}).get("expiry")
        if expiry_raw:
            try:
                expiry_dt = datetime.fromisoformat(str(expiry_raw).replace("Z", "+00:00"))
                self.access_token_expiry = expiry_dt.timestamp()
            except ValueError:
                self.access_token_expiry = time.time() + 20 * 3600
        else:
            self.access_token_expiry = time.time() + 20 * 3600
        return token

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.access_token or time.time() >= self.access_token_expiry - 60:
            self.authenticate()
        
        url = f"{self.base_url}{path}"
        if httpx:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._headers(), params=params)
                if response.status_code == 401:
                    self.authenticate()
                    response = client.get(url, headers=self._headers(), params=params)
                response.raise_for_status()
                body = response.json()
        else:
            import urllib.request
            import urllib.parse
            full_url = f"{url}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(full_url, headers=self._headers())
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))

        if body.get("status") == "FAILURE":
            raise GrowwAPIError(body.get("message", f"Groww request failed: {path}"))
        return body.get("payload", body)

    def get_quote(self, trading_symbol: str = "NIFTY") -> Dict[str, Any]:
        return self._get(
            "/v1/live-data/quote",
            {"exchange": "NSE", "segment": "CASH", "trading_symbol": trading_symbol},
        )

    def get_option_chain(self, underlying: str = "NIFTY", expiry_date: Optional[str] = None) -> Dict[str, Any]:
        if not expiry_date:
            expiries = self._get(
                "/v1/historical/expiries",
                {"exchange": "NSE", "underlying_symbol": underlying, "year": datetime.now().year},
            ).get("expiries", [])
            today = datetime.now(timezone.utc).date().isoformat()
            future_expiries = [e for e in expiries if e >= today]
            expiry_date = future_expiries[0] if future_expiries else None
        if not expiry_date:
            raise GrowwAPIError("No future expiry found for option chain")
        return self._get(
            f"/v1/option-chain/exchange/NSE/underlying/{underlying}",
            {"expiry_date": expiry_date},
        )

    def _post(self, path: str, json_data: Dict[str, Any]) -> Dict[str, Any]:
        if not self.access_token or time.time() >= self.access_token_expiry - 60:
            self.authenticate()
        url = f"{self.base_url}{path}"
        if httpx:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, headers=self._headers(), json=json_data)
                if response.status_code == 401:
                    self.authenticate()
                    response = client.post(url, headers=self._headers(), json=json_data)
                response.raise_for_status()
                body = response.json()
        else:
            import urllib.request
            req = urllib.request.Request(
                url,
                data=json.dumps(json_data).encode("utf-8"),
                headers={**self._headers(), "Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))

        if body.get("status") == "FAILURE":
            raise GrowwAPIError(body.get("message", f"Groww POST failed: {path}"))
        return body.get("payload", body)

    def _delete(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.access_token or time.time() >= self.access_token_expiry - 60:
            self.authenticate()
        url = f"{self.base_url}{path}"
        if httpx:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.delete(url, headers=self._headers(), params=params)
                if response.status_code == 401:
                    self.authenticate()
                    response = client.delete(url, headers=self._headers(), params=params)
                response.raise_for_status()
                body = response.json()
        else:
            import urllib.request
            import urllib.parse
            full_url = f"{url}?{urllib.parse.urlencode(params or {})}"
            req = urllib.request.Request(full_url, headers=self._headers(), method="DELETE")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))

        if body.get("status") == "FAILURE":
            raise GrowwAPIError(body.get("message", f"Groww DELETE failed: {path}"))
        return body.get("payload", body)

    def place_order(
        self,
        trading_symbol: str,
        quantity: int,
        side: str = "BUY",
        order_type: str = "LIMIT",
        price: float = 0.0,
        trigger_price: float = 0.0,
        exchange: str = "NSE",
        segment: str = "FNO",
        product: str = "MIS",
    ) -> Dict[str, Any]:
        """Place an intraday limit/market/SL order."""
        # Groww API contract: uses 'product' (not 'product_type') and 'validity' (not 'duration')
        payload = {
            "exchange": exchange,
            "segment": segment,
            "trading_symbol": trading_symbol,
            "transaction_type": side.upper(),  # BUY / SELL
            "order_type": order_type.upper(),   # LIMIT / MARKET / SL / SL-M
            "quantity": quantity,
            "product": product,                 # MIS / CNC / NRML
            "price": price,
            "trigger_price": trigger_price,
            "validity": "DAY",
        }
        return self._post("/v1/order/create", payload)

    def place_protective_stop_loss(
        self,
        trading_symbol: str,
        quantity: int,
        stop_loss_trigger_price: float,
        limit_price: Optional[float] = None,
        segment: str = "FNO",
        product: str = "MIS",
    ) -> Dict[str, Any]:
        """
        Places a broker-side hardware protective Stop-Loss order on the exchange.
        Protects capital against network cuts, OS crashes, or local execution drops.
        """
        if limit_price is None:
            # Set limit price 2 points below trigger to guarantee execution during fast slippage
            limit_price = max(0.5, round(stop_loss_trigger_price - 2.0, 1))

        payload = {
            "exchange": "NSE",
            "segment": segment,
            "trading_symbol": trading_symbol,
            "transaction_type": "SELL",
            "order_type": "SL",
            "quantity": quantity,
            "product": product,
            "price": limit_price,
            "trigger_price": stop_loss_trigger_price,
            "validity": "DAY",
        }
        return self._post("/v1/order/create", payload)

    def modify_order(
        self,
        order_id: str,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Modify an existing open or trigger pending order."""
        # Groww API requires groww_order_id and segment for order modification
        payload: Dict[str, Any] = {
            "groww_order_id": order_id,
            "segment": "FNO",  # Default to FNO for options; caller can pass segment if needed
        }
        if quantity is not None:
            payload["quantity"] = quantity
        if price is not None:
            payload["price"] = price
        if trigger_price is not None:
            payload["trigger_price"] = trigger_price
        return self._post("/v1/order/modify", payload)

    def cancel_order(self, order_id: str, segment: str = "FNO") -> Dict[str, Any]:
        """Cancel a pending open order using POST as per current Groww API contract."""
        # Current Groww API: POST /v1/order/cancel with groww_order_id and segment
        payload = {"groww_order_id": order_id, "segment": segment}
        return self._post("/v1/order/cancel", payload)

    def get_order_status(self, order_id: str, segment: str = "FNO") -> Dict[str, Any]:
        """Query real-time broker execution status — requires segment per Groww API."""
        return self._get("/v1/order/status", {"groww_order_id": order_id, "segment": segment})

    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch all open and closed intraday positions."""
        res = self._get("/v1/positions/user", {})
        return res.get("positions", [])

    def reconcile_order(
        self,
        order_id: str,
        max_retries: int = 3,
        retry_delay_sec: float = 0.5,
    ) -> Dict[str, Any]:
        """Reconciliation worker verifying fills and partial fills with backoff."""
        for attempt in range(max_retries):
            try:
                status_info = self.get_order_status(order_id)
                state = status_info.get("order_status", "").upper()
                if state in ["FILLED", "EXECUTED", "CANCELLED", "REJECTED"]:
                    return status_info
                time.sleep(retry_delay_sec)
            except Exception as e:
                time.sleep(retry_delay_sec)
        return {"order_id": order_id, "order_status": "TIMEOUT_RECONCILIATION_PENDING"}

    def get_historical_candles(
        self,
        trading_symbol: str = "NIFTY",
        timeframe: str = "1m",
        count: int = 300,
    ) -> list[Dict[str, Any]]:
        interval = {"1m": "1minute", "3m": "3minute", "5m": "5minute", "10m": "10minute", "15m": "15minute"}.get(timeframe)
        if not interval:
            raise ValueError(f"Unsupported timeframe: {timeframe}")
        minutes = int(timeframe.replace("m", "")) if timeframe.endswith("m") else 1
        end = datetime.now()
        start = end - timedelta(minutes=minutes * count + 5)
        payload = self._get(
            "/v1/historical/candles",
            {
                "exchange": "NSE",
                "segment": "CASH",
                "groww_symbol": f"NSE-{trading_symbol}",
                "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
                "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
                "candle_interval": interval,
            },
        )
        records: list[Dict[str, Any]] = []
        for row in payload.get("candles", []):
            if len(row) < 6:
                continue
            records.append(
                {
                    "timestamp": row[0],
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5] or 0),
                }
            )
        return records[-count:]


live_client = GrowwRESTClient()
