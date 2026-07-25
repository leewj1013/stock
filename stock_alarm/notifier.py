from __future__ import annotations

import json
import os
import csv
import hashlib
import urllib.parse
import urllib.request
from datetime import date, datetime
from urllib.error import HTTPError

from .app import refresh_kakao_token


def send_console(message: str) -> None:
    print(message)


def send_telegram(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False

    data = urllib.parse.urlencode({"chat_id": chat_id, "text": message[:4096]}).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()
    return True


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


def send_notification(message: str) -> str:
    notifier = os.environ.get("NOTIFIER", "telegram").lower()
    if os.environ.get("FORCE_SEND", "0") != "1" and was_sent(notifier, message):
        write_delivery_log("skipped_duplicate")
        return "skipped_duplicate"

    sent = False
    if notifier == "telegram":
        sent = send_telegram(message)
    elif notifier == "kakao":
        sent = send_kakao(message)
    elif notifier == "console":
        sent = True

    if not sent:
        send_console(message)
        write_delivery_log("console")
        return "console"
    if notifier == "console":
        send_console(message)
    mark_sent(notifier, message)
    write_delivery_log(notifier)
    return notifier


def write_delivery_log(channel: str, path: str = "logs/deliveries.csv") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        if not exists:
            writer.writerow(["created_at", "channel"])
        writer.writerow([datetime.now().isoformat(timespec="seconds"), channel])


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
