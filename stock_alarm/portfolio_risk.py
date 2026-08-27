from __future__ import annotations

import os
from datetime import datetime, timedelta

from .data_store import DB_PATH, latest_portfolio_risk, query_rows, record_portfolio_risk


def _limit(name: str, default: float) -> float:
    return abs(float(os.environ.get(name, str(default))))


def snapshot(state: dict, path: str = DB_PATH, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    equity = int(state.get("total_equity") or 0)
    today = now.date().isoformat()
    week_start = (now.date() - timedelta(days=now.weekday())).isoformat()
    prior = latest_portfolio_risk(path)
    today_rows = query_rows("SELECT equity FROM portfolio_risk_snapshots WHERE created_at >= ? ORDER BY snapshot_id LIMIT 1", (today,), path)
    week_rows = query_rows("SELECT equity FROM portfolio_risk_snapshots WHERE created_at >= ? ORDER BY snapshot_id LIMIT 1", (week_start,), path)
    daily_start = int(today_rows[0]["equity"]) if today_rows else int(prior.get("equity") or equity)
    before_week = query_rows("SELECT equity FROM portfolio_risk_snapshots WHERE created_at < ? ORDER BY snapshot_id DESC LIMIT 1", (week_start,), path)
    weekly_start = int(week_rows[0]["equity"]) if week_rows else int(before_week[0]["equity"]) if before_week else equity
    high_water = max(equity, int(prior.get("high_water") or 0))
    daily_return = (equity - daily_start) / daily_start * 100 if daily_start else 0.0
    weekly_return = (equity - weekly_start) / weekly_start * 100 if weekly_start else 0.0
    drawdown = (equity - high_water) / high_water * 100 if high_water else 0.0
    reasons = []
    if daily_return <= -_limit("RISK_DAILY_LOSS_PCT", 2):
        reasons.append("daily_loss_limit")
    if weekly_return <= -_limit("RISK_WEEKLY_LOSS_PCT", 5):
        reasons.append("weekly_loss_limit")
    if drawdown <= -_limit("RISK_MAX_DRAWDOWN_PCT", 10):
        reasons.append("drawdown_limit")
    exposure = float(state.get("holdings_value") or 0) / equity * 100 if equity else 0.0
    if exposure > _limit("RISK_MAX_EXPOSURE_PCT", 70):
        reasons.append("exposure_limit")
    row = {
        "created_at": now.isoformat(timespec="seconds"), "equity": equity, "high_water": high_water,
        "daily_start_equity": daily_start, "weekly_start_equity": weekly_start,
        "daily_return_pct": round(daily_return, 4), "weekly_return_pct": round(weekly_return, 4),
        "drawdown_pct": round(drawdown, 4), "exposure_pct": round(exposure, 4),
        "status": "halted" if reasons else "active", "reason": ",".join(reasons),
    }
    record_portfolio_risk(row, path)
    row["transition"] = "halted" if row["status"] == "halted" and prior.get("status") != "halted" else ""
    return row


def new_buys_allowed(path: str = DB_PATH) -> tuple[bool, str]:
    risk = latest_portfolio_risk(path)
    if risk.get("status") == "halted":
        return False, str(risk.get("reason") or "portfolio_risk_limit")
    return True, ""
