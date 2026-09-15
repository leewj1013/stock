from __future__ import annotations

import json
import os
import time
import threading
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "https://openapi.tossinvest.com"
ALLOWED_API_HOST = "openapi.tossinvest.com"

# Warning types serious enough to block a new recommendation or force-exit an
# existing holding. OVERHEATED/INVESTMENT_WARNING/VI_* are common and often
# short-lived -- blocking on those would false-positive on otherwise-normal
# stocks, so they're surfaced as information only, not a hard stop.
BLOCKING_STOCK_WARNINGS = {"LIQUIDATION_TRADING", "INVESTMENT_RISK"}


class TossApiError(RuntimeError):
    def __init__(self, status: int | None, code: str, message: str):
        self.status = status
        self.code = code
        super().__init__(f"Toss API error status={status or 'network'} code={code}: {message}")


def validated_base_url(value: str) -> str:
    """Return the one production Toss origin credentials may be sent to."""
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("invalid TOSS_API_BASE_URL") from error
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != ALLOWED_API_HOST
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ValueError(f"TOSS_API_BASE_URL must be https://{ALLOWED_API_HOST}")
    return DEFAULT_BASE_URL


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        raise TossApiError(code, "redirect-blocked", "Toss API redirects are not allowed")


_URL_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _urlopen(request: urllib.request.Request, timeout: float):
    return _URL_OPENER.open(request, timeout=timeout)


