from __future__ import annotations

import html
import os
from datetime import datetime
from statistics import mean

from .app import load_env, performance_penalty, write_error_log
from .daily_check import lines as daily_check_lines, run_log_statuses
from .health import lines as health_lines
from .positions_check import position_count
from .report import latest_error_summary, tail_csv, tail_text


OUT_PATH = "reports/dashboard.html"


def e(value: object) -> str:
    return html.escape(str(value or ""))


def metric_cards() -> list[tuple[str, str]]:
    summary = {row.get("metric", ""): row.get("value", "") for row in tail_csv("logs/recommendation_performance_summary.csv", 50)}
    deliveries = tail_csv("logs/deliveries.csv", 50)
    return [
        ("positions", str(position_count())),
        ("performance rows", summary.get("rows", "0")),
        ("completed 1d", summary.get("completed_1d", "0")),
        ("avg 1d return", value_or_dash(summary.get("avg_1d_return_pct"), "%")),
        ("win rate 1d", value_or_dash(summary.get("win_rate_1d_pct"), "%")),
        ("suggested min score", summary.get("suggested_min_score", "?")),
        ("latest error", latest_error_summary(deliveries)),
        ("today runs", today_run_summary()),
    ]


def today_run_summary() -> str:
    statuses = run_log_statuses()
    ok = sum(line.endswith("=ok") for line in statuses)
    return f"{ok}/{len(statuses)} ok" if statuses else "0/0 ok"


def today_run_rows() -> list[dict[str, str]]:
    rows = []
    for line in run_log_statuses():
        name, separator, status = line.partition("=")
        if separator:
            rows.append({"step": name, "status": status})
    return rows


def value_or_dash(value: str | None, suffix: str = "") -> str:
    return f"{value}{suffix}" if value else "-"


def performance_summary_rows() -> list[dict[str, str]]:
    summary = {row.get("metric", ""): row.get("value", "") for row in tail_csv("logs/recommendation_performance_summary.csv", 50)}
    labels = [
        ("전체 1일 평균", "avg_1d_return_pct", "%"),
        ("전체 1일 승률", "win_rate_1d_pct", "%"),
        ("90점 이상 평균", "score_90_plus_avg_1d_return_pct", "%"),
        ("70~89점 평균", "score_70_89_avg_1d_return_pct", "%"),
        ("70점 미만 평균", "score_under_70_avg_1d_return_pct", "%"),
        ("추천 점수 조정", "score_adjustment", ""),
        ("추천 최소점수 제안", "suggested_min_score", ""),
    ]
    return [{"metric": label, "value": value_or_dash(summary.get(key), suffix)} for label, key, suffix in labels]


def recommendation_rank_rows(limit: int = 10, worst: bool = False) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], list[float]] = {}
    for row in tail_csv("logs/recommendation_performance.csv", 1000):
        value = row.get("return_1d_pct")
        if not value:
            continue
        key = (row.get("ticker", ""), row.get("name", ""))
        grouped.setdefault(key, []).append(float(value))
    rows = [
        {
            "ticker": ticker,
            "name": name,
            "picks": str(len(values)),
            "avg_1d_return_pct": f"{mean(values):.2f}",
            "win_rate_1d_pct": f"{sum(value > 0 for value in values) / len(values) * 100:.1f}",
        }
        for (ticker, name), values in grouped.items()
    ]
    return sorted(rows, key=lambda row: float(row["avg_1d_return_pct"]), reverse=not worst)[:limit]


def sell_alerted_recommendation_rows(limit: int = 10) -> list[dict[str, str]]:
    recommendations = {row.get("ticker", ""): row for row in tail_csv("logs/recommendation_performance.csv", 1000)}
    rows = []
    for alert in reversed(tail_csv("logs/sell_alerts.csv", 1000)):
        ticker = alert.get("ticker", "")
        recommendation = recommendations.get(ticker)
        if not recommendation:
            continue
        rows.append(
            {
                "ticker": ticker,
                "name": alert.get("name", recommendation.get("name", "")),
                "score": recommendation.get("score", ""),
                "entry_close": recommendation.get("entry_close", ""),
                "return_1d_pct": recommendation.get("return_1d_pct", ""),
                "sell_return_pct": alert.get("return_pct", ""),
                "sell_reason": alert.get("reason", ""),
            }
        )
        if len(rows) >= limit:
            return rows
    return rows


