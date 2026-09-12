from __future__ import annotations

import json
import hmac
import html
import os
import re
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from .app import load_env, naver_rows, write_error_log
from .dashboard import latest_position_rows, render, today_recommendation_rows
from .data_store import active_strategy_version, import_legacy_virtual_trader, latest_portfolio_risk, recent_equity_trend, recent_position_checks, recent_price_quality, recent_virtual_sales, virtual_buy, virtual_deposit, virtual_trader_state
from .market_breadth import CACHE_PATH as MARKET_BREADTH_CACHE_PATH
from .sector_reference import load_sector_mapping
from .trading_profiles import PROFILES


def profile_db_path(name: str) -> str:
    return PROFILES.get(name, PROFILES["aggressive"])["db_path"]


HOST = "127.0.0.1"
PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))
# Only this port is ever handed to the Cloudflare tunnel (see start_remote_dashboard.ps1).
# It must never be able to reach the full dashboard, /remote-setup, or any POST route:
# a tunnel forwards every request to 127.0.0.1 regardless of the caller's real address, so
# an HTTP Host header (or client_address) can never tell a genuine local visit apart from
# one relayed through the tunnel. Keeping the write/admin routes on a separate port that is
# never tunneled is what actually enforces "local-only", not a header check.
REMOTE_PORT = int(os.environ.get("DASHBOARD_REMOTE_PORT", "8766"))
REMOTE_ORIGIN = os.environ.get("DASHBOARD_REMOTE_ORIGIN", "https://leewj1013.github.io").rstrip("/")


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


def prices(path: str = "data/stock_alarm.db") -> dict[str, int]:
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
    holding_tickers = [row["ticker"] for row in virtual_trader_state(path=path)["holdings"]]
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


