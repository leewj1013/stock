from __future__ import annotations

import os
from datetime import datetime, timedelta

from .data_store import DB_PATH, latest_portfolio_risk, query_rows, record_portfolio_risk


def _limit(name: str, default: float) -> float:
    return abs(float(os.environ.get(name, str(default))))


def evaluate_risk_state(
    state: dict,
    daily_start_equity: int | float,
    weekly_start_equity: int | float,
    high_water: int | float,
    previous_status: str = "",
    created_at: datetime | None = None,
    exposure_limit_pct: float | None = None,
) -> dict:
    """Evaluate the live portfolio halt rules without persistence.

    Both the live DB snapshot and isolated backtests call this function so the
    four trigger thresholds cannot drift apart.  Callers own the definition of
    daily/weekly starting equity; the live wrapper reads those values from DB,
    while a daily backtest supplies the prior close and prior-week close.
    """
    created_at = created_at or datetime.now()
    equity = int(state.get("total_equity") or 0)
    daily_start = int(daily_start_equity or equity)
    weekly_start = int(weekly_start_equity or equity)
    peak = max(equity, int(high_water or 0))
    daily_return = (equity - daily_start) / daily_start * 100 if daily_start else 0.0
    weekly_return = (equity - weekly_start) / weekly_start * 100 if weekly_start else 0.0
    drawdown = (equity - peak) / peak * 100 if peak else 0.0
    reasons = []
    if daily_return <= -_limit("RISK_DAILY_LOSS_PCT", 2):
        reasons.append("daily_loss_limit")
    if weekly_return <= -_limit("RISK_WEEKLY_LOSS_PCT", 5):
        reasons.append("weekly_loss_limit")
    if drawdown <= -_limit("RISK_MAX_DRAWDOWN_PCT", 10):
        reasons.append("drawdown_limit")
    exposure = float(state.get("holdings_value") or 0) / equity * 100 if equity else 0.0
    exposure_limit = _limit("RISK_MAX_EXPOSURE_PCT", 70) if exposure_limit_pct is None else abs(exposure_limit_pct)
    if exposure > exposure_limit:
        reasons.append("exposure_limit")
    status = "halted" if reasons else "active"
    row = {
        "created_at": created_at.isoformat(timespec="seconds"), "equity": equity, "high_water": peak,
        "daily_start_equity": daily_start, "weekly_start_equity": weekly_start,
        "daily_return_pct": round(daily_return, 4), "weekly_return_pct": round(weekly_return, 4),
        "drawdown_pct": round(drawdown, 4), "exposure_pct": round(exposure, 4),
        "status": status, "reason": ",".join(reasons),
    }
    if status == "halted" and previous_status != "halted":
        row["transition"] = "halted"
    elif status == "active" and previous_status == "halted":
        row["transition"] = "resumed"
    else:
        row["transition"] = ""
    return row


def snapshot(state: dict, path: str = DB_PATH, now: datetime | None = None, exposure_limit_pct: float | None = None) -> dict:
    """Record risk state without liquidating holdings.

    A halted state intentionally blocks only new buys. Existing positions remain
    open and continue through take-profit and individual sell-condition checks.
    """
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
    row = evaluate_risk_state(
        state, daily_start, weekly_start, int(prior.get("high_water") or equity),
        str(prior.get("status") or ""), now, exposure_limit_pct,
    )
    record_portfolio_risk(row, path)
    return row


def drawdown_episode_days(path: str = DB_PATH) -> int:
    """Distinct snapshot days in the current unbroken run of drawdown_limit halts."""
    days: set[str] = set()
    rows = query_rows("SELECT created_at, reason FROM portfolio_risk_snapshots ORDER BY snapshot_id DESC LIMIT 5000", path=path)
    for row in rows:
        if "drawdown_limit" not in str(row.get("reason") or ""):
            break
        days.add(str(row.get("created_at") or "")[:10])
    return len(days)


def buy_allocation_scale(path: str = DB_PATH, release_policy: dict | None = None) -> tuple[float, str]:
    """New-buy permission as a size multiplier (1.0 open, 0.0 blocked).

    With release_policy {"mode": "cooldown", "cooldown_days": N, "reentry_scale": s},
    a halt caused only by drawdown_limit re-opens buys at size s once it has
    lasted N snapshot days -- the live counterpart of
    risk_release_policy.ExperimentalRiskController's cooldown mode. A drawdown
    halt otherwise releases only by recovering to 90% of a never-decaying peak,
    which an all-cash account can't do. Daily/weekly/exposure halts still block.
    """
    risk = latest_portfolio_risk(path)
    if risk.get("status") != "halted":
        return 1.0, ""
    reason = str(risk.get("reason") or "portfolio_risk_limit")
    policy = release_policy or {}
    if policy.get("mode") != "cooldown" or set(filter(None, reason.split(","))) != {"drawdown_limit"}:
        return 0.0, reason
    if drawdown_episode_days(path) >= int(policy.get("cooldown_days", 20)):
        return float(policy.get("reentry_scale", 0.3)), "drawdown_cooldown_reentry"
    return 0.0, reason


def new_buys_allowed(path: str = DB_PATH, release_policy: dict | None = None) -> tuple[bool, str]:
    scale, reason = buy_allocation_scale(path, release_policy)
    return scale > 0, reason
