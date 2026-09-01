from __future__ import annotations

import csv
import json
import math
import os
from datetime import date, datetime, timedelta
from statistics import mean

from .app import load_env
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
    return score_with_weights(parts, active_weights(path))


def score_with_weights(parts: dict[str, float], weights: dict[str, float] | None = None) -> float:
    weights = weights or DEFAULT_WEIGHTS
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
            prices = naver_rows(ticker, signal_day, min(signal_day + timedelta(days=days * 3 + 10), date.today()))
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


def return_distribution_p_value(proposed: list[float], baseline: list[float]) -> float:
    """One-sided Mann-Whitney U test for proposed returns being higher.

    This dependency-free non-parametric test is intentionally conservative for
    the paper-trading promotion gate. Ties use average ranks and tie correction.
    """
    if not proposed or not baseline:
        return 1.0
    tagged = [(float(value), 0) for value in proposed] + [(float(value), 1) for value in baseline]
    tagged.sort(key=lambda item: item[0])
    rank_sum = 0.0
    tie_sizes = []
    index = 0
    while index < len(tagged):
        end = index + 1
        while end < len(tagged) and tagged[end][0] == tagged[index][0]:
            end += 1
        average_rank = (index + 1 + end) / 2
        rank_sum += average_rank * sum(group == 0 for _value, group in tagged[index:end])
        tie_sizes.append(end - index)
        index = end
    n1, n2 = len(proposed), len(baseline)
    u = rank_sum - n1 * (n1 + 1) / 2
    total = n1 + n2
    tie_term = sum(size ** 3 - size for size in tie_sizes)
    variance = n1 * n2 / 12 * ((total + 1) - tie_term / max(total * (total - 1), 1))
    if variance <= 0:
        return 1.0
    z = (u - n1 * n2 / 2 - 0.5) / math.sqrt(variance)
    return round(max(0.0, min(1.0, 0.5 * math.erfc(z / math.sqrt(2)))), 8)


def _proposed_weights(training: list[tuple[dict, float]], current: dict[str, float], max_change: float) -> dict[str, float]:
    proposed = {}
    for factor in FACTORS:
        xs = [float(json.loads(row["factors_json"]).get(factor, 0)) for row, _value in training]
        ys = [float(value) for _row, value in training]
        target = max(0.75, min(1.25, 1 + _correlation(xs, ys) * 0.15))
        proposed[factor] = round(max(current[factor] - max_change, min(current[factor] + max_change, target)), 4)
    return proposed


