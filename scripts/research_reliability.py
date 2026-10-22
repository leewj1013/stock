"""Offline research diagnostics; live databases are opened read-only."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
import random
import sqlite3
from statistics import mean, stdev
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stock_alarm.strategy_learning import objective


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def quantile(values, probability):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def calendar_from_cache(root, as_of):
    days = set()
    sources = []
    for path in sorted((root / ".cache/naver").glob("KOSPI-*.json")):
        rows = json.loads(path.read_text(encoding="utf-8"))
        days.update(datetime.strptime(str(row[0]), "%Y%m%d").date().isoformat()
                    for row in rows if str(row[0]) <= as_of.replace("-", ""))
        sources.append(path.relative_to(root).as_posix())
    return sorted(days), sources


def window_rows(rows, calendar, horizon):
    indices = {day: index for index, day in enumerate(calendar)}
    result = []
    for row in rows:
        start = row.get("entry_date")
        index = indices.get(start)
        if index is not None and index + horizon < len(calendar):
            result.append({"ticker": row["ticker"], "start": start, "end": calendar[index + horizon]})
    return result


def overlap_summary(windows):
    grouped = defaultdict(list)
    for row in windows:
        grouped[row["ticker"]].append(row)
    overlaps, nonoverlap = 0, 0
    for rows in grouped.values():
        previous_end = selected_end = ""
        for row in sorted(rows, key=lambda item: (item["start"], item["end"])):
            overlaps += row["start"] <= previous_end
            previous_end = max(previous_end, row["end"])
            if row["start"] > selected_end:
                nonoverlap += 1
                selected_end = row["end"]
    selected_end, across_dates = "", 0
    for start, end in sorted({(row["start"], row["end"]) for row in windows}, key=lambda item: item[1]):
        if start > selected_end:
            across_dates += 1
            selected_end = end
    return {"mapped_windows": len(windows), "same_ticker_overlapping_rows": overlaps,
            "same_ticker_nonoverlap_rows": nonoverlap, "nonoverlapping_date_windows": across_dates}


def block_bootstrap(daily, block, repetitions=2000):
    if len(daily) < block:
        return {"block_sessions": block, "status": "insufficient_calendar", "ci95": None}
    rng = random.Random(20261022)
    means = []
    for _ in range(repetitions):
        sampled = []
        while len(sampled) < len(daily):
            start = rng.randrange(len(daily) - block + 1)
            sampled.extend(daily[start:start + block])
        sampled = sampled[:len(daily)]
        count = sum(n for _, n in sampled)
        if count:
            means.append(sum(total for total, _ in sampled) / count)
    return {"block_sessions": block, "status": "exploratory_short_history",
            "replicates": len(means), "standard_error": stdev(means) if len(means) > 1 else None,
            "ci95": [quantile(means, 0.025), quantile(means, 0.975)] if means else None}


def sample_audit(rows, calendar):
    counts = Counter(row["pick_date"] for row in rows)
    tickers = Counter(row["ticker"] for row in rows)
    entry_days = Counter(row["entry_date"] for row in rows)
    values = [objective(row) for row in rows]
    grouped = defaultdict(list)
    for row, value in zip(rows, values):
        grouped[row["entry_date"]].append(value)
    days = [day for day in calendar if min(entry_days) <= day <= max(entry_days)] if entry_days else []
    daily = [(sum(grouped[day]), len(grouped[day])) for day in days]
    summaries = [{"entry_date": day, "count": len(grouped[day]),
                  "objective_sum": sum(grouped[day]),
                  "objective_mean": mean(grouped[day]) if grouped[day] else None} for day in days]
    windows = {str(horizon): overlap_summary(window_rows(rows, calendar, horizon)) for horizon in (10, 20)}
    folds = []
    for index in range(3):
        start = len(rows) - 180 + index * 60
        if start <= 0:
            continue
        train, validation = rows[:start], rows[start:start + 60]
        training_windows = window_rows(train, calendar, 10)
        validation_start = min(row["entry_date"] for row in validation)
        folds.append({"fold": index + 1, "training_rows": len(train), "validation_rows": len(validation),
                      "training_last_pick": train[-1]["pick_date"], "validation_first_pick": validation[0]["pick_date"],
                      "same_pick_day_boundary": train[-1]["pick_date"] == validation[0]["pick_date"],
                      "training_targets_not_finished_before_validation": sum(row["end"] >= validation_start for row in training_windows)})
    count = len(rows)
    iid_se = stdev(values) / math.sqrt(count) if count > 1 else None
    return {"rows": count, "unique_keys": len({(row["pick_date"], row["ticker"]) for row in rows}),
            "pick_days": len(counts), "entry_days": len(entry_days), "tickers": len(tickers),
            "first_pick": min(counts) if counts else None, "last_pick": max(counts) if counts else None,
            "most_frequent_ticker_count": max(tickers.values(), default=0),
            "balanced_day_equivalent": count ** 2 / sum(n * n for n in counts.values()) if count else 0,
            "calendar_sessions": len(days), "objective_mean": mean(values) if values else None,
            "iid_standard_error": iid_se,
            "iid_normal_ci95": [mean(values) - 1.96 * iid_se, mean(values) + 1.96 * iid_se] if iid_se is not None else None,
            "objective_excess_coverage": {str(h): sum(row.get(f"excess_{h}d_pct") is not None for row in rows) for h in (1, 3, 5, 10)},
            "overlap": windows, "fold_boundaries": folds,
            "block_bootstrap": [block_bootstrap(daily, h) for h in (10, 20)]}, summaries


def session(stamp):
    clock = stamp[11:19]
    if "09:00:00" <= clock < "15:30:00":
        return "intraday"
    if "15:30:00" <= clock < "16:00:00":
        return "early_close"
    if clock >= "16:00:00":
        return "after_1600"
    return "other"


def price_audit(connection, as_of):
    raw = connection.execute("""SELECT COUNT(*) AS rows, MIN(created_at) AS first_seen,
        MAX(created_at) AS last_seen, SUM(reference_close>0 AND close>0) AS paired_rows
        FROM price_quality WHERE substr(created_at,1,10)<=?""", (as_of,)).fetchone()
    latest = {}
    raw_status = Counter()
    columns = {row[1] for row in connection.execute("PRAGMA table_info(price_quality)")}
    metadata = ", ".join(name if name in columns else f"NULL AS {name}" for name in
                         ("source_fetched_at", "reference_fetched_at", "reference_price_date"))
    for row in connection.execute(f"""SELECT quality_id, created_at, ticker, price_date, source,
            reference_source, close, reference_close, status, reason, {metadata} FROM price_quality
            WHERE substr(created_at,1,10)<=? ORDER BY quality_id""", (as_of,)):
        row = dict(row)
        raw_status[(row["source"], row["reference_source"] or "none", row["status"])] += 1
        key = (row["created_at"][:10], row["ticker"], row["source"], row["reference_source"] or "none", session(row["created_at"]))
        if key not in latest or (row["created_at"], row["quality_id"]) > (latest[key]["created_at"], latest[key]["quality_id"]):
            latest[key] = row
    groups = defaultdict(list)
    pairs = []
    for (day, ticker, source, reference, period), row in sorted(latest.items()):
        paired = bool(row["close"] and row["reference_close"] and row["close"] > 0 and row["reference_close"] > 0)
        gap = abs(row["close"] / row["reference_close"] - 1) * 100 if paired else None
        close_candidate = bool(paired and source == "naver" and reference == "pykrx"
                                and period == "after_1600" and row["price_date"] == day
                                and row["reference_price_date"] == day and row["source_fetched_at"]
                                and row["reference_fetched_at"] and abs((datetime.fromisoformat(row["source_fetched_at"])
                                - datetime.fromisoformat(row["reference_fetched_at"])).total_seconds()) <= 120)
        record = {"observed_day": day, "ticker": ticker, "session": period, "source": source,
                  "reference_label": reference, "price_date": row["price_date"], "observed_at": row["created_at"],
                  "close": row["close"], "reference_close": row["reference_close"],
                  "abs_difference_pct": gap, "status": row["status"], "reason": row["reason"],
                  "actual_reference_verified": bool(paired and row["reference_fetched_at"] and reference in {"toss", "pykrx"}),
                  "source_fetched_at": row["source_fetched_at"], "reference_fetched_at": row["reference_fetched_at"],
                  "reference_price_date": row["reference_price_date"],
                  "same_day_close_candidate": close_candidate, "quote_timestamps_available": False}
        pairs.append(record)
        groups[(source, reference, period)].append(record)
    aggregate = []
    for (source, reference, period), records in sorted(groups.items()):
        differences = [row["abs_difference_pct"] for row in records if row["abs_difference_pct"] is not None]
        aggregate.append({"source": source, "reference_label": reference, "session": period,
                          "latest_observations": len(records), "paired": len(differences),
                          "same_day_close_candidates": sum(row["same_day_close_candidate"] for row in records),
                          "same_observation_date": sum(row["price_date"] == row["observed_day"] for row in records),
                          "median_abs_difference_pct": quantile(differences, .5), "p95_abs_difference_pct": quantile(differences, .95),
                          "over_0_5pct": sum(value > .5 for value in differences),
                          "over_0_5pct_rate": sum(value > .5 for value in differences) / len(differences) if differences else None})
    return {"raw": dict(raw), "raw_status": [{"source": s, "reference_label": r, "status": st, "rows": n}
            for (s, r, st), n in sorted(raw_status.items())], "deduplicated_observations": len(latest),
            "groups": aggregate, "strict_comparable_pairs": 0,
            "same_day_close_candidates": sum(row["same_day_close_candidate"] for row in pairs)}, pairs


STUDIES = (
    ("low_vol", "2026-09-30", 12, "monthly", "reports/research/2026-09-30-low_vol.md", "reports/research/scripts/low_vol.py"),
    ("high52w", "2026-10-03", 12, "monthly", "reports/research/2026-10-03-high52w.md", "reports/research/scripts/high52w.py"),
    ("reversal_1w", "2026-10-14", 26, "weekly", "reports/research/2026-10-13-reversal_1w.md", "reports/research/scripts/reversal_1w.py"),
    ("high_vol_exclusion", "2026-10-18", 252, "daily", "reports/revalidation/2026-10-18-causal-signals/REPORT.md", "scripts/revalidate_causal_signals.py"),
    ("bear_ma20_reentry", "2026-10-18", 252, "daily", "reports/revalidation/2026-10-18-causal-signals/REPORT.md", "scripts/revalidate_causal_signals.py"),
    ("holding_period", "2026-10-17", 252, "daily", "reports/revalidation/2026-10-17-execution-timing/REPORT.md", "scripts/revalidate_execution_timing.py"),
    ("us_baseline", "2026-10-17", 252, "daily", "reports/revalidation/2026-10-17-execution-timing/REPORT.md", "scripts/revalidate_execution_timing.py"),
    ("exit_variants", "2026-10-20", 252, "daily", "reports/revalidation/2026-10-20-exit-variants/PROTOCOL.md", "scripts/revalidate_exit_variants.py"),
)


def lifetime_audit(root, as_of, calendar=None):
    files = []
    unparsed = []
    for path in sorted((root / "data/backtest_expanded/pit_universe/ohlcv").glob("*.csv")):
        with path.open("rb") as file:
            file.seek(0, 2)
            size = file.tell()
            file.seek(max(0, size - 1024))
            tail = file.read().strip().splitlines()
        stamp = tail[-1].split(b",")[0].decode("ascii", errors="replace") if tail else ""
        if len(stamp) == 8 and stamp.isdigit():
            files.append({"ticker": path.stem, "last_day": datetime.strptime(stamp, "%Y%m%d").date().isoformat()})
        else:
            unparsed.append(path.relative_to(root).as_posix())
    counts = Counter(row["last_day"] for row in files)
    latest_universe_day = sorted((row["last_day"] for row in files))[max(0, len(files) // 10 - 1)] if files else None
    registry_path = root / "reports/research/reliability_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8")) if registry_path.exists() else {}
    studies = []
    for slug, frozen_at, minimum, frequency, report, source in STUDIES:
        hashes = {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in (report, source) if (root / p).exists()}
        after = sum(row["last_day"] > frozen_at for row in files)
        registry.setdefault(slug, {"hash_frozen_on": as_of, "source_hashes": hashes})
        changed = hashes != registry[slug]["source_hashes"]
        # shortcut: cached KOSPI sessions and 90% file coverage are readiness proxies; run the frozen study before judging persistence.
        days = [day for day in (calendar or []) if frozen_at < day <= (latest_universe_day or "")]
        periods = {day[:7] if frequency == "monthly" else date.fromisoformat(day).isocalendar()[:2]
                   if frequency == "weekly" else day for day in days}
        ready = slug != "us_baseline" and len(periods) >= minimum
        studies.append({"study": slug, "latest_documented_revision": frozen_at,
                        "hash_frozen_on": registry[slug]["hash_frozen_on"],
                        "minimum_new_observations": minimum,
                        "frequency": frequency, "source_hashes": hashes, "kr_files_with_any_later_data": after,
                        "as_of": as_of, "forward_periods_with_90pct_kr_file_coverage": len(periods) if slug != "us_baseline" else None,
                        "status": "source_changed_requires_review" if changed else "ready_for_frozen_retest" if ready else "await_frozen_forward_results",
                        "reason": "No frozen-definition post-study result series is stored by this audit; sparse live quotes are not the original universe."})
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"coverage": [{"last_day": day, "files": count} for day, count in sorted(counts.items())],
            "price_files": len(files), "latest_price_day": max(counts) if counts else None,
            "latest_90pct_coverage_day": latest_universe_day,
            "unparsed_files": unparsed, "studies": studies}, files


def run(root=ROOT, as_of=None, output=None):
    root = Path(root)
    as_of = as_of or date.today().isoformat()
    output = Path(output or root / f"reports/revalidation/{as_of}-research-reliability")
    output.mkdir(parents=True, exist_ok=True)
    calendar, calendar_sources = calendar_from_cache(root, as_of)
    with sqlite3.connect((root / "data/stock_alarm.db").resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute("""SELECT * FROM
            (SELECT * FROM recommendation_outcomes WHERE quality_status='valid' AND pick_date<=?
             ORDER BY pick_date DESC, ticker LIMIT 1000) ORDER BY pick_date, ticker DESC""", (as_of,))]
        mature = [row for row in rows if row["return_20d_pct"] is not None and objective(row) is not None]
        sample, daily = sample_audit(mature, calendar)
        latest_version = connection.execute("SELECT * FROM learned_strategy_versions WHERE substr(created_at,1,10)<=? ORDER BY created_at DESC LIMIT 1", (as_of,)).fetchone()
        sample["latest_learning_decision"] = dict(latest_version) if latest_version else None
        sample["valid_db_rows_in_1000_window"] = len(rows)
        performance = read_csv(root / "logs/recommendation_performance.csv")
        keys = [(row["pick_date"], row["ticker"]) for row in performance if row["pick_date"] <= as_of]
        sample["performance_csv_rows"] = len(keys)
        sample["performance_csv_duplicate_keys"] = len(keys) - len(set(keys))
        lifetime, coverage = lifetime_audit(root, as_of, calendar)
        prices, pairs = price_audit(connection, as_of)
    result = {"as_of": as_of, "generated_at": datetime.now().isoformat(timespec="seconds"),
              "scope": "offline_observational_no_strategy_changes", "sample": sample, "lifetime": lifetime,
              "prices": prices, "calendar_cache_files": calendar_sources,
              "source_files": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in
                               ("scripts/research_reliability.py", "stock_alarm/strategy_learning.py", "stock_alarm/data_quality.py", "stock_alarm/virtual_trader_report.py")}}
    (output / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output / "sample_rows.json").write_text(json.dumps(mature, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (output / "calendar.json").write_text(json.dumps(calendar), encoding="utf-8")
    write_csv(output / "sample_daily.csv", daily)
    write_csv(output / "price_pairs.csv", pairs)
    write_csv(output / "price_groups.csv", prices["groups"])
    write_csv(output / "universe_coverage.csv", coverage)
    print(json.dumps({"output": str(output), "mature_rows": len(mature), "pick_days": sample["pick_days"],
                      "price_observations": prices["deduplicated_observations"], "latest_price_day": lifetime["latest_price_day"]}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", default=date.today().isoformat())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    date.fromisoformat(args.as_of)
    run(as_of=args.as_of, output=args.output)
