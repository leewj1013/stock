from __future__ import annotations

import csv
import json
import math
import os
from datetime import date, datetime, timedelta
from statistics import mean

from .data_store import (
    DB_PATH,
    STRATEGY_VERSION,
    active_strategy_version,
    query_rows,
    recent_recommendation_outcomes,
    save_strategy_version,
    upsert_recommendation_outcomes,
)


FACTORS = ("volume_score", "trading_value_score", "trend_score", "relative_strength_score", "news_score", "disclosure_score", "financial_score")
DEFAULT_WEIGHTS = {factor: 1.0 for factor in FACTORS}
RETURN_WEIGHTS = (("return_1d_pct", 0.2), ("return_3d_pct", 0.3), ("return_5d_pct", 0.3), ("return_10d_pct", 0.2))


def active_weights(path: str = DB_PATH) -> dict[str, float]:
    row = active_strategy_version(path)
    if not row:
        return dict(DEFAULT_WEIGHTS)
    try:
        stored = json.loads(row["weights_json"])
    except (TypeError, ValueError, json.JSONDecodeError):
        return dict(DEFAULT_WEIGHTS)
    return {factor: float(stored.get(factor, 1.0)) for factor in FACTORS}


def adjusted_score(parts: dict[str, float], path: str = DB_PATH) -> float:
    weights = active_weights(path)
    positive = sum(float(parts.get(factor) or 0) * weights[factor] for factor in FACTORS)
    return round(max(0.0, min(100.0, positive - float(parts.get("performance_penalty") or 0))), 2)


def _number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def sync_outcomes(performance_path: str = "logs/recommendation_performance.csv", path: str = DB_PATH) -> int:
    if not os.path.exists(performance_path):
        return 0
    candidates = query_rows("SELECT * FROM candidate_snapshots ORDER BY evaluated_at", path=path)
    snapshots = {(str(row.get("evaluated_at", ""))[:10], row.get("ticker")): row for row in candidates}
    rows = []
    benchmark_cache: dict[tuple[str, int], float | None] = {}
    def benchmark_return(pick_date: str, days: int) -> float | None:
        key = (pick_date, days)
        if key in benchmark_cache:
            return benchmark_cache[key]
        try:
            from .app import naver_rows
            ticker = os.environ.get("LEARNING_BENCHMARK_TICKER", "069500")
            signal_day = date.fromisoformat(pick_date)
            prices = naver_rows(ticker, signal_day, signal_day + timedelta(days=days * 3 + 10))
            future = [row for row in prices if datetime.strptime(str(row[0]), "%Y%m%d").date() > signal_day]
            if len(future) <= days or int(future[0][1]) <= 0:
                benchmark_cache[key] = None
            else:
                benchmark_cache[key] = (int(future[days][4]) - int(future[0][1])) / int(future[0][1]) * 100
        except Exception:
            benchmark_cache[key] = None
        return benchmark_cache[key]
    with open(performance_path, newline="", encoding="utf-8-sig") as file:
        for item in csv.DictReader(file):
            snapshot = snapshots.get((item.get("pick_date"), item.get("ticker")), {})
            factors = {factor: float(snapshot.get(factor) or item.get(factor) or 0) for factor in FACTORS}
            returns = {days: _number(item.get(f"return_{days}d_pct")) for days in (1, 3, 5, 10, 20)}
            excess = {}
            for days, value in returns.items():
                benchmark = benchmark_return(str(item.get("pick_date")), days) if value is not None else None
                excess[days] = value - benchmark if value is not None and benchmark is not None else None
            rows.append({
                "pick_date": item.get("pick_date"), "ticker": item.get("ticker"), "name": item.get("name"),
                "strategy_version": STRATEGY_VERSION, "score": _number(item.get("score")),
                "factors_json": json.dumps(factors, sort_keys=True), "entry_date": item.get("execution_date"),
                "entry_price": _number(item.get("entry_close")),
                **{f"return_{days}d_pct": value for days, value in returns.items()},
                **{f"excess_{days}d_pct": value for days, value in excess.items()},
                "mfe_20d_pct": _number(item.get("mfe_20d_pct")), "mae_20d_pct": _number(item.get("mae_20d_pct")),
                "quality_status": "valid" if item.get("execution_date") else "pending",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            })
    upsert_recommendation_outcomes(rows, path)
    return len(rows)


def objective(row: dict) -> float | None:
    values = []
    for column, weight in RETURN_WEIGHTS:
        excess_column = column.replace("return_", "excess_")
        value = row.get(excess_column) if row.get(excess_column) is not None else row.get(column)
        if value is not None:
            values.append((float(value), weight))
    return sum(value * weight for value, weight in values) / sum(weight for _value, weight in values) if values else None


