from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "https://openapi.tossinvest.com"


class TossApiError(RuntimeError):
    def __init__(self, status: int | None, code: str, message: str):
        self.status = status
        self.code = code
        super().__init__(f"Toss API error status={status or 'network'} code={code}: {message}")


class TossClient:
    """Minimal read-only Toss Securities client.

    Deliberately exposes authentication, market prices and account discovery only.
    No order endpoint is implemented in this module.
    """

    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 base_url: str | None = None, timeout: float = 10.0):
        self.client_id = client_id or os.environ.get("TOSS_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("TOSS_CLIENT_SECRET", "")
        self.base_url = (base_url or os.environ.get("TOSS_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._access_token = ""
        self._expires_at = 0.0
        if not self.client_id or not self.client_secret:
            raise ValueError("TOSS_CLIENT_ID and TOSS_CLIENT_SECRET are required")

    def access_token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        payload = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode()
        request = urllib.request.Request(
            f"{self.base_url}/oauth2/token", data=payload, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "stockAlarm/1.0"},
        )
        body = self._open_json(request)
        token = str(body.get("access_token") or "")
        if not token:
            raise TossApiError(200, "invalid-token-response", "access_token is missing")
        expires_in = max(1, int(body.get("expires_in") or 0))
        self._access_token = token
        self._expires_at = time.time() + expires_in
        return token

    def prices(self, symbols: list[str]) -> list[dict]:
        cleaned = [symbol.strip() for symbol in symbols if symbol and symbol.strip()]
        if not cleaned or len(cleaned) > 200:
            raise ValueError("symbols must contain between 1 and 200 items")
        query = urllib.parse.urlencode({"symbols": ",".join(cleaned)})
        result = self._get(f"/api/v1/prices?{query}").get("result", [])
        return result if isinstance(result, list) else []

    def accounts(self) -> list[dict]:
        result = self._get("/api/v1/accounts").get("result", [])
        return result if isinstance(result, list) else []

    def connection_check(self, probe_symbol: str = "005930") -> dict:
        self.access_token()
        prices = self.prices([probe_symbol])
        accounts = self.accounts()
        return {
            "authenticated": True,
            "market_data_ok": bool(prices),
            "probe_symbol": probe_symbol,
            "probe_currency": str(prices[0].get("currency") or "") if prices else "",
            "account_api_ok": True,
            "account_count": len(accounts),
            "account_types": sorted({str(row.get("accountType") or "unknown") for row in accounts}),
            "trading_enabled": False,
        }

    def _get(self, path: str) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}", method="GET",
            headers={"Authorization": f"Bearer {self.access_token()}", "User-Agent": "stockAlarm/1.0"},
        )
        return self._open_json(request)

    def _open_json(self, request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                payload = {}
            detail = payload.get("error", payload)
            if isinstance(detail, dict):
                code = str(detail.get("code") or detail.get("error") or "http-error")
                message = str(detail.get("message") or detail.get("error_description") or error.reason)
            else:
                code, message = "http-error", str(error.reason)
            raise TossApiError(error.code, code, message) from None
        except urllib.error.URLError as error:
            raise TossApiError(None, "network-error", str(error.reason)) from None

