from __future__ import annotations

import json
import os
import csv
import hashlib
import urllib.parse
import urllib.request
from datetime import date, datetime
from urllib.error import HTTPError, URLError

from .app import refresh_kakao_token


TELEGRAM_SAFE_LENGTH = 3500


def send_console(message: str) -> None:
    print(message)


def split_telegram_message(message: str, limit: int = TELEGRAM_SAFE_LENGTH) -> list[str]:
    """Split on line boundaries and hard-split a single oversized line."""
    if not message:
        return [""]
    chunks: list[str] = []
    current = ""
    for line in message.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current.rstrip("\n"))
                current = ""
            chunks.append(line[:limit].rstrip("\n"))
            line = line[limit:]
        if current and len(current) + len(line) > limit:
            chunks.append(current.rstrip("\n"))
            current = ""
        current += line
    if current or not chunks:
        chunks.append(current.rstrip("\n"))
    return chunks


def send_telegram(message: str) -> dict[str, str] | bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False

    chunks = split_telegram_message(message)
    receipts = []
    delivered_chat_id = ""
    for index, chunk in enumerate(chunks, 1):
        prefix = f"[{index}/{len(chunks)}]\n" if len(chunks) > 1 else ""
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": prefix + chunk}).encode()
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode())
        result = payload.get("result") or {}
        delivered_chat_id = str((result.get("chat") or {}).get("id", ""))
        if not payload.get("ok") or not result.get("message_id"):
            raise RuntimeError(f"Telegram rejected sendMessage: {payload.get('description', 'missing message_id')}")
        if delivered_chat_id != str(chat_id):
            raise RuntimeError("Telegram response chat_id did not match configured chat_id")
        receipts.append(str(result["message_id"]))
    return {
        "message_id": "|".join(receipts),
        "chat_id_suffix": delivered_chat_id[-4:],
    }


def telegram_get_me() -> dict:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/getMe", method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def send_kakao(message: str) -> bool:
    token = os.environ.get("KAKAO_ACCESS_TOKEN", "")
    if not token:
        return False

    payload = {
        "template_object": json.dumps(
            {
                "object_type": "text",
                "text": message[:1000],
                "link": {"web_url": "https://finance.naver.com", "mobile_web_url": "https://m.stock.naver.com"},
            },
            ensure_ascii=False,
        )
    }
    data = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except HTTPError as error:
        if error.code != 401 or not refresh_kakao_token():
            raise
        return send_kakao(message)
    return True


def send_notification(message: str, event_type: str = "", tickers: list[str] | None = None) -> str:
    tickers = list(dict.fromkeys(ticker.strip() for ticker in (tickers or []) if ticker.strip()))
    notifier = os.environ.get("NOTIFIER", "telegram").lower()
    if os.environ.get("FORCE_SEND", "0") != "1" and was_sent(notifier, message):
        write_delivery_log("skipped_duplicate", event_type=event_type, tickers=tickers)
        return "skipped_duplicate"

    sent = False
    receipt = {}
    error_message = ""
    try:
        if notifier == "telegram":
            sent = send_telegram(message)
            receipt = sent if isinstance(sent, dict) else {}
        elif notifier == "kakao":
            sent = send_kakao(message)
        elif notifier == "console":
            sent = True
    except (HTTPError, URLError, OSError, RuntimeError, json.JSONDecodeError) as error:
        sent = False
        error_message = f"{type(error).__name__}: {error}"

    if not sent:
        send_console(message)
        write_delivery_log("console", status="fallback", error=error_message or "notifier returned false", event_type=event_type, tickers=tickers)
        return "console"
    if notifier == "console":
        send_console(message)
    mark_sent(notifier, message)
    write_delivery_log(
        notifier,
        status="delivered",
        message_id=receipt.get("message_id", ""),
        chat_id_suffix=receipt.get("chat_id_suffix", ""),
        event_type=event_type,
        tickers=tickers,
    )
    return notifier


def write_delivery_log(
    channel: str,
    path: str = "logs/deliveries.csv",
    status: str = "",
    message_id: str = "",
    chat_id_suffix: str = "",
    error: str = "",
    event_type: str = "",
    tickers: list[str] | None = None,
) -> None:
    from .csv_schema import ensure_header

    header = ["created_at", "channel", "status", "message_id", "chat_id_suffix", "error", "event_type", "item_count", "tickers"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ensure_header(path, header)
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        if not exists:
            writer.writerow(header)
        unique_tickers = list(dict.fromkeys(ticker.strip() for ticker in (tickers or []) if ticker.strip()))
        writer.writerow([datetime.now().isoformat(timespec="seconds"), channel, status, message_id, chat_id_suffix, error, event_type, len(unique_tickers), "|".join(unique_tickers)])


def message_key(channel: str, message: str) -> str:
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
    return f"{date.today().isoformat()}:{channel}:{digest}"


def was_sent(channel: str, message: str, path: str = "logs/sent_keys.csv") -> bool:
    key = message_key(channel, message)
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as file:
            if any(row and row[0] == key for row in csv.reader(file)):
                return True
    return False


def mark_sent(channel: str, message: str, path: str = "logs/sent_keys.csv") -> None:
    key = message_key(channel, message)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if was_sent(channel, message, path):
        return
    with open(path, "a", newline="", encoding="utf-8-sig") as file:
        csv.writer(file).writerow([key, datetime.now().isoformat(timespec="seconds")])
