from __future__ import annotations

import os
import re


SKIP_DIRS = {".cache", ".git", ".venv", "__pycache__"}
SKIP_SUFFIXES = {".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3"}
PATTERNS = [
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(?:secret|token|api[_-]?key)\s*[:=]\s*[A-Fa-f0-9]{32,}\b"),
]
SENSITIVE_KEYS = {
    "ALPHA_VANTAGE_API_KEY", "DART_API_KEY", "DASHBOARD_LOCAL_TOKEN", "DASHBOARD_REMOTE_TOKEN",
    "DASHBOARD_LOCAL_USERNAME", "DASHBOARD_LOCAL_PASSWORD_HASH",
    "KAKAO_ACCESS_TOKEN", "KAKAO_REFRESH_TOKEN", "KRX_API_KEY", "KRX_ID", "KRX_PW",
    "KAKAO_JAVASCRIPT_KEY", "KAKAO_NATIVE_APP_KEY", "KAKAO_REST_API_KEY",
    "NAVER_ACCESS_KEY_ID", "NAVER_HUB_CLIENT_ID", "NAVER_HUB_CLIENT_SECRET", "NAVER_SECRET_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET",
}
ASSIGNMENT = re.compile(r"(?m)^[ \t]*([A-Z][A-Z0-9_]+)[ \t]*=[ \t]*([^ \t\r\n#]*)")


def _contains_sensitive_assignment(text: str) -> bool:
    placeholders = {"", "changeme", "change-me", "example", "placeholder", "your-value", "xxx"}
    for key, raw_value in ASSIGNMENT.findall(text):
        value = raw_value.strip().strip("'\"")
        if key in SENSITIVE_KEYS and value.lower() not in placeholders and not value.startswith(("${", "<")):
            return True
    return False


def scan(root: str = ".") -> list[str]:
    hits: list[str] = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            path = os.path.join(directory, name)
            if any(name.lower().endswith(suffix) for suffix in SKIP_SUFFIXES):
                continue
            try:
                with open(path, "rb") as file:
                    content = file.read()
            except OSError:
                continue
            if b"\x00" in content[:4096]:
                continue
            text = content.decode("utf-8", errors="ignore")
            if _contains_sensitive_assignment(text) or any(pattern.search(text) for pattern in PATTERNS):
                hits.append(path)
    return hits


def main() -> int:
    hits = scan()
    if hits:
        print("secret check ok=False")
        print("\n".join(hits))
        return 1
    print("secret check ok=True")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
