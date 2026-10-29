"""Loopback, read-only server for the dashboard v2 preview."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .app import load_env
from .dashboard_v2 import dashboard_v2_payload, real_account_payload


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path((RUNTIME_ROOT / "workspace.path").read_text(encoding="utf-8-sig").strip()) if (RUNTIME_ROOT / "workspace.path").exists() else RUNTIME_ROOT
PORT = int(os.environ.get("DASHBOARD_V2_PORT", "8767"))


class DashboardV2Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        if host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self.send_error(403)
            return
        origin = self.headers.get("Origin", "")
        if origin and origin != f"http://{host}":
            self.send_error(403)
            return
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/") and self.headers.get("Sec-Fetch-Site", "") not in {"", "same-origin", "none"}:
            self.send_error(403)
            return
        if path in {"/api/v2", "/api/v2/real-account", "/api/v2/view"}:
            if path == "/api/v2/real-account":
                data = real_account_payload()
            elif path == "/api/v2":
                data = dashboard_v2_payload()
            else:
                query = parse_qs(parsed.query)
                market = query.get("market", [""])[0]
                view = query.get("view", [""])[0]
                if market not in {"kr", "us"} or view not in {"signals", "account", "compare", "research"}:
                    self.send_error(400)
                    return
                if view == "signals":
                    page_text = query.get("page", ["0"])[0]
                    filter_name = query.get("filter", ["전체"])[0]
                    term = query.get("q", [""])[0]
                    if not page_text.isdecimal() or len(page_text) > 6 or filter_name not in {"전체", "매수 추천", "매도 신호", "추적 중"} or len(term) > 100:
                        self.send_error(400)
                        return
                    data = dashboard_v2_payload(market, view, page=int(page_text), filter_name=filter_name, query=term)
                else:
                    data = dashboard_v2_payload(market, view)
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            kind = "application/json"
        elif path in {"/", "/v2"}:
            body = (ROOT / "design/dashboard-v2-sample.html").read_bytes()
            kind = "text/html"
        elif path in {"/dashboard-v2-shell.js", "/dashboard-v2-live.js"}:
            body = (ROOT / "design" / path.lstrip("/")).read_bytes()
            kind = "text/javascript"
        elif path == "/dashboard-v2.css":
            body = (ROOT / "design/dashboard-v2.css").read_bytes()
            kind = "text/css"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self.send_error(405)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    os.chdir(ROOT)
    load_env()
    ThreadingHTTPServer(("127.0.0.1", PORT), DashboardV2Handler).serve_forever()


if __name__ == "__main__":
    main()