def sell_alert_summary_rows() -> list[dict[str, str]]:
    counts: dict[str, int] = {}
    for row in tail_csv("logs/sell_alerts.csv", 1000):
        summary = row.get("summary") or row.get("reason", "")
        if summary:
            counts[summary] = counts.get(summary, 0) + 1
    return [{"summary": summary, "count": str(count)} for summary, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)]


def performance_penalty_rows(limit: int = 10) -> list[dict[str, str]]:
    seen = set()
    rows = []
    for row in reversed(tail_csv("logs/recommendation_performance.csv", 1000)):
        ticker = row.get("ticker", "")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        penalty = performance_penalty(ticker)
        if penalty:
            rows.append({"ticker": ticker, "name": row.get("name", ""), "penalty": f"{penalty:.2f}"})
        if len(rows) >= limit:
            return rows
    return rows


def recommendation_reason_rows(limit: int = 10) -> list[dict[str, str]]:
    performance = {row.get("ticker", ""): row for row in tail_csv("logs/recommendation_performance.csv", 1000)}
    rows = []
    seen = set()
    for recommendation in reversed(tail_csv("logs/recommendations.csv", 1000)):
        ticker = recommendation.get("ticker", "")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        performance_row = performance.get(ticker, {})
        penalty = performance_penalty(ticker)
        rows.append(
            {
                "created_at": recommendation.get("created_at", ""),
                "ticker": ticker,
                "name": recommendation.get("name", ""),
                "score": recommendation.get("score", ""),
                "reason": reason_summary(recommendation, performance_row, penalty),
                "volume_ratio": recommendation.get("volume_ratio", ""),
                "trading_value_억": trading_value_eok(recommendation.get("trading_value", "")),
                "news_score": performance_row.get("news_score", ""),
                "disclosure_score": performance_row.get("disclosure_score", ""),
                "performance_penalty": f"{penalty:.2f}",
            }
        )
        if len(rows) >= limit:
            return rows
    return rows


def reason_summary(recommendation: dict[str, str], performance: dict[str, str], penalty: float) -> str:
    parts = []
    try:
        if float(recommendation.get("volume_ratio", 0)) >= 2:
            parts.append("거래량 급증")
    except ValueError:
        pass
    if positive(performance.get("news_score", "")):
        parts.append("뉴스 보너스")
    if positive(performance.get("disclosure_score", "")):
        parts.append("공시 보너스")
    if penalty:
        parts.append("성과 감점")
    return " + ".join(parts) or "기본 조건 충족"


def positive(value: str) -> bool:
    try:
        return float(value) > 0
    except ValueError:
        return False


def trading_value_eok(value: str) -> str:
    try:
        return f"{int(value) / 100_000_000:.0f}" if value else ""
    except ValueError:
        return ""


def settings_rows() -> list[dict[str, str]]:
    rows = []
    for line in health_lines():
        key, separator, value = line.partition("=")
        if separator:
            rows.append({"setting": key, "value": value})
    return rows


def issue_rows() -> list[dict[str, str]]:
    rows = []
    for label, value in metric_cards():
        if status_class(value) in {"bad", "warn"}:
            rows.append({"source": "card", "item": label, "status": value})
    for row in [*settings_rows(), *today_run_rows()]:
        value = row.get("value", row.get("status", ""))
        if status_class(value) in {"bad", "warn"}:
            rows.append({"source": row.get("setting") and "setting" or "run", "item": row.get("setting", row.get("step", "")), "status": value})
    return rows or [{"source": "dashboard", "item": "issues", "status": "none"}]


def table(title: str, rows: list[dict[str, str]], columns: list[str]) -> str:
    body = "".join("<tr>" + "".join(cell(row.get(column, "")) for column in columns) + "</tr>" for row in rows)
    head = "".join(f"<th>{e(column)}</th>" for column in columns)
    return f"<section><h2>{e(title)}</h2><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></section>"


def cell(value: object) -> str:
    klass = status_class(str(value or ""))
    attr = f" class='{klass}'" if klass else ""
    return f"<td{attr}>{e(value)}</td>"


def card(label: str, value: str) -> str:
    klass = status_class(value)
    attr = f" class='{klass}'" if klass else ""
    return f"<div class='card'><b>{e(label)}</b><span{attr}>{e(value)}</span></div>"


