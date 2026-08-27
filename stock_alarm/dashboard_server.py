from __future__ import annotations

import json
import hmac
import os
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .app import load_env, naver_rows, write_error_log
from .dashboard import latest_position_rows, render, today_recommendation_rows
from .data_store import active_strategy_version, import_legacy_virtual_trader, latest_portfolio_risk, recent_price_quality, virtual_buy, virtual_deposit, virtual_trader_state


HOST = "127.0.0.1"
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))
REMOTE_ORIGIN = os.environ.get("DASHBOARD_REMOTE_ORIGIN", "https://leewj1013.github.io").rstrip("/")


def is_local_host(host: str) -> bool:
    hostname = host.split(":", 1)[0].strip("[]").lower()
    return hostname in {"127.0.0.1", "localhost", "::1"}


def allowed_origin(origin: str) -> str:
    clean = origin.rstrip("/")
    if clean == REMOTE_ORIGIN or clean.startswith("http://127.0.0.1:") or clean.startswith("http://localhost:"):
        return clean
    return ""


def valid_remote_token(authorization: str) -> bool:
    expected = os.environ.get("DASHBOARD_REMOTE_TOKEN", "")
    supplied = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def recommendations() -> list[dict[str, str]]:
    return today_recommendation_rows()


def prices() -> dict[str, int]:
    price_map = {
        row.get("ticker", ""): int(float(row.get("close") or 0))
        for row in recommendations()
        if row.get("ticker") and row.get("close")
    }
    # A position report is newer than the original recommendation price.
    price_map.update({
        row.get("ticker", ""): int(float(row.get("close") or 0))
        for row in latest_position_rows()
        if row.get("ticker") and row.get("close")
    })
    holding_tickers = [row["ticker"] for row in virtual_trader_state()["holdings"]]
    today = date.today()
    for ticker in holding_tickers:
        try:
            rows = naver_rows(ticker, today - timedelta(days=10), today, max_cache_age_seconds=60)
            if rows:
                price_map[ticker] = int(rows[-1][4])
        except (OSError, ValueError, TypeError):
            # Keep the most recent report/recommendation price if Naver is temporarily unavailable.
            continue
    return price_map


def trader_payload() -> dict:
    state = virtual_trader_state(prices())
    risk = latest_portfolio_risk()
    strategy = active_strategy_version()
    quality = recent_price_quality(100)
    latest_quality = {}
    for row in quality:
        latest_quality.setdefault(row.get("ticker"), row)
    unavailable = [row["ticker"] for row in state["holdings"] if latest_quality.get(row["ticker"], {}).get("status") not in {None, "valid"}]
    return {
        **state,
        "price_updated_at": datetime.now().isoformat(timespec="seconds"),
        "price_source": "네이버 금융 · 검증 실패 종목은 진입가 임시표시",
        "risk": risk,
        "strategy_version": strategy.get("version_id", "기본 전략"),
        "price_unavailable_tickers": unavailable,
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def _is_local(self) -> bool:
        return is_local_host(self.headers.get("Host", ""))

    def _remote_read_allowed(self) -> bool:
        return bool(
            allowed_origin(self.headers.get("Origin", ""))
            and valid_remote_token(self.headers.get("Authorization", ""))
        )

    def _cors(self) -> None:
        origin = allowed_origin(self.headers.get("Origin", ""))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:  # noqa: N802
        if not allowed_origin(self.headers.get("Origin", "")):
            self.send_error(403)
            return
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/trader":
            if not self._is_local() and not self._remote_read_allowed():
                self._json(401, {"error": "원격 대시보드 인증이 필요합니다."})
                return
            self._json(200, trader_payload())
            return
        if path in {"/", "/dashboard"}:
            if not self._is_local():
                self.send_error(404)
                return
            payload = render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            if not self._is_local():
                self._json(403, {"error": "원격에서는 조회만 가능합니다."})
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            path = urlparse(self.path).path
            if path == "/api/trader/deposit":
                virtual_deposit(int(body.get("amount", 0)))
                self._json(200, trader_payload())
                return
            if path == "/api/trader/buy":
                result = virtual_buy(recommendations())
                self._json(200, {**trader_payload(), **{key: result[key] for key in ("spent", "bought", "executions")}})
                return
            if path == "/api/trader/import":
                imported = import_legacy_virtual_trader(body)
                self._json(200, {**trader_payload(), "imported": imported})
                return
            self.send_error(404)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
        except Exception as error:
            write_error_log(error)
            self._json(500, {"error": "가상 트레이더 처리 중 오류가 발생했습니다."})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    load_env()
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"http://{HOST}:{PORT}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