class TossClient:
    """Minimal read-only Toss Securities client.

    Deliberately exposes authentication, market prices, account discovery,
    holdings, buying power, sellable quantity, commissions, order history/
    detail, stock warnings, candles and the market calendar only. Order
    *placement* (create/modify/cancel) is intentionally not implemented --
    Toss's API grants it under the exact same OAuth2 client-credentials
    scheme as everything else here (no separate permission tier), so the
    only thing currently preventing a live trade is that this module never
    calls it.
    """

    def __init__(self, client_id: str | None = None, client_secret: str | None = None,
                 base_url: str | None = None, timeout: float = 10.0):
        self.client_id = client_id or os.environ.get("TOSS_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("TOSS_CLIENT_SECRET", "")
        self.base_url = validated_base_url(base_url or os.environ.get("TOSS_API_BASE_URL") or DEFAULT_BASE_URL)
        self.timeout = timeout
        self._access_token = ""
        self._expires_at = 0.0
        self._token_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._last_request_at: dict[str, float] = {}
        if not self.client_id or not self.client_secret:
            raise ValueError("TOSS_CLIENT_ID and TOSS_CLIENT_SECRET are required")

    def access_token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        with self._token_lock:
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

    def holdings(self, account_seq: int, symbol: str | None = None) -> dict:
        """Holdings overview (positions + totals) for one account.

        account_seq comes from accounts()' accountSeq field. Account/asset/
        order endpoints all require it as the X-Tossinvest-Account header,
        not a query param.
        """
        query = f"?{urllib.parse.urlencode({'symbol': symbol})}" if symbol else ""
        return self._get(f"/api/v1/holdings{query}", account_seq=account_seq).get("result", {})

    def buying_power(self, account_seq: int, currency: str = "KRW") -> dict:
        query = urllib.parse.urlencode({"currency": currency})
        return self._get(f"/api/v1/buying-power?{query}", account_seq=account_seq).get("result", {})

    def sellable_quantity(self, account_seq: int, symbol: str) -> dict:
        """How much of `symbol` this account can sell right now."""
        query = urllib.parse.urlencode({"symbol": symbol})
        return self._get(f"/api/v1/sellable-quantity?{query}", account_seq=account_seq).get("result", {})

    def commissions(self, account_seq: int) -> list[dict]:
        """Per-market trading commission rates for this account."""
        result = self._get("/api/v1/commissions", account_seq=account_seq).get("result", [])
        return result if isinstance(result, list) else []

    def order_history(self, account_seq: int, status: str, symbol: str | None = None,
                       from_date: str | None = None, to_date: str | None = None,
                       cursor: str | None = None, limit: int | None = None) -> dict:
        """Paginated order history. status is required: "OPEN" (unfilled/working)
        or "CLOSED" (filled/cancelled/expired)."""
        query = {"status": status}
        for key, value in (("symbol", symbol), ("from", from_date), ("to", to_date), ("cursor", cursor), ("limit", limit)):
            if value is not None:
                query[key] = value
        return self._get(f"/api/v1/orders?{urllib.parse.urlencode(query)}", account_seq=account_seq).get("result", {})

    def order_detail(self, account_seq: int, order_id: str) -> dict:
        """A single order's current status and fill history."""
        return self._get(f"/api/v1/orders/{order_id}", account_seq=account_seq).get("result", {})

    def stock_warnings(self, symbol: str) -> list[dict]:
        """Active trading-caution flags for a symbol: liquidation trading,
        overheated, investment warning/risk designation, VI (volatility
        interruption), stock warrants. No account header needed -- these are
        per-symbol, not per-account."""
        result = self._get(f"/api/v1/stocks/{symbol}/warnings").get("result", [])
        return result if isinstance(result, list) else []

    def blocking_warnings(self, symbol: str) -> set[str]:
        """The subset of stock_warnings() serious enough to act on (see
        BLOCKING_STOCK_WARNINGS)."""
        warnings = self.stock_warnings(symbol)
        return {row["warningType"] for row in warnings if row.get("warningType") in BLOCKING_STOCK_WARNINGS}

    def candles(self, symbol: str, interval: str = "1d", count: int = 200,
                before: str | None = None, adjusted: bool = True) -> dict:
        """OHLCV candles. interval is "1m" or "1d" only, max 200 per call."""
        query = {"symbol": symbol, "interval": interval, "count": count, "adjusted": str(adjusted).lower()}
        if before:
            query["before"] = before
        return self._get(f"/api/v1/candles?{urllib.parse.urlencode(query)}").get("result", {})

    def market_calendar_kr(self) -> dict:
        """Yesterday/today/tomorrow's KR trading hours (integrated KRX+NXT)."""
        return self._get("/api/v1/market-calendar/KR").get("result", {})

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

    def _get(self, path: str, account_seq: int | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.access_token()}", "User-Agent": "stockAlarm/1.0"}
        if account_seq is not None:
            headers["X-Tossinvest-Account"] = str(account_seq)
        request = urllib.request.Request(f"{self.base_url}{path}", method="GET", headers=headers)
        if path.startswith("/api/v1/candles"):
            group, per_second = "candles", 20
        elif path.startswith("/api/v1/prices"):
            group, per_second = "market-data", 15
        else:
            group, per_second = "stock", 5
        with self._rate_lock:
            wait = 1 / per_second - (time.monotonic() - self._last_request_at.get(group, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last_request_at[group] = time.monotonic()
        return self._open_json(request)

    def _open_json(self, request: urllib.request.Request) -> dict:
        for attempt in range(3):
            try:
                with _urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < 2:
                    try:
                        retry_after = float(error.headers.get("Retry-After", "1"))
                    except (TypeError, ValueError):
                        retry_after = 1.0
                    time.sleep(max(0.1, min(5.0, retry_after)) * (2 ** attempt))
                    continue
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
        raise TossApiError(429, "rate-limit", "retry limit exceeded")


def candles_to_naver_rows(candles: list[dict]) -> list[list]:
    """Convert candles()' `candles` list into the [YYYYMMDD, open, high, low,
    close, volume] row format naver_rows() produces (oldest first), so a
    caller can use either source as a drop-in for the same downstream code."""
    rows = []
    for candle in candles:
        try:
            day = candle["timestamp"][:10].replace("-", "")
            rows.append([
                day, int(float(candle["openPrice"])), int(float(candle["highPrice"])),
                int(float(candle["lowPrice"])), int(float(candle["closePrice"])), int(float(candle.get("volume") or 0)),
            ])
        except (KeyError, ValueError, TypeError):
            continue
    rows.sort(key=lambda row: row[0])
    return rows


_shared_client: TossClient | None = None
_shared_client_key: tuple[str, str, str] | None = None
_shared_client_lock = threading.Lock()


def shared_client() -> TossClient:
    global _shared_client, _shared_client_key
    key = (
        os.environ.get("TOSS_CLIENT_ID", ""),
        os.environ.get("TOSS_CLIENT_SECRET", ""),
        os.environ.get("TOSS_API_BASE_URL", DEFAULT_BASE_URL),
    )
    with _shared_client_lock:
        if _shared_client is None or _shared_client_key != key:
            _shared_client = TossClient()
            _shared_client_key = key
        return _shared_client


def reset_shared_client() -> None:
    global _shared_client, _shared_client_key
    with _shared_client_lock:
        _shared_client = None
        _shared_client_key = None


def blocking_warnings_for(symbol: str) -> set[str]:
    """TossClient().blocking_warnings(), tolerating any failure (missing
    TOSS_CLIENT_ID/SECRET, network, rate limit) as "no warning known" --
    callers use this to gate recommendations/holdings, and a Toss outage
    must not block the whole recommendation or sell-check run."""
    try:
        return shared_client().blocking_warnings(symbol)
    except Exception:
        return set()


def all_warnings_for(symbol: str) -> set[str]:
    """All active stock_warnings() types (blocking and info-only alike),
    tolerating any failure the same way blocking_warnings_for() does. Lets a
    caller show OVERHEATED/INVESTMENT_WARNING/VI_* as information without a
    second Toss call -- split the result against BLOCKING_STOCK_WARNINGS
    locally instead of calling blocking_warnings_for() separately."""
    try:
        return {row["warningType"] for row in shared_client().stock_warnings(symbol)}
    except Exception:
        return set()


def latest_close_for(symbol: str) -> int | None:
    """Most recent daily close from Toss, tolerating any failure as "unknown"
    -- used to cross-check naver_rows() against an independent price source
    without ever blocking on a Toss outage."""
    try:
        candles = shared_client().candles(symbol, count=1).get("candles", [])
        return int(float(candles[-1]["closePrice"])) if candles else None
    except Exception:
        return None