def trader_payload(profile: str = "aggressive") -> dict:
    path = profile_db_path(profile)
    state = virtual_trader_state(prices(path), path=path)
    holding_tickers = {str(row.get("ticker") or "") for row in state["holdings"]}
    try:
        sectors = load_sector_mapping(holding_tickers)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        sectors = {}
    risk = latest_portfolio_risk(path)
    strategy = active_strategy_version()
    quality = recent_price_quality(100, path)
    latest_quality = {}
    for row in quality:
        latest_quality.setdefault(row.get("ticker"), row)
    unavailable = [row["ticker"] for row in state["holdings"] if latest_quality.get(row["ticker"], {}).get("status") not in {None, "valid"}]
    breadth = None
    try:
        with open(MARKET_BREADTH_CACHE_PATH, encoding="utf-8") as file:
            breadth = float(json.load(file).get("up_ratio"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    market_limit = 70 if breadth is not None and breadth >= .60 else 40 if breadth is not None and breadth >= .45 else 10 if breadth is not None else None
    market_mode = "공격" if market_limit == 70 else "중립" if market_limit == 40 else "방어" if market_limit == 10 else "데이터 대기"
    from .toss_client import all_warnings_for, BLOCKING_STOCK_WARNINGS
    latest_checks = {}
    for row in recent_position_checks(1000, path):
        latest_checks.setdefault(row.get("ticker"), row)
    for holding in state["holdings"]:
        check = latest_checks.get(holding.get("ticker"), {})
        try:
            fallback_holding_days = (date.today() - date.fromisoformat(str(holding.get("first_entry_at") or "")[:10])).days
        except ValueError:
            fallback_holding_days = None
        stop_pct = float(check.get("dynamic_stop_loss_pct") or -5)
        stop_price = round(float(holding.get("average_price") or 0) * (1 + stop_pct / 100))
        distance = check.get("distance_ma20_pct")
        active_warnings = all_warnings_for(str(holding.get("ticker") or ""))
        blocking = active_warnings & BLOCKING_STOCK_WARNINGS
        non_blocking = active_warnings - BLOCKING_STOCK_WARNINGS
        if blocking:
            watch_state = f"종목 경고: {','.join(sorted(blocking))}"
        elif holding.get("position_status") == "partial":
            watch_state = "1차 익절 완료"
        elif check.get("decision") == "SELL":
            watch_state = "매도조건 충족"
        elif check.get("return_pct") is not None and float(check["return_pct"]) <= stop_pct * .8:
            watch_state = "손절선 근접"
        elif distance is not None and float(distance) <= 2:
            watch_state = "20일선 주의"
        elif non_blocking:
            watch_state = f"종목 주의: {','.join(sorted(non_blocking))}"
        else:
            watch_state = "정상 보유"
        holding.update({
            "sector": sectors.get(str(holding.get("ticker") or ""), "미분류"),
            "allocation_pct": round(float(holding.get("valuation") or 0) / max(float(state.get("total_equity") or 0), 1) * 100, 2),
            "holding_days": check.get("holding_days") if check.get("holding_days") is not None else fallback_holding_days,
            "watch_state": watch_state,
            "stop_price": stop_price,
            "ma20": check.get("ma20"),
        })
    sales = []
    for row in recent_virtual_sales(500, path):
        cost_basis = int(row.get("cost_basis") or 0)
        realized = int(row.get("realized_profit_loss") or 0)
        sales.append({
            **row,
            "return_pct": round(realized / cost_basis * 100, 2) if cost_basis else 0.0,
            "sale_label": "부분매도" if row.get("sale_type") == "partial" else "전량매도",
        })
    wins = sum(int(row.get("realized_profit_loss") or 0) > 0 for row in sales)
    total_cost = sum(int(row.get("cost_basis") or 0) for row in sales)
    total_realized = sum(int(row.get("realized_profit_loss") or 0) for row in sales)
    return {
        **state,
        "profile": profile,
        "equity_trend": recent_equity_trend(7, path),
        "price_updated_at": datetime.now().isoformat(timespec="seconds"),
        "price_source": "네이버 금융 · 검증 실패 종목은 진입가 임시표시",
        "risk": risk,
        "strategy_version": strategy.get("version_id", "기본 전략"),
        "price_unavailable_tickers": unavailable,
        "market_up_ratio_pct": round(breadth * 100, 2) if breadth is not None else None,
        "market_exposure_limit_pct": market_limit,
        "market_mode": market_mode,
        "sales": sales,
        "sale_summary": {
            "count": len(sales),
            "realized_profit_loss": total_realized,
            "win_rate_pct": round(wins / len(sales) * 100, 2) if sales else 0.0,
            "return_pct": round(total_realized / total_cost * 100, 2) if total_cost else 0.0,
        },
    }


def remote_setup_page() -> str:
    content = ""
    for path in ("logs/cloudflared.err.log", "logs/cloudflared.out.log"):
        try:
            with open(path, encoding="utf-8", errors="replace") as file:
                content += file.read()
        except OSError:
            pass
    match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", content)
    api_url = match.group(0) if match else ""
    token = os.environ.get("DASHBOARD_REMOTE_TOKEN", "")
    dashboard_url = "https://leewj1013.github.io/stock/?api=" + quote(api_url, safe="")
    return f"""<!doctype html><html lang=\"ko\"><meta charset=\"utf-8\"><title>원격 대시보드 연결</title>
<style>body{{font-family:Segoe UI,Malgun Gothic,sans-serif;max-width:680px;margin:48px auto;padding:0 20px}}input{{width:100%;padding:10px;box-sizing:border-box}}button,a{{display:inline-block;margin:12px 8px 0 0;padding:10px 14px}}.muted{{color:#666}}</style>
<h1>원격 대시보드 연결</h1><p>API 주소: <b>{html.escape(api_url or '터널 미실행')}</b></p>
<label for=\"token\">읽기 전용 접속 토큰</label><input id=\"token\" type=\"password\" readonly value={html.escape(json.dumps(token))}>
<button id=\"copy\" type=\"button\">토큰 복사</button><a href=\"{html.escape(dashboard_url)}\" target=\"_blank\" rel=\"noreferrer\">GitHub Pages 열기</a>
<p class=\"muted\" id=\"status\">토큰 복사 후 GitHub Pages의 토큰 입력란에 붙여넣으세요.</p>
<script>document.getElementById('copy').onclick=async()=>{{await navigator.clipboard.writeText(document.getElementById('token').value);document.getElementById('status').textContent='토큰을 복사했습니다.';}};</script></html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    """Full local admin UI: dashboard page, remote-setup page (which prints the
    remote token in cleartext), and every trader write route. Deliberately has
    no auth of its own -- it is only ever reachable by whoever is sitting at
    this machine, because HOST binds to 127.0.0.1 and this port is never
    handed to the Cloudflare tunnel (see REMOTE_PORT above).
    """

    def _cors(self) -> None:
        # Same-origin access via http://127.0.0.1:PORT/ (open_dashboard.bat)
        # never involves CORS at all, so this handler historically sent no
        # CORS headers. Opening dashboard.html directly (file://) is
        # cross-origin with Origin: null, and browsers then withhold the
        # response from JS without this header -- the dashboard's trader
        # panel silently renders its empty initial state instead. HOST
        # already binds to loopback only, so this stays narrowly scoped to
        # file:// rather than reflecting arbitrary origins.
        if self.headers.get("Origin", "") == "null":
            self.send_header("Access-Control-Allow-Origin", "null")
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
        if self.headers.get("Origin", "") != "null":
            self.send_error(403)
            return
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/trader":
            profile = parse_qs(parsed.query).get("profile", ["aggressive"])[0]
            self._json(200, trader_payload(profile if profile in PROFILES else "aggressive"))
            return
        if path in {"/", "/dashboard"}:
            payload = render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/remote-setup":
            payload = remote_setup_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
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
            profile = body.get("profile") if body.get("profile") in PROFILES else "aggressive"
            db_path = profile_db_path(profile)
            if path == "/api/trader/deposit":
                virtual_deposit(int(body.get("amount", 0)), path=db_path)
                self._json(200, trader_payload(profile))
                return
            if path == "/api/trader/buy":
                result = virtual_buy(recommendations(), path=db_path)
                self._json(200, {**trader_payload(profile), **{key: result[key] for key in ("spent", "bought", "executions")}})
                return
            if path == "/api/trader/import":
                imported = import_legacy_virtual_trader(body, path=db_path)
                self._json(200, {**trader_payload(profile), "imported": imported})
                return
            self.send_error(404)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
        except Exception as error:
            write_error_log(error)
            self._json(500, {"error": "가상 트레이더 처리 중 오류가 발생했습니다."})

    def log_message(self, format: str, *args: object) -> None:
        return


class RemoteReadOnlyHandler(BaseHTTPRequestHandler):
    """The only handler ever exposed through the Cloudflare tunnel.

    Serves a single read-only, token-gated route and nothing else -- no
    dashboard HTML, no /remote-setup (which would leak the token itself), no
    write routes. Anyone who finds the tunnel URL still needs a valid
    DASHBOARD_REMOTE_TOKEN to get anything back.
    """

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
        parsed = urlparse(self.path)
        if parsed.path != "/api/trader":
            self.send_error(404)
            return
        if not allowed_origin(self.headers.get("Origin", "")) or not valid_remote_token(self.headers.get("Authorization", "")):
            self._json(401, {"error": "원격 대시보드 인증이 필요합니다."})
            return
        profile = parse_qs(parsed.query).get("profile", ["aggressive"])[0]
        self._json(200, trader_payload(profile if profile in PROFILES else "aggressive"))

    def do_POST(self) -> None:  # noqa: N802
        self._json(403, {"error": "원격에서는 조회만 가능합니다."})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    load_env()
    remote_server = ThreadingHTTPServer((HOST, REMOTE_PORT), RemoteReadOnlyHandler)
    threading.Thread(target=remote_server.serve_forever, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), DashboardHandler)
    print(f"http://{HOST}:{PORT}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
