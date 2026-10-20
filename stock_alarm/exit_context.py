"""Observation only: what the market looked like when a sell/take-profit fired (gap, pre-market, previous evening).

Nothing here changes a sell decision. It feeds the alert text and logs/sell_exit_events.csv so gap-up behaviour can be
studied later (the backtest engine only sees daily closes). Every lookup is best-effort and must never block a sell run.
"""
from __future__ import annotations

import csv
import os
from datetime import date, datetime
from pathlib import Path

EVENT_LOG = "logs/sell_exit_events.csv"
GAP_NOTE_PCT = 3.0  # |open vs previous close| from which the alert text mentions the gap
MAX_CANDLE_PAGES = 5  # 200 one-minute candles each: from a 09:07 check this reaches back past the previous evening
FIELDS = ["logged_at", "profile", "ticker", "name", "stage", "sale_type", "reason", "holding_days", "entry_price",
          "prev_close", "open_price", "price_at_check", "return_pct", "open_return_pct", "gap_pct",
          "preopen_price", "premarket_high", "prev_evening_price", "prev_evening_time", "note"]


def gap_fields(rows: list[list], day: date) -> tuple[int, int]:
    """(previous close, today's open) from daily rows, or (0, 0) while today's row is not published yet."""
    if len(rows) < 2 or str(rows[-1][0]) != day.strftime("%Y%m%d"):
        return 0, 0
    return int(rows[-2][4]), int(rows[-1][1])


def gap_pct(prev_close: int, open_price: int) -> float | None:
    return (open_price / prev_close - 1) * 100 if prev_close > 0 and open_price > 0 else None


def open_return_pct(entry_price: int, open_price: int) -> float | None:
    return (open_price / entry_price - 1) * 100 if entry_price > 0 and open_price > 0 else None


def _ts(candle: dict) -> str:
    return str(candle.get("timestamp") or "")


def extended_context(ticker: str, day: date, candle_fn=None) -> dict:
    """Pre-market (before 09:00, NXT) and previous-evening last traded prices from Toss one-minute candles.

    candle_fn(symbol, interval, count, before) -> {"candles": [...newest first], "nextBefore": ...}.
    """
    if candle_fn is None:
        from .toss_client import shared_client
        client = shared_client()
        candle_fn = lambda symbol, interval, count, before: client.candles(symbol, interval=interval, count=count, before=before)
    today = day.isoformat()
    candles, before = [], None
    for _ in range(MAX_CANDLE_PAGES):
        result = candle_fn(ticker, "1m", 200, before) or {}
        page = result.get("candles") or []
        candles += page
        before = result.get("nextBefore")
        traded_before_today = [c for c in candles if _ts(c) < today and float(c.get("volume") or 0) > 0]
        if not page or not before or traded_before_today:
            break
    traded = lambda c: float(c.get("volume") or 0) > 0
    pre = sorted((c for c in candles if _ts(c)[:10] == today and _ts(c)[11:16] < "09:00" and traded(c)), key=_ts)
    evening = sorted((c for c in candles if _ts(c)[:10] < today and traded(c)), key=_ts)
    return {
        "preopen_price": int(float(pre[-1]["closePrice"])) if pre else "",
        "premarket_high": int(max(float(c["highPrice"]) for c in pre)) if pre else "",
        "prev_evening_price": int(float(evening[-1]["closePrice"])) if evening else "",
        "prev_evening_time": _ts(evening[-1])[:16].replace("T", " ") if evening else "",
    }


def profile_label(sell_alerts_log: str) -> str:
    stem = Path(sell_alerts_log).stem.replace("sell_alerts", "").strip("_")
    return stem or "aggressive"


def wants_extended(alert, profile: str) -> bool:
    """Take-profit or gap events of the primary account; the comparison accounts mirror it, so they skip the extra API calls."""
    gap = gap_pct(alert.prev_close, alert.open_price)
    return profile == "aggressive" and (str(alert.stage).startswith("take_profit") or (gap is not None and abs(gap) >= GAP_NOTE_PCT))


def append_events(rows: list[dict], path: str | None = None) -> None:
    path = path or EVENT_LOG  # resolved per call so tests can redirect the module constant
    exists = os.path.exists(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def record_exit_events(alerts: list, profile: str, day: date, path: str | None = None, candle_fn=None) -> int:
    """Append one row per held-position sell alert; best-effort, returns how many rows were written."""
    rows = []
    for alert in alerts:
        gap = gap_pct(alert.prev_close, alert.open_price)
        open_ret = open_return_pct(alert.entry_price, alert.open_price)
        row = {"logged_at": datetime.now().isoformat(timespec="seconds"), "profile": profile, "ticker": alert.ticker, "name": alert.name,
               "stage": alert.stage, "sale_type": alert.sale_type, "reason": alert.reason, "holding_days": alert.holding_days,
               "entry_price": alert.entry_price, "prev_close": alert.prev_close or "", "open_price": alert.open_price or "",
               "price_at_check": alert.close, "return_pct": f"{alert.return_pct:.2f}",
               "open_return_pct": "" if open_ret is None else f"{open_ret:.2f}", "gap_pct": "" if gap is None else f"{gap:.2f}", "note": ""}
        if wants_extended(alert, profile):
            try:
                row.update(extended_context(alert.ticker, day, candle_fn))
            except Exception as error:  # extended-hours data is a bonus, never a reason to lose the event row
                row["note"] = f"extended lookup failed: {type(error).__name__}"
        rows.append(row)
    if rows:
        append_events(rows, path)
    return len(rows)
