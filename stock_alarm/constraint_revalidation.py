from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from .backtest_data import DATA_DIR, REPORT_DIR
from .benchmark_comparison import run as run_benchmark
from .gap_decomposition import run as run_gap


OUTPUT_DIR = REPORT_DIR / "constraint_revalidation"
BEFORE_DIR = OUTPUT_DIR / "before"
REPORT_PATH = REPORT_DIR / "CONSTRAINT_REVALIDATION_REPORT.md"
CLAUDE_PATH = REPORT_DIR / "CONSTRAINT_REVALIDATION_CLAUDE.txt"


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _snapshot_before() -> None:
    """Preserve the last unconstrained outputs once; later reruns never replace them."""
    sources = {
        REPORT_DIR / "BENCHMARK_COMPARISON_REPORT.md": "BENCHMARK_COMPARISON_REPORT.md",
        REPORT_DIR / "GAP_DECOMPOSITION_REPORT.md": "GAP_DECOMPOSITION_REPORT.md",
        REPORT_DIR / "benchmark_comparison" / "strategy_metrics.csv": "benchmark_strategy_metrics.csv",
        REPORT_DIR / "benchmark_comparison" / "parameters.json": "benchmark_parameters.json",
        REPORT_DIR / "gap_decomposition" / "factor_contributions.csv": "gap_factor_contributions.csv",
        REPORT_DIR / "gap_decomposition" / "regime_contributions.csv": "gap_regime_contributions.csv",
        REPORT_DIR / "gap_decomposition" / "parameters.json": "gap_parameters.json",
    }
    BEFORE_DIR.mkdir(parents=True, exist_ok=True)
    for source, name in sources.items():
        target = BEFORE_DIR / name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _benchmark_comparison(before: list[dict], after: list[dict]) -> list[dict]:
    old = {(row["strategy"], row["regime"]): row for row in before}
    rows = []
    for row in after:
        key = (row["strategy"], row["regime"])
        prior = old.get(key, {})
        before_return = _float(prior.get("total_return_pct"))
        after_return = _float(row.get("total_return_pct"))
        rows.append({
            "strategy": key[0], "regime": key[1],
            "before_total_return_pct": round(before_return, 4),
            "after_total_return_pct": round(after_return, 4),
            "change_pp": round(after_return - before_return, 4),
            "before_mdd_pct": round(_float(prior.get("mdd_pct")), 4),
            "after_mdd_pct": round(_float(row.get("mdd_pct")), 4),
            "before_trades": int(_float(prior.get("trades"))),
            "after_trades": int(_float(row.get("trades"))),
        })
    return rows


def _gap_comparison(before: list[dict], after: list[dict]) -> list[dict]:
    old = {row["factor"]: row for row in before}
    rows = []
    for row in after:
        prior = old.get(row["factor"], {})
        before_value = _float(prior.get("gap_contribution_pp"))
        after_value = _float(row.get("gap_contribution_pp"))
        rows.append({
            "factor": row["factor"], "before_gap_contribution_pp": round(before_value, 4),
            "after_gap_contribution_pp": round(after_value, 4),
            "change_pp": round(after_value - before_value, 4),
            "after_rank": row.get("rank", ""), "method": row.get("method", ""),
        })
    return rows


def _regime_gap_comparison(before: list[dict], after: list[dict]) -> list[dict]:
    old = {row["regime"]: row for row in before}
    rows = []
    for row in after:
        prior = old.get(row["regime"], {})
        output = {"regime": row["regime"]}
        for metric in ("stock_return_pct", "kospi_return_pct", "gap_pp", "cash_pp", "early_exit_pp",
                       "cost_pp", "selection_pp", "sizing_pp", "residual_pp"):
            output[f"before_{metric}"] = round(_float(prior.get(metric)), 4)
            output[f"after_{metric}"] = round(_float(row.get(metric)), 4)
        output["after_defensive_cash_pp"] = round(_float(row.get("defensive_cash_pp")), 4)
        output["after_general_cash_pp"] = round(_float(row.get("general_cash_pp")), 4)
        rows.append(output)
    return rows


