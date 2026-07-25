from __future__ import annotations

import subprocess


REQUIRED_IGNORES = [".env", "data/positions.csv", "logs/deliveries.csv", ".cache/naver", ".venv/Scripts/python.exe"]


def validate_ignores() -> list[str]:
    errors = []
    for path in REQUIRED_IGNORES:
        result = subprocess.run(["git", "check-ignore", path], capture_output=True)
        if result.returncode != 0:
            errors.append(f"not ignored: {path}")
    return errors