def _correlation(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    xbar, ybar = mean(xs), mean(ys)
    numerator = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    denominator = math.sqrt(sum((x - xbar) ** 2 for x in xs) * sum((y - ybar) ** 2 for y in ys))
    return numerator / denominator if denominator else 0.0


def _drawdown(values: list[float]) -> float:
    equity, peak, worst = 100.0, 100.0, 0.0
    for value in values:
        equity *= 1 + value / 100
        peak = max(peak, equity)
        worst = min(worst, (equity - peak) / peak * 100)
    return round(worst, 4)


def learn(path: str = DB_PATH, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    rows = list(reversed(recent_recommendation_outcomes(250, path)))
    usable = [(row, objective(row)) for row in rows]
    usable = [(row, value) for row, value in usable if value is not None]
    minimum = int(os.environ.get("LEARNING_MIN_SAMPLES", "100"))
    if len(usable) < minimum:
        return {"status": "insufficient_data", "sample_count": len(usable), "minimum": minimum}
    validation_size = min(20, max(10, len(usable) // 5))
    training, validation = usable[:-validation_size], usable[-validation_size:]
    current = active_weights(path)
    max_change = float(os.environ.get("LEARNING_MAX_DAILY_WEIGHT_CHANGE", "0.05"))
    proposed = {}
    for factor in FACTORS:
        xs = [float(json.loads(row["factors_json"]).get(factor, 0)) for row, _value in training]
        ys = [float(value) for _row, value in training]
        target = max(0.75, min(1.25, 1 + _correlation(xs, ys) * 0.15))
        proposed[factor] = round(max(current[factor] - max_change, min(current[factor] + max_change, target)), 4)
    def ranked_returns(weights):
        scored = []
        for row, value in validation:
            factors = json.loads(row["factors_json"])
            score = sum(float(factors.get(factor, 0)) * weights[factor] for factor in FACTORS)
            scored.append((score, float(value)))
        scored.sort(reverse=True)
        return [value for _score, value in scored[: max(1, len(scored) // 2)]]
    baseline_values, proposed_values = ranked_returns(current), ranked_returns(proposed)
    baseline_return, proposed_return = mean(baseline_values), mean(proposed_values)
    baseline_dd, proposed_dd = _drawdown(baseline_values), _drawdown(proposed_values)
    accepted = proposed_return > baseline_return and proposed_dd >= baseline_dd - 2
    active = active_strategy_version(path)
    if active and active.get("effective_date"):
        active_period = [(row, value) for row, value in usable if str(row.get("pick_date") or "") >= str(active["effective_date"])]
        if len(active_period) >= 20:
            realized = [float(value) for _row, value in active_period]
            if mean(realized) < 0 and _drawdown(realized) < float(active.get("baseline_max_drawdown") or 0) - 2:
                rollback_rows = query_rows("SELECT * FROM learned_strategy_versions WHERE version_id=?", (active.get("rollback_version"),), path)
                rollback_weights = rollback_rows[0]["weights_json"] if rollback_rows else json.dumps(DEFAULT_WEIGHTS, sort_keys=True)
                version_id = f"rollback-{now:%Y%m%d-%H%M%S}"
                save_strategy_version({
                    "version_id": version_id, "created_at": now.isoformat(timespec="seconds"),
                    "effective_date": (now.date() + timedelta(days=1)).isoformat(), "weights_json": rollback_weights,
                    "sample_count": len(usable), "objective_return": round(mean(realized), 4),
                    "baseline_return": float(active.get("baseline_return") or 0), "max_drawdown": _drawdown(realized),
                    "baseline_max_drawdown": float(active.get("baseline_max_drawdown") or 0), "status": "active",
                    "rollback_version": active.get("version_id"), "notes": "automatic performance rollback",
                }, path)
                return {"status": "rolled_back", "version_id": version_id, "sample_count": len(usable), "weights": json.loads(rollback_weights)}
    version_id = f"learned-{now:%Y%m%d-%H%M%S}"
    save_strategy_version({
        "version_id": version_id, "created_at": now.isoformat(timespec="seconds"),
        "effective_date": (now.date() + timedelta(days=1)).isoformat(), "weights_json": json.dumps(proposed, sort_keys=True),
        "sample_count": len(usable), "objective_return": round(proposed_return, 4),
        "baseline_return": round(baseline_return, 4), "max_drawdown": proposed_dd,
        "baseline_max_drawdown": baseline_dd, "status": "active" if accepted else "rejected",
        "rollback_version": active.get("version_id"), "notes": "walk-forward validation",
    }, path)
    return {"status": "promoted" if accepted else "rejected", "version_id": version_id, "sample_count": len(usable), "weights": proposed}


def run() -> dict:
    sync_outcomes()
    result = learn()
    if result.get("status") in {"promoted", "rolled_back"}:
        from .notifier import send_notification
        title = "전략 자동승격" if result["status"] == "promoted" else "전략 자동복귀"
        send_notification(
            f"[{title}]\n버전: {result.get('version_id')}\n표본: {result.get('sample_count')}건\n다음 거래일부터 적용",
            event_type="strategy_change",
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    run()
