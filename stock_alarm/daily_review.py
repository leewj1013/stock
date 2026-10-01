"""Weekday after-close review: health, experiments vs their criteria, and the
recommendation evidence so far -- written to reports/daily_review/ (shown on
the dashboard's 전략 연구 tab). Telegram only when something is wrong; the
16:00 closing brief already carries the day's numbers.

Replaces the Claude-run review, which stalled on permission prompts nobody
could see. Read-only against every DB.

    python -m stock_alarm.daily_review
"""
from __future__ import annotations

import os
from datetime import datetime
from math import sqrt
from statistics import mean, stdev

from .app import is_trading_day, load_env, write_error_log

REPORT_DIR = os.path.join("reports", "daily_review")


def health_issues(today: str, path: str | None = None, errors_log: str = os.path.join("logs", "errors.log"), deliveries_log: str = "logs/deliveries.csv") -> list[str]:
    """What looks broken today: failed runs, missing valuations, new errors."""
    from .data_store import DB_PATH, query_rows
    from .trading_profiles import PROFILES
    from .report import tail_csv

    issues = []
    runs = query_rows("SELECT run_type, status, COUNT(*) AS n FROM strategy_runs WHERE started_at LIKE ? GROUP BY 1, 2", (f"{today}%",), path or DB_PATH)
    failed = sum(int(row["n"]) for row in runs if row["status"] == "failed")
    if failed:
        issues.append(f"실패한 실행 {failed}건 (strategy_runs)")
    if not any(row["status"] == "completed" for row in runs):
        issues.append("오늘 완료된 추천·매도 실행이 없음 (strategy_runs)")
    for name, profile in PROFILES.items():
        snapshot = query_rows("SELECT MAX(created_at) AS last FROM virtual_valuation_snapshots", path=profile["db_path"])
        if not snapshot or not str(snapshot[0]["last"] or "").startswith(today):
            issues.append(f"{name} 계좌 오늘 평가 기록 없음")
    try:
        with open(errors_log, encoding="utf-8", errors="ignore") as file:
            errors_today = sum(1 for line in file if line.startswith(f"[{today}"))
    except OSError:
        errors_today = 0
    if errors_today:
        issues.append(f"오늘 오류 로그 {errors_today}건 (logs/errors.log)")
    if not any(
        row.get("created_at", "").startswith(today)
        and row.get("event_type") == "daily_summary"
        and row.get("channel") == "telegram"
        and row.get("status") == "delivered"
        for row in tail_csv(deliveries_log, 10000)
    ):
        issues.append("오늘 마감 브리핑 텔레그램 발송 성공 기록 없음")
    return issues


def evidence_lines() -> list[str]:
    """Recommendation excess return vs KOSPI so far: n, mean, standard error."""
    from .data_store import query_rows

    lines = []
    for horizon in (5, 10, 20):
        values = [float(row["v"]) for row in query_rows(
            f"SELECT excess_{horizon}d_pct AS v FROM recommendation_outcomes WHERE excess_{horizon}d_pct IS NOT NULL")]
        if len(values) >= 2:
            error = stdev(values) / sqrt(len(values))
            lines.append(f"- {horizon}일 초과수익: 평균 {mean(values):+.2f}% ± {error:.2f} (n={len(values)})")
        else:
            lines.append(f"- {horizon}일 초과수익: 표본 부족 (n={len(values)})")
    return lines


def report(now: datetime | None = None) -> tuple[str, list[str]]:
    from .dashboard import experiment_account_rows, experiment_progress_rows

    now = now or datetime.now()
    today = now.date().isoformat()
    issues = health_issues(today)
    lines = [f"# 매일 점검 · {today}", "", "## 상태"]
    lines += [f"- ⚠ {issue}" for issue in issues] or ["- 정상 (실행·평가 기록·오류 로그 확인)"]
    lines += ["", "## 계좌 (시작 이후)", "| 실험 | 총수익률% | 최대낙폭% | 샤프 | 하락장% | 현금 |", "|---|---|---|---|---|---|"]
    for row in experiment_account_rows():
        lines.append(f"| {row['experiment']} | {row['total_return_pct'] or '-'} | {row['mdd_pct'] or '-'} | "
                     f"{row['sharpe'] or '표본 부족'} | {row['bear_return_pct'] or '-'} | {row['cash_pct']} |")
    lines += ["", "## 실험 진행"]
    lines += [f"- {row['experiment']}: {row['progress']} ({row['risk_state']})" for row in experiment_progress_rows()]
    try:
        from .shadow_trader import shadow_portfolio
        shadow = shadow_portfolio()
    except Exception:
        shadow = {}
    if shadow:
        virtual = f"{shadow['virtual_return_pct']:+.2f}%" if shadow.get("virtual_return_pct") is not None else "-"
        lines.append(f"- 섀도 vs 가상계좌 ({shadow['since']} 이후): 섀도 {shadow['return_pct']:+.2f}% / 가상 {virtual}")
    lines += ["", "## 추천 근거 (KOSPI 대비)"] + evidence_lines()
    lines += ["", "하루치 수치는 근거가 아닙니다. 판단은 사전 등록 검정과 월간 돌아보기로 합니다."]
    return "\n".join(lines) + "\n", issues


def run() -> str:
    load_env()
    if not is_trading_day():
        return "market_closed"
    text, issues = report()
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"{datetime.now().date().isoformat()}.md")
    with open(path, "w", encoding="utf-8") as file:
        file.write(text)
    if issues:
        from .notifier import send_notification
        send_notification("[매일 점검 이상]\n" + "\n".join(f"- {issue}" for issue in issues), event_type="daily_review")
    print(f"daily_review written {path} issues={len(issues)}")
    return path


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