def _ranked_returns(validation: list[tuple[dict, float]], weights: dict[str, float]) -> list[float]:
    scored = []
    for row, value in validation:
        factors = json.loads(row["factors_json"])
        score = sum(float(factors.get(factor, 0)) * weights[factor] for factor in FACTORS)
        scored.append((score, str(row.get("pick_date") or ""), str(row.get("ticker") or ""), float(value)))
    scored.sort(reverse=True)
    return [value for _score, _date, _ticker, value in scored[: max(1, len(scored) // 2)]]


def _fold_summary(index: int, baseline_values: list[float], proposed_values: list[float], alpha: float, validation_count: int | None = None) -> dict:
    baseline_return, proposed_return = mean(baseline_values), mean(proposed_values)
    baseline_dd, proposed_dd = _drawdown(baseline_values), _drawdown(proposed_values)
    p_value = return_distribution_p_value(proposed_values, baseline_values)
    improved = proposed_return > baseline_return
    drawdown_ok = proposed_dd >= baseline_dd - 2
    significant = p_value < alpha
    wins = [value for value in proposed_values if value > 0]
    losses = [value for value in proposed_values if value < 0]
    profit_factor = sum(wins) / abs(sum(losses)) if losses else None
    return {
        "fold": index,
        "sample_count": validation_count if validation_count is not None else len(proposed_values),
        "selected_count": len(proposed_values),
        "baseline_return": round(baseline_return, 4),
        "proposed_return": round(proposed_return, 4),
        "baseline_mdd": baseline_dd,
        "proposed_mdd": proposed_dd,
        "p_value": p_value,
        "win_rate_pct": round(len(wins) / len(proposed_values) * 100, 2) if proposed_values else 0.0,
        "profit_factor": None if profit_factor is None else round(profit_factor, 4),
        "passed": improved and drawdown_ok and significant,
        "checks": {"return_improved": improved, "mdd_within_2pp": drawdown_ok, "significant": significant},
    }


def walk_forward_validate(
    usable: list[tuple[dict, float]],
    current: dict[str, float] | None = None,
    minimum: int = 300,
    validation_size: int = 60,
    fold_count: int = 3,
    alpha: float = 0.05,
    max_change: float = 0.05,
) -> dict:
    minimum = max(300, int(minimum))
    validation_size = max(60, int(validation_size))
    fold_count = max(3, int(fold_count))
    required = max(minimum, validation_size * fold_count + 1)
    if len(usable) < required:
        return {
            "status": "insufficient_data", "sample_count": len(usable), "minimum": required,
            "folds": [], "p_value": None,
            "decision_reason": f"유효 표본 부족: {len(usable)}/{required}건; 기존 가중치 유지",
        }
    current = current or dict(DEFAULT_WEIGHTS)
    validation_start = len(usable) - validation_size * fold_count
    folds = []
    all_baseline, all_proposed = [], []
    for fold_index in range(fold_count):
        start = validation_start + fold_index * validation_size
        stop = start + validation_size
        training, validation = usable[:start], usable[start:stop]
        fold_weights = _proposed_weights(training, current, max_change)
        baseline_values = _ranked_returns(validation, current)
        proposed_values = _ranked_returns(validation, fold_weights)
        folds.append(_fold_summary(fold_index + 1, baseline_values, proposed_values, alpha, len(validation)))
        all_baseline.extend(baseline_values)
        all_proposed.extend(proposed_values)
    proposed = _proposed_weights(usable, current, max_change)
    baseline_values, proposed_values = all_baseline, all_proposed
    baseline_return, proposed_return = mean(baseline_values), mean(proposed_values)
    baseline_dd, proposed_dd = _drawdown(baseline_values), _drawdown(proposed_values)
    p_value = return_distribution_p_value(proposed_values, baseline_values)
    accepted = all(fold["passed"] for fold in folds) and p_value < alpha
    failed_checks = []
    if not all(fold["passed"] for fold in folds):
        failed_checks.append("one_or_more_walk_forward_folds_failed")
    if p_value >= alpha:
        failed_checks.append("aggregate_significance_failed")
    decision_reason = "all_walk_forward_folds_improved_and_significant" if accepted else ",".join(failed_checks)
    return {
        "status": "promoted" if accepted else "rejected",
        "sample_count": len(usable), "weights": proposed, "folds": folds,
        "p_value": p_value, "decision_reason": decision_reason,
        "objective_return": round(proposed_return, 4), "baseline_return": round(baseline_return, 4),
        "max_drawdown": proposed_dd, "baseline_max_drawdown": baseline_dd,
    }


def learn(path: str = DB_PATH, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    minimum = max(300, int(os.environ.get("LEARNING_MIN_SAMPLES", "300")))
    validation_size = max(60, int(os.environ.get("LEARNING_VALIDATION_MIN_SAMPLES", "60")))
    fold_count = max(3, int(os.environ.get("LEARNING_VALIDATION_FOLDS", "3")))
    maximum = max(minimum, int(os.environ.get("LEARNING_MAX_SAMPLES", "1000")))
    alpha = float(os.environ.get("LEARNING_SIGNIFICANCE_LEVEL", "0.05"))
    rows = list(reversed(recent_recommendation_outcomes(maximum, path)))
    usable = [(row, objective(row)) for row in rows]
    usable = [(row, value) for row, value in usable if value is not None]
    current = active_weights(path)
    validation = walk_forward_validate(
        usable, current, minimum, validation_size, fold_count, alpha,
        float(os.environ.get("LEARNING_MAX_DAILY_WEIGHT_CHANGE", "0.05")),
    )
    if validation["status"] == "insufficient_data":
        return validation
    proposed = validation["weights"]
    folds = validation["folds"]
    p_value = validation["p_value"]
    accepted = validation["status"] == "promoted"
    proposed_return = validation["objective_return"]
    baseline_return = validation["baseline_return"]
    proposed_dd = validation["max_drawdown"]
    baseline_dd = validation["baseline_max_drawdown"]
    decision_reason = validation["decision_reason"]
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
                    "validation_json": json.dumps(folds, ensure_ascii=False), "p_value": p_value,
                    "decision_reason": "active_strategy_negative_return_and_drawdown_breach",
                }, path)
                return {"status": "rolled_back", "version_id": version_id, "sample_count": len(usable), "weights": json.loads(rollback_weights), "folds": folds, "p_value": p_value, "decision_reason": "active_strategy_negative_return_and_drawdown_breach"}
    version_id = f"learned-{now:%Y%m%d-%H%M%S}"
    save_strategy_version({
        "version_id": version_id, "created_at": now.isoformat(timespec="seconds"),
        "effective_date": (now.date() + timedelta(days=1)).isoformat(), "weights_json": json.dumps(proposed, sort_keys=True),
        "sample_count": len(usable), "objective_return": round(proposed_return, 4),
        "baseline_return": round(baseline_return, 4), "max_drawdown": proposed_dd,
        "baseline_max_drawdown": baseline_dd, "status": "active" if accepted else "rejected",
        "rollback_version": active.get("version_id"), "notes": f"{fold_count}-fold walk-forward validation",
        "validation_json": json.dumps(folds, ensure_ascii=False), "p_value": p_value,
        "decision_reason": decision_reason,
    }, path)
    return {"status": "promoted" if accepted else "rejected", "version_id": version_id, "sample_count": len(usable), "weights": proposed, "folds": folds, "p_value": p_value, "decision_reason": decision_reason}


def decision_message(result: dict) -> str:
    titles = {"promoted": "전략 자동승격", "rejected": "전략 승격거절", "rolled_back": "전략 자동복귀", "insufficient_data": "전략 학습보류"}
    lines = [f"[{titles.get(result.get('status'), '전략 학습결과')}]", f"표본: {result.get('sample_count', 0)}건"]
    for fold in result.get("folds") or []:
        lines.append(
            f"구간{fold['fold']}: 기존 {fold['baseline_return']:+.2f}% → 신규 {fold['proposed_return']:+.2f}% "
            f"· MDD {fold['proposed_mdd']:.2f}% · p={fold['p_value']:.4f} · {'통과' if fold['passed'] else '실패'}"
        )
    p_value = result.get("p_value")
    lines.append(f"통합 p-value: {p_value:.4f}" if p_value is not None else "통합 p-value: N/A")
    lines.append(f"판단: {result.get('decision_reason', '')}")
    if result.get("status") in {"promoted", "rolled_back"}:
        lines.append("다음 거래일부터 적용")
    return "\n".join(lines)


def run() -> dict:
    load_env()
    sync_outcomes()
    result = learn()
    from .notifier import send_notification
    send_notification(decision_message(result), event_type="strategy_change")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    run()