def status_class(value: str) -> str:
    lowered = value.lower()
    if lowered in {"ok", "none"} or " ok" in lowered:
        return "ok"
    if lowered in {"old"}:
        return "warn"
    if any(word in lowered for word in ("missing", "error", "found", "not-ok")):
        return "bad"
    return ""


def render() -> str:
    cards = "".join(card(label, value) for label, value in metric_cards())
    checks = "".join(f"<li>{e(line)}</li>" for line in daily_check_lines())
    task_log = "".join(f"<li>{e(line)}</li>" for line in tail_text("logs/task.out.log", 10))
    task_errors = tail_text("logs/task.err.log", 10) or ["none"]
    task_error_items = "".join(f"<li>{e(line)}</li>" for line in task_errors)
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>stockAlarm Dashboard</title>
<style>
body{{font-family:Segoe UI,Malgun Gothic,sans-serif;margin:24px;background:#f6f7f9;color:#111}}
h1{{margin-bottom:4px}} .muted{{color:#666}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:18px 0}}
.card{{background:white;border-radius:12px;padding:14px;box-shadow:0 1px 4px #ddd}} .card span{{display:block;font-size:24px;margin-top:8px}}
section{{background:white;border-radius:12px;padding:16px;margin:16px 0;box-shadow:0 1px 4px #ddd;overflow:auto}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{border-bottom:1px solid #eee;text-align:left;padding:8px;white-space:nowrap}} th{{background:#fafafa}}
.ok{{color:#147a2e;font-weight:600}} .warn{{color:#9a6700;font-weight:600}} .bad{{color:#b42318;font-weight:600}}
li{{margin:4px 0}}
</style>
</head>
<body>
<h1>stockAlarm Dashboard</h1>
<div class="muted">generated {e(datetime.now().isoformat(timespec="seconds"))}</div>
<div class="cards">{cards}</div>
{table("Issues", issue_rows(), ["source", "item", "status"])}
{table("Today run details", today_run_rows(), ["step", "status"])}
{table("Why recommended", recommendation_reason_rows(), ["created_at", "ticker", "name", "score", "reason", "volume_ratio", "trading_value_억", "news_score", "disclosure_score", "performance_penalty"])}
{table("Sell alert summary", sell_alert_summary_rows(), ["summary", "count"])}
{table("Recent sell alerts", tail_csv("logs/sell_alerts.csv", 10), ["created_at", "ticker", "name", "return_pct", "summary", "reason"])}
{table("Recommendation stats", performance_summary_rows(), ["metric", "value"])}
<section><h2>Daily check</h2><ul>{checks}</ul></section>
{table("Current settings", settings_rows(), ["setting", "value"])}
{table("Recent deliveries", tail_csv("logs/deliveries.csv", 10), ["created_at", "channel"])}
{table("Top recommendation performance", recommendation_rank_rows(), ["ticker", "name", "picks", "avg_1d_return_pct", "win_rate_1d_pct"])}
{table("Worst recommendation performance", recommendation_rank_rows(worst=True), ["ticker", "name", "picks", "avg_1d_return_pct", "win_rate_1d_pct"])}
{table("Performance penalties", performance_penalty_rows(), ["ticker", "name", "penalty"])}
{table("Recommendations with sell alerts", sell_alerted_recommendation_rows(), ["ticker", "name", "score", "entry_close", "return_1d_pct", "sell_return_pct", "sell_reason"])}
{table("Recent recommendations", tail_csv("logs/recommendations.csv", 10), ["created_at", "ticker", "name", "close", "score"])}
{table("Recommendation performance", tail_csv("logs/recommendation_performance.csv", 20), ["pick_date", "ticker", "name", "score", "entry_close", "return_1d_pct", "return_3d_pct", "return_5d_pct", "news_score", "disclosure_score"])}
{table("Positions", tail_csv("logs/positions_report.csv", 10), ["created_at", "ticker", "name", "entry_price", "close", "return_pct"])}
<section><h2>Recent task log</h2><ul>{task_log}</ul></section>
<section><h2>Recent task errors</h2><ul>{task_error_items}</ul></section>
</body>
</html>"""


def write(path: str = OUT_PATH) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(render())
    return path


def main() -> None:
    try:
        load_env()
        print(write())
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
