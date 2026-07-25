from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from .app import load_env, save_env_value


class CallbackHandler(BaseHTTPRequestHandler):
    code = ""

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        CallbackHandler.code = query.get("code", [""])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write("Kakao authorization complete. You can close this tab.".encode())

    def log_message(self, *_: object) -> None:
        return


def auth_url(rest_api_key: str, redirect_uri: str) -> str:
    return "https://kauth.kakao.com/oauth/authorize?" + urllib.parse.urlencode(
        {
            "client_id": rest_api_key,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "talk_message",
        }
    )


def exchange_code(rest_api_key: str, redirect_uri: str, code: str) -> dict:
    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "client_id": rest_api_key,
            "redirect_uri": redirect_uri,
            "code": code,
        }
    ).encode()
    request = urllib.request.Request("https://kauth.kakao.com/oauth/token", data=data, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def save_tokens(body: dict) -> None:
    save_env_value("KAKAO_ACCESS_TOKEN", body["access_token"])
    save_env_value("KAKAO_REFRESH_TOKEN", body["refresh_token"])


def wait_for_local_code() -> str:
    CallbackHandler.code = ""
    server = HTTPServer(("127.0.0.1", 8080), CallbackHandler)
    server.handle_request()
    if not CallbackHandler.code:
        raise RuntimeError("Authorization code was not received.")
    return CallbackHandler.code


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", help="authorization code copied from Kakao redirect URL")
    parser.add_argument("--print-url", action="store_true", help="only print the Kakao authorization URL")
    args = parser.parse_args()

    load_env()
    rest_api_key = os.environ["KAKAO_REST_API_KEY"]
    redirect_uri = os.environ.get("KAKAO_REDIRECT_URI", "http://127.0.0.1:8080/oauth")
    url = auth_url(rest_api_key, redirect_uri)

    print("Open this URL and approve Kakao Talk message permission:")
    print(url)
    if args.print_url:
        return

    code = args.code or wait_for_local_code()
    save_tokens(exchange_code(rest_api_key, redirect_uri, code))
    print("Saved KAKAO_ACCESS_TOKEN and KAKAO_REFRESH_TOKEN to .env")


if __name__ == "__main__":
    main()
