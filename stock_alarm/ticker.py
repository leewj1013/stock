from __future__ import annotations

import re


def is_valid_kr_ticker(value: str) -> bool:
    """Accept six-character KRX symbols, including newer alphanumeric codes."""
    return bool(re.fullmatch(r"[0-9A-Z]{6}", (value or "").strip().upper()))
