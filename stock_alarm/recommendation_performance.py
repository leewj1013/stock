from __future__ import annotations

import csv
import os
from datetime import date, datetime, timedelta
from statistics import mean

from .app import load_env, naver_rows, write_error_log
from .backtest import naver_close_after
from .dart_reference import reference as dart_reference
from .news_reference import reference as news_reference


HOLD_DAYS = [1, 3, 5]
LOG_PATH = "logs/recommendations.csv"
OUT_PATH = "logs/recommendation_performance.csv"
SUMMARY_PATH = "logs/recommendation_performance_summary.csv"
SCORE_BUCKETS = [("score_90_plus", 90, None), ("score_70_89", 70, 90), ("score_under_70", None, 70)]
EXTERNAL_COLUMNS = ["news_score", "disclosure_score", "financial_score", "external_notes"]


def read_recommendations(path: str = LOG_PATH) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def performance_rows(recommendations: list[dict[str, str]]) -> list[list[str]]:
    rows = []
    seen = set()
    for row in reversed(recommendations):
        if not row.get("created_at") or not row.get("ticker"):
            continue
        entry = int(float(row["close"]))
        pick_day = pick_trading_day(row["ticker"], datetime.fromisoformat(row["created_at"]).date(), entry)
        key = (pick_day.isoformat(), row["ticker"])
        if key in seen:
            continue
        seen.add(key)
        returns = []
        for days in HOLD_DAYS:
            close = naver_close_after(row["ticker"], pick_day, days)
            returns.append("" if close is None else f"{(close - entry) / entry * 100:.2f}")
        news_score, disclosure_score, notes = external_reference(row.get("name", "") or row["ticker"], row["ticker"])
        rows.append([pick_day.isoformat(), row["ticker"], row.get("name", ""), row.get("score", ""), str(entry), *returns, news_score, disclosure_score, "", notes])
    return list(reversed(rows))


def external_reference(name: str, ticker: str = "") -> tuple[str, str, str]:
    scores = ["", ""]
    notes = []
    try:
        if os.environ.get("NEWS_LOOKUP", "0") == "1":
            scores[0], note = news_reference(name)
            notes.append(note)
    except Exception:
        notes.append("news lookup failed")
    try:
        if os.environ.get("DART_LOOKUP", "0") == "1":
            scores[1], note = dart_reference(ticker)
            notes.append(note)
    except Exception:
        notes.append("dart lookup failed")
    return scores[0], scores[1], "; ".join(notes)


def pick_trading_day(ticker: str, created_day: date, entry_close: int) -> date:
    rows = naver_rows(ticker, created_day - timedelta(days=7), created_day)
    for row in reversed(rows):
        if int(row[4]) == entry_close:
            return datetime.strptime(str(row[0]), "%Y%m%d").date()
    return datetime.strptime(str(rows[-1][0]), "%Y%m%d").date() if rows else created_day


def write_csv(rows: list[list[str]], path: str = OUT_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["pick_date", "ticker", "name", "score", "entry_close", "return_1d_pct", "return_3d_pct", "return_5d_pct", *EXTERNAL_COLUMNS])
        writer.writerows(rows)


def write_summary(rows: list[list[str]], path: str = SUMMARY_PATH) -> None:
    values = [float(row[5]) for row in rows if row[5]]
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["metric", "value"])
        writer.writerow(["rows", len(rows)])
        writer.writerow(["completed_1d", len(values)])
        if values:
            writer.writerow(["avg_1d_return_pct", f"{mean(values):.2f}"])
            writer.writerow(["win_rate_1d_pct", f"{sum(value > 0 for value in values) / len(values) * 100:.1f}"])
        for metric, value in score_bucket_summary(rows):
            writer.writerow([metric, value])
        writer.writerow(["score_adjustment", score_adjustment_suggestion(rows)])
        writer.writerow(["suggested_min_score", suggested_min_score(rows)])


def lines(rows: list[list[str]]) -> list[str]:
    completed = [float(row[5]) for row in rows if row[5]]
    output = ["# recommendation performance", f"rows={len(rows)}", f"completed_1d={len(completed)}"]
    if completed:
        output.extend([f"avg_1d_return_pct={mean(completed):.2f}", f"win_rate_1d_pct={sum(value > 0 for value in completed) / len(completed) * 100:.1f}"])
    output.extend(f"{metric}={value}" for metric, value in score_bucket_summary(rows))
    output.append(f"score_adjustment={score_adjustment_suggestion(rows)}")
    output.append(f"suggested_min_score={suggested_min_score(rows)}")
    return output


def score_bucket_summary(rows: list[list[str]]) -> list[tuple[str, str]]:
    result = []
    for label, minimum, maximum in SCORE_BUCKETS:
        values = []
        for row in rows:
            score = float(row[3] or 0)
            if row[5] and (minimum is None or score >= minimum) and (maximum is None or score < maximum):
                values.append(float(row[5]))
        result.append((f"{label}_completed_1d", str(len(values))))
        if values:
            result.append((f"{label}_avg_1d_return_pct", f"{mean(values):.2f}"))
            result.append((f"{label}_win_rate_1d_pct", f"{sum(value > 0 for value in values) / len(values) * 100:.1f}"))
    return result


def score_adjustment_suggestion(rows: list[list[str]], min_completed: int = 10) -> str:
    buckets = []
    for label, minimum, maximum in SCORE_BUCKETS:
        values = []
        for row in rows:
            score = float(row[3] or 0)
            if row[5] and (minimum is None or score >= minimum) and (maximum is None or score < maximum):
                values.append(float(row[5]))
        if values:
            buckets.append((label, len(values), mean(values)))
    completed = sum(count for _, count, _ in buckets)
    if completed < min_completed:
        return f"not enough data ({completed}/{min_completed})"
    best = max(buckets, key=lambda item: item[2])
    worst = min(buckets, key=lambda item: item[2])
    return f"watch {best[0]} higher, {worst[0]} lower"


def suggested_min_score(rows: list[list[str]], min_completed: int = 10) -> str:
    buckets = []
    for label, minimum, maximum in SCORE_BUCKETS:
        values = [float(row[5]) for row in rows if row[5] and (minimum is None or float(row[3] or 0) >= minimum) and (maximum is None or float(row[3] or 0) < maximum)]
        if values:
            buckets.append((minimum, maximum, len(values), mean(values)))
    completed = sum(count for _minimum, _maximum, count, _avg in buckets)
    if completed < min_completed:
        return f"not enough data ({completed}/{min_completed})"
    positive = [bucket for bucket in buckets if bucket[3] > 0]
    if not positive:
        return "none"
    minimums = [minimum for minimum, _maximum, _count, _avg in positive if minimum is not None]
    return str(min(minimums)) if minimums else "0"


def run() -> list[str]:
    load_env()
    rows = performance_rows(read_recommendations())
    write_csv(rows)
    write_summary(rows)
    return lines(rows)


def main() -> None:
    try:
        print("\n".join(run()))
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
