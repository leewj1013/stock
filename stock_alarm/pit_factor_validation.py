from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from .app import configured_stocks, load_env
from .backtest_data import REPORT_DIR
from .factor_analysis import FACTOR_LABELS, run as run_factor_analysis
from .point_in_time_collect import collect
from .point_in_time_store import DEFAULT_PATH, PointInTimeStore
from .robustness_analysis import corrected_factor_verdicts, factor_hac_results


def _csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _table(rows: list[dict], fields: list[tuple[str, str]]) -> str:
    lines = ["| " + " | ".join(label for _, label in fields) + " |", "|" + "|".join("---" for _ in fields) + "|"]
    lines.extend("| " + " | ".join(str(row.get(key, "")) for key, _ in fields) + " |" for row in rows)
    return "\n".join(lines)


def build_pit_report(store_path: Path, report_dir: Path, start: date, end: date) -> Path:
    names = configured_stocks()
    coverage = PointInTimeStore(store_path).coverage(list(names), start, end)
    detail_dir = report_dir / "point_in_time"
    detail_dir.mkdir(parents=True, exist_ok=True)
    with (detail_dir / "coverage.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(coverage[0]) if coverage else [])
        if coverage:
            writer.writeheader(); writer.writerows(coverage)
    from contextlib import closing
    from .point_in_time_store import connect
    with closing(connect(store_path)) as db:
        failures = [dict(row) for row in db.execute("SELECT source,ticker,start_date,end_date,status,records,message,collected_at FROM collection_log WHERE status<>'success' ORDER BY collected_at,ticker")]
    with (detail_dir / "failures.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(failures[0]) if failures else ["source", "ticker", "message"])
        writer.writeheader()
        if failures:
            writer.writerows(failures)
    verdicts = _csv(report_dir / "factor_verdicts.csv")
    corrected = _csv(report_dir / "factor_verdict_corrections.csv")
    hac = _csv(report_dir / "factor_hac_fdr.csv")
    available = Counter(row["source"] for row in coverage if row["status"] == "available")
    records = Counter()
    for row in coverage:
        records[row["source"]] += int(row["records"])
    rank_rows = sorted(verdicts, key=lambda row: float(row["predictive_score"]) if row.get("predictive_score") not in (None, "", "None") else -999, reverse=True)
    primary = [row for row in hac if row.get("scope") == "all" and row.get("target") in ("excess_3d_pct", "excess_5d_pct")]
    corrected_lookup = {row["factor"]: row for row in corrected}
    for row in rank_rows:
        row["hac_fdr_verdict"] = corrected_lookup.get(row["factor"], {}).get("after_hac_fdr_verdict", "N/A")
    lines = [
        "# Point-in-Time 외부요인 검증 리포트", "",
        f"- 생성시각: {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 요청기간: {start.isoformat()} ~ {end.isoformat()}",
        f"- 종목수: {len(names)}", f"- 저장소: `{store_path.resolve()}`", "",
        "## 수집 커버리지", "",
        _table([{"source": source, "tickers": available[source], "total": records[source], "coverage": f"{available[source] / len(names) * 100:.2f}%" if names else "0%"} for source in ("news", "disclosure", "financial")],
               [("source", "데이터"), ("tickers", "레코드 보유 종목"), ("coverage", "종목 커버리지"), ("total", "레코드 수")]), "",
        "상세 종목별 최초·최종 공개시각은 `point_in_time/coverage.csv`, 실패 내역은 `point_in_time/failures.csv`에 있습니다.", "",
        "## Point-in-time 가정과 한계", "",
        "- 뉴스는 네이버 검색 API가 반환한 `pubDate`를 공개시각으로 사용합니다. API는 날짜 범위 검색을 지원하지 않고 쿼리당 최신 1,000건까지만 페이지 조회할 수 있어, 2022년까지 완전한 역사 수집을 보장하지 않습니다. 범위를 벗어난 기간은 채우지 않고 미수집으로 둡니다.",
        "- OpenDART 공시 목록은 접수일(`rcept_dt`)은 제공하지만 이 수집 경로에서 시각은 제공하지 않습니다. 당일 정보 누출을 막기 위해 접수일 다음 영업일 00:00 KST부터 사용합니다.",
        "- PyKRX 일별 PER/PBR/DIV/EPS/BPS는 공급자가 제공한 해당 일자 스냅샷을 다음 영업일부터 사용합니다. 공급자의 과거 수정 이력과 원 실적 발표시각을 완전히 재현하지 못하므로 `revision_history_unverified`로 표시합니다.",
        "- 다음 영업일 계산은 주말을 제외하지만 거래소 휴장일 달력까지 반영하지 않습니다. 같은 날 사용하는 것보다 보수적이나 완전한 공시시각 복원은 아닙니다.", "",
        "## 7개 요인 예측력 순위", "",
        _table(rank_rows, [("label", "요인"), ("configured_points", "현재 배점"), ("predictive_score", "3·5일 평균 IC"), ("predictive_rank", "순위"), ("verdict", "기존 검정"), ("hac_fdr_verdict", "HAC+FDR")]), "",
        "## 외부요인 전체기간 3·5일 HAC+FDR", "",
        _table([row | {"label": FACTOR_LABELS.get(row["factor"], row["factor"])} for row in primary if row["factor"] in ("news_score", "disclosure_score", "financial_score")],
               [("label", "요인"), ("target", "목표"), ("ic_period_count", "IC 일수"), ("mean_daily_ic", "IC"), ("original_p_value", "기존 p"), ("newey_west_p_value", "HAC p"), ("fdr_p_value_primary", "FDR q")]), "",
        "## 해석", "",
        "이 결과는 확보된 PIT 표본에 대한 진단이며 운영 가중치를 자동 변경하지 않습니다. 특히 뉴스의 역사 범위와 재무 수치의 수정 이력 한계 때문에 커버리지가 낮은 구간의 ‘비유의’ 결과를 곧바로 신호 부재로 단정하면 안 됩니다.", "",
    ]
    output = report_dir / "POINT_IN_TIME_FACTOR_REPORT.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def run(store_path: Path = DEFAULT_PATH, report_dir: Path = REPORT_DIR, start: date = date(2022, 6, 30), end: date | None = None, collect_first: bool = False) -> dict:
    load_env(); end = end or date.today()
    os.environ["PIT_STORE_PATH"] = str(store_path)
    if collect_first:
        collect(start, end, store_path)
    factor = run_factor_analysis(report_dir=report_dir)
    alpha = float(os.environ.get("STAT_FDR_ALPHA", "0.05"))
    rows = factor_hac_results(report_dir, alpha, int(os.environ.get("FACTOR_MIN_CROSS_SECTION", "5")), os.environ.get("STAT_HAC_LAG_POLICY", "holding_period_minus_one"), int(os.environ.get("STAT_HAC_MAX_LAG", "19")), int(os.environ.get("STAT_HAC_FIXED_LAG", "4")))
    from .factor_analysis import _write_csv
    _write_csv(report_dir / "factor_hac_fdr.csv", rows)
    corrected = corrected_factor_verdicts(report_dir, rows, alpha, float(os.environ.get("FACTOR_MONOTONIC_TOLERANCE_PCT", "0.05")))
    _write_csv(report_dir / "factor_verdict_corrections.csv", corrected)
    report = build_pit_report(store_path, report_dir, start, end)
    result = {"report": str(report), "samples": factor["samples"], "store": str(store_path), "live_config_changed": False}
    print(json.dumps(result, ensure_ascii=False)); return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect and validate point-in-time external factors")
    parser.add_argument("--db", type=Path, default=DEFAULT_PATH); parser.add_argument("--start", type=date.fromisoformat, default=date(2022, 6, 30)); parser.add_argument("--end", type=date.fromisoformat, default=date.today()); parser.add_argument("--collect", action="store_true")
    args = parser.parse_args(); run(args.db, REPORT_DIR, args.start, args.end, args.collect)


if __name__ == "__main__": main()
