from __future__ import annotations

import os
import re


SKIP_DIRS = {".cache", ".git", ".venv", "__pycache__", "logs"}
SKIP_SUFFIXES = {".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3"}
PATTERNS = [
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b[A-Fa-f0-9]{32}\b"),
]


def scan(root: str = ".") -> list[str]:
    hits: list[str] = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in files:
            path = os.path.join(directory, name)
            if os.path.basename(path) == ".env":
                continue
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
            if any(pattern.search(text) for pattern in PATTERNS):
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