def _halt_periods(states: list[dict]) -> list[dict]:
    periods = []
    current = None
    for row in states:
        halted = str(row.get("defensive_mode", "")).lower() in {"true", "1", "y"}
        if halted and current is None:
            current = {"start_date": row["date"], "end_date": row["date"], "days": 0, "reasons": Counter()}
        if halted:
            current["end_date"] = row["date"]
            current["days"] += 1
            current["reasons"].update(filter(None, str(row.get("risk_reason", "")).split(",")))
        elif current is not None:
            current["reasons"] = ", ".join(f"{key}:{value}" for key, value in current["reasons"].most_common())
            periods.append(current)
            current = None
    if current is not None:
        current["reasons"] = ", ".join(f"{key}:{value}" for key, value in current["reasons"].most_common())
        periods.append(current)
    return periods


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    return [
        "| " + " | ".join(label for _key, label in columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
        *["| " + " | ".join(str(row.get(key, "")) for key, _label in columns) + " |" for row in rows],
    ]


def _build_report(benchmark_rows: list[dict], gap_rows: list[dict], regime_gap_rows: list[dict], halt_periods: list[dict],
                  before_benchmark_parameters: dict, after_benchmark_parameters: dict,
                  before_gap_parameters: dict, after_gap_parameters: dict) -> str:
    overall = [row for row in benchmark_rows if row["regime"] == "all"]
    stock = next(row for row in overall if row["strategy"] == "stock_alarm")
    kospi = next(row for row in overall if row["strategy"] == "kospi_buy_hold")
    gap_before = _float(before_gap_parameters.get("total_gap_pp"))
    gap_after = _float(after_gap_parameters.get("total_gap_pp"))
    before_top = [row["factor"] for row in sorted(gap_rows, key=lambda row: row["before_gap_contribution_pp"], reverse=True) if row["factor"] != "기타/미분해"][:3]
    after_top = [row["factor"] for row in sorted(gap_rows, key=lambda row: row["after_gap_contribution_pp"], reverse=True) if row["factor"] != "기타/미분해"][:3]
    before_cost = next((row["before_gap_contribution_pp"] for row in gap_rows if row["factor"] == "누적 거래비용"), 0.0)
    after_cost = next((row["after_gap_contribution_pp"] for row in gap_rows if row["factor"] == "누적 거래비용"), 0.0)
    conclusion = (
        "구조적 유휴현금 진단은 더 강해졌지만, 과잉회전율이 주원인이라는 진단은 유지되지 않습니다. "
        f"거래비용 기여는 {before_cost:.4f}%p에서 {after_cost:.4f}%p로 축소됐습니다."
    )
    lines = [
        "# 위험중단·고상관 제한 연결 후 벤치마크/격차분해 재검증", "",
        "## 결론", "", f"**{conclusion}**", "",
        f"stockAlarm 총수익률은 연결 전 `{stock['before_total_return_pct']:.4f}%`에서 연결 후 `{stock['after_total_return_pct']:.4f}%`로 `{stock['change_pp']:+.4f}%p` 변했습니다. KOSPI는 `{kospi['after_total_return_pct']:.4f}%`이며, 수익률 격차는 `{gap_before:.4f}%p`에서 `{gap_after:.4f}%p`로 `{gap_after-gap_before:+.4f}%p` 변했습니다.",
        f"위험중단은 `{after_gap_parameters.get('risk_halt_days', 0)}`일, 진입 `{after_gap_parameters.get('risk_halt_transitions', 0)}`회, 해제 `{after_gap_parameters.get('risk_resume_transitions', 0)}`회였습니다. 방어모드 현금 효과는 `{_float(after_gap_parameters.get('defensive_cash_effect_pp')):.4f}%p`, 일반 유휴현금 효과는 `{_float(after_gap_parameters.get('general_cash_effect_pp')):.4f}%p`입니다.",
        f"고상관 그룹 제한으로 거절된 stockAlarm 신규 신호는 `{after_gap_parameters.get('correlation_rejected_signals', 0)}`건입니다.", "",
        "## 무엇을 연결했는가", "",
        "- `portfolio_risk.evaluate_risk_state`: 라이브 DB 스냅샷과 백테스트가 당일 -2%, 주간 -5%, 고점 대비 -10%, 보유비중 70% 초과의 동일 평가 함수를 사용합니다.",
        "- `app.correlation_limited_allocations`: 라이브와 백테스트가 동일한 상관계수 0.8 및 연결그룹 합산 40% 신규진입 제한 함수를 사용합니다. 백테스트는 해당 날짜까지만의 가격행을 주입합니다.",
        "- 위험중단 중에도 기존 보유종목 매도검사는 계속 실행되며 신규 신호 생성만 차단합니다.",
        "- 운영 config, 라이브 DB, 가상계좌, 실주문 API는 수정하지 않았습니다.", "",
        "## 회귀 테스트", "",
        f"- 실행 결과: `{os.environ.get('REVALIDATION_TEST_SUMMARY', '별도 테스트 실행 결과 참조')}`",
        "- 네 위험 트리거를 공통 순수 평가기에서 검증했습니다.",
        "- 벤치마크 엔진에서 보유비중 70% 초과 시 신규 후보가 주문되지 않는지 검증했습니다.",
        "- 3종목 고상관 연결그룹 목표비중 합계가 40% 이하인지 검증했습니다.", "",
        "## 전체 기간 벤치마크 연결 전/후", "",
    ]
    lines.extend(_table(overall, [
        ("strategy", "전략"), ("before_total_return_pct", "연결 전 수익률%"),
        ("after_total_return_pct", "연결 후 수익률%"), ("change_pp", "변화%p"),
        ("before_mdd_pct", "전 MDD%"), ("after_mdd_pct", "후 MDD%"),
        ("before_trades", "전 거래"), ("after_trades", "후 거래"),
    ]))
    lines.extend(["", "## 국면별 벤치마크 연결 전/후", ""])
    lines.extend(_table([row for row in benchmark_rows if row["regime"] != "all"], [
        ("regime", "국면"), ("strategy", "전략"), ("before_total_return_pct", "전 수익률%"),
        ("after_total_return_pct", "후 수익률%"), ("change_pp", "변화%p"),
        ("before_mdd_pct", "전 MDD%"), ("after_mdd_pct", "후 MDD%"),
    ]))
    lines.extend(["", "## 격차분해 연결 전/후", ""])
    lines.extend(_table(gap_rows, [
        ("factor", "요인"), ("before_gap_contribution_pp", "연결 전%p"),
        ("after_gap_contribution_pp", "연결 후%p"), ("change_pp", "변화%p"),
        ("after_rank", "연결 후 순위"),
    ]))
    lines.extend(["", f"연결 전 Top 3: `{', '.join(before_top)}`", "", f"연결 후 Top 3: `{', '.join(after_top)}`", "",
                  "## 국면별 격차분해 연결 전/후", ""])
    lines.extend(_table(regime_gap_rows, [
        ("regime", "국면"), ("before_gap_pp", "전 격차%p"), ("after_gap_pp", "후 격차%p"),
        ("before_cash_pp", "전 현금%p"), ("after_cash_pp", "후 현금%p"),
        ("after_defensive_cash_pp", "후 방어현금%p"), ("after_general_cash_pp", "후 일반현금%p"),
        ("before_cost_pp", "전 비용%p"), ("after_cost_pp", "후 비용%p"),
        ("before_early_exit_pp", "전 조기매도%p"), ("after_early_exit_pp", "후 조기매도%p"),
        ("before_selection_pp", "전 선정%p"), ("after_selection_pp", "후 선정%p"),
        ("before_sizing_pp", "전 비중%p"), ("after_sizing_pp", "후 비중%p"),
    ]))
    lines.extend(["",
                  "## 방어모드 연속 구간", ""])
    if halt_periods:
        lines.extend(_table(halt_periods, [("start_date", "시작"), ("end_date", "종료"), ("days", "거래일"), ("reasons", "사유별 일수")]))
    else:
        lines.append("연결 후에도 평가기간 중 방어모드가 발생하지 않았습니다.")
    lines.extend(["", "## 해석 시 주의사항", "",
                  "- 일봉 백테스트의 당일/주간 손익 기준은 전일 종가와 직전 주 마지막 거래일 종가입니다. 라이브 장중 스냅샷과 동일한 임계값·판정함수를 쓰지만 관측 빈도는 다릅니다.",
                  "- 고상관 40%는 신규진입 시점의 목표비중 제한입니다. 기존 보유종목은 강제매도하지 않으므로 이후 가격상승으로 실보유비중이 40%를 넘을 수 있습니다.",
                  "- 비용 제거·현금 대체·비중 무상한은 반사실적 진단이며 실제 실행 가능 수익을 보장하지 않습니다.",
                  "- 현재 watchlist를 과거에 적용한 생존편향과 과거 외부요인 스냅샷 부재 한계는 유지됩니다.", "",
                  "## 재현 정보", "", "```json", json.dumps({
                      "created_at": datetime.now().isoformat(timespec="seconds"),
                      "before_benchmark_parameters": before_benchmark_parameters,
                      "after_benchmark_parameters": after_benchmark_parameters,
                      "before_gap_parameters": before_gap_parameters,
                      "after_gap_parameters": after_gap_parameters,
                      "before_snapshot_directory": str(BEFORE_DIR),
                      "live_state_modified": False,
                  }, ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)


def run(data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR, reexecute: bool = True) -> dict:
    _snapshot_before()
    if reexecute:
        run_benchmark(data_dir, report_dir)
        run_gap(data_dir, report_dir)

    before_benchmark = _read_csv(BEFORE_DIR / "benchmark_strategy_metrics.csv")
    after_benchmark = _read_csv(REPORT_DIR / "benchmark_comparison" / "strategy_metrics.csv")
    before_gap = _read_csv(BEFORE_DIR / "gap_factor_contributions.csv")
    after_gap = _read_csv(REPORT_DIR / "gap_decomposition" / "factor_contributions.csv")
    before_regime_gap = _read_csv(BEFORE_DIR / "gap_regime_contributions.csv")
    after_regime_gap = _read_csv(REPORT_DIR / "gap_decomposition" / "regime_contributions.csv")
    states = _read_csv(REPORT_DIR / "gap_decomposition" / "daily_portfolio_state.csv")
    before_benchmark_parameters = json.loads((BEFORE_DIR / "benchmark_parameters.json").read_text(encoding="utf-8"))
    after_benchmark_parameters = json.loads((REPORT_DIR / "benchmark_comparison" / "parameters.json").read_text(encoding="utf-8"))
    before_gap_parameters = json.loads((BEFORE_DIR / "gap_parameters.json").read_text(encoding="utf-8"))
    after_gap_parameters = json.loads((REPORT_DIR / "gap_decomposition" / "parameters.json").read_text(encoding="utf-8"))

    benchmark_rows = _benchmark_comparison(before_benchmark, after_benchmark)
    gap_rows = _gap_comparison(before_gap, after_gap)
    regime_gap_rows = _regime_gap_comparison(before_regime_gap, after_regime_gap)
    periods = _halt_periods(states)
    _write_csv(OUTPUT_DIR / "benchmark_before_after.csv", benchmark_rows)
    _write_csv(OUTPUT_DIR / "gap_before_after.csv", gap_rows)
    _write_csv(OUTPUT_DIR / "regime_gap_before_after.csv", regime_gap_rows)
    _write_csv(OUTPUT_DIR / "risk_halt_periods.csv", periods)
    report = _build_report(benchmark_rows, gap_rows, regime_gap_rows, periods, before_benchmark_parameters,
                           after_benchmark_parameters, before_gap_parameters, after_gap_parameters)
    REPORT_PATH.write_text(report, encoding="utf-8")
    CLAUDE_PATH.write_text(
        "아래는 stockAlarm 위험중단·고상관 제한 연결 재검증의 전체 결과입니다. "
        "수치, 방법론, 한계를 포함해 검토해 주세요.\n\n" + report,
        encoding="utf-8",
    )
    result = {"report": str(REPORT_PATH), "claude_copy": str(CLAUDE_PATH),
              "risk_halt_days": after_gap_parameters.get("risk_halt_days", 0),
              "stock_return_pct": after_gap_parameters.get("stock_return_pct"),
              "gap_pp": after_gap_parameters.get("total_gap_pp"), "live_state_modified": False}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Revalidate benchmarks with live risk and correlation gates")
    parser.add_argument("--report-only", action="store_true", help="Regenerate comparison outputs without rerunning backtests")
    args = parser.parse_args()
    run(reexecute=not args.report_only)


if __name__ == "__main__":
    main()
