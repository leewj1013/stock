from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .app import load_env, naver_rows, write_error_log
from .dashboard import latest_position_rows, render, today_recommendation_rows
from .data_store import import_legacy_virtual_trader, virtual_buy, virtual_deposit, virtual_trader_state


HOST = "127.0.0.1"
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))


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
    return {
        **state,
        "price_updated_at": datetime.now().isoformat(timespec="seconds"),
        "price_source": "네이버 금융 (실패 시 최근 저장 가격)",
    }


class DashboardHandler(BaseHTTPRequestHandler):
    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/trader":
            self._json(200, trader_payload())
            return
        if path in {"/", "/dashboard"}:
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
