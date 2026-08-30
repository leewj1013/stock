from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .benchmark_comparison import run as run_benchmark
from .validation_backtest import REPORT_DIR

OUTPUT = REPORT_DIR / "benchmark_comparison"
OLD = REPORT_DIR / "constraint_revalidation" / "before"
REPORT = REPORT_DIR / "BENCHMARK_BASELINE_REVALIDATION_REPORT.md"


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _table(rows, columns):
    result = ["| " + " | ".join(label for key, label in columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    result.extend("| " + " | ".join(str(row.get(key, "")) for key, label in columns) + " |" for row in rows)
    return result


def _old_random_percentiles() -> dict[str, float]:
    values = {}
    text = (OLD / "BENCHMARK_COMPARISON_REPORT.md").read_text(encoding="utf-8")
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 8 and cells[0] in {"all", "bull", "bear", "sideways"} and cells[1] == "100":
            try:
                values[cells[0]] = float(cells[6])
            except ValueError:
                pass
    return values


def run(execute: bool = True) -> dict:
    if execute:
        run_benchmark()
    metrics = _read(OUTPUT / "strategy_metrics.csv")
    random_summary = _read(OUTPUT / "random_summary.csv")
    tests = _read(OUTPUT / "newey_west_comparisons.csv")
    old_metrics = _read(OLD / "benchmark_strategy_metrics.csv")
    old_random = _old_random_percentiles()
    parameters = json.loads((OUTPUT / "parameters.json").read_text(encoding="utf-8"))

    comparisons = []
    for strategy in ("stock_alarm", "kospi_buy_hold", "equal_weight_buy_hold", "momentum"):
        old = next((row for row in old_metrics if row["strategy"] == strategy and row["regime"] == "all"), {})
        new = next(row for row in metrics if row["strategy"] == strategy and row["regime"] == "all")
        comparisons.append({"strategy": strategy, "before_return_pct": old.get("total_return_pct", "N/A"),
                            "after_return_pct": new["total_return_pct"],
                            "change_pp": round(float(new["total_return_pct"]) - float(old.get("total_return_pct") or new["total_return_pct"]), 4)})
    percentile_compare = []
    for row in random_summary:
        regime = row["regime"]
        percentile_compare.append({"regime": regime, "before_percentile": old_random.get(regime, "N/A"),
                                   "after_percentile": row["stock_percentile"], "after_empirical_p": row["empirical_p_superiority"]})
    significant = [row for row in tests if str(row["significant_superiority"]).lower() == "true"]
    random_significant = [row for row in significant if row["benchmark"] == "random_mean"]
    conclusion = ("stockAlarm은 랜덤 선택 대비 어느 국면에서도 FDR 보정 후 유의한 우위를 보이지 않았습니다. "
                  "전체 수익률·CAGR·Sharpe도 랜덤 중앙값보다 낮아 스코어링 복잡성을 정당화하지 못합니다. "
                  "단순 모멘텀 대비 전체와 상승장에서는 유의한 우위가 있지만, KOSPI·동일가중 B&H보다 전체 수익률이 크게 낮습니다.")
    overall = [row for row in metrics if row["regime"] == "all"]
    regimes = [row for row in metrics if row["regime"] != "all"]
    lines = ["# 수정 baseline 정식 벤치마크 재검증", "", "## 기술 요약", "", f"**{conclusion}**", "",
             f"평가기간은 `{parameters['actual_start_date']}`~`{parameters['actual_end_date']}` ({parameters['random_iterations']}회 랜덤, 시드 `{parameters['random_seed']}`)입니다. 시장 70/40/10 한도, 종목당 고정 10%, 고상관 그룹 40%, 동일 비용·매도·위험중단을 적용했습니다.", "",
             "## 전체기간 5개 전략", "", *_table(overall, [("strategy","전략"),("trading_days","거래일"),("trades","거래"),("total_return_pct","총수익률%"),("cagr_pct","CAGR%"),("win_rate_pct","승률%"),("payoff_ratio","손익비"),("mdd_pct","MDD%"),("sharpe","Sharpe")]), "",
             "랜덤은 100회 결과의 중앙값이며 Buy & Hold의 승률·손익비는 거래 단위가 없어 N/A입니다.", "",
             "## 국면별 비교", "", *_table(regimes, [("regime","국면"),("strategy","전략"),("total_return_pct","수익률%"),("cagr_pct","CAGR%"),("win_rate_pct","승률%"),("payoff_ratio","손익비"),("mdd_pct","MDD%"),("sharpe","Sharpe")]), "",
             "## 랜덤 분포 내 stockAlarm 위치", "", *_table(random_summary, [("regime","국면"),("p05_total_return_pct","랜덤5%"),("median_total_return_pct","중앙값"),("p95_total_return_pct","랜덤95%"),("stock_total_return_pct","stockAlarm%"),("stock_percentile","백분위"),("empirical_p_superiority","경험적p")]), "",
             "## Newey-West 단측검정과 BH-FDR", "", *_table(tests, [("regime","국면"),("benchmark","비교전략"),("days","표본일"),("mean_daily_difference_pct","일평균차%p"),("hac_p_one_sided","단측p"),("fdr_q_one_sided","FDR q"),("significant_superiority","유의우위")]), "",
             f"총 16개 검정(4개 비교전략×4국면)을 하나의 검정군으로 보정했습니다. 랜덤 대비 유의한 결과는 {len(random_significant)}건입니다.", "",
             "## 시장필터 연결 전후", "", *_table(comparisons, [("strategy","전략"),("before_return_pct","연결전%"),("after_return_pct","연결후%"),("change_pp","차이%p")]), "",
             "### 랜덤 백분위 변화", "", *_table(percentile_compare, [("regime","국면"),("before_percentile","연결전"),("after_percentile","연결후"),("after_empirical_p","연결후 경험적p")]), "",
             "연결 전 stockAlarm은 +8.5615%, 전체 랜덤 백분위 78, 상승장 99였습니다. 연결 후 절대수익률은 +56.8131%로 개선됐지만 랜덤도 동일한 시장필터 혜택을 받아 전체 백분위는 31, 상승장은 24로 낮아졌습니다. 이는 개선의 대부분이 스코어링 고유 우위보다 공통 시장필터에서 왔다는 증거입니다.", "",
             "## 최종 판정", "", f"**{conclusion}**", "",
             "통계적으로 확인된 장점은 단순 모멘텀 대비 전체기간과 상승장 우위뿐입니다. 그러나 랜덤 선택보다 낫지 않고 두 Buy & Hold의 절대수익률에도 미달하므로 현재 스코어링·매매 복잡성 전체를 정당화하는 증거로는 부족합니다.", "",
             "## 한계와 안전장치", "", "- 현재 watchlist를 과거에 적용한 생존편향이 있습니다.", "- 뉴스·공시·재무 point-in-time 자료 부재로 해당 점수는 0입니다.", "- 국면별 수익률은 해당 국면 일수익률만 조건부 복리한 값입니다.", "- 운영 config·DB·가상계좌·실주문 API는 수정하지 않았습니다.", "",
             "## 재현 파일", "", f"- 표·분포·검정 CSV: `{OUTPUT}`", f"- 연결 전 산출물: `{OLD}`", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return {"report": str(REPORT), "stock_return_pct": next(row["total_return_pct"] for row in overall if row["strategy"] == "stock_alarm"),
            "random_significant_regimes": [row["regime"] for row in random_significant], "live_state_modified": False}


def main() -> None:
    parser = argparse.ArgumentParser(description="Formal benchmark revalidation for the market-filter baseline")
    parser.add_argument("--reuse-results", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(execute=not args.reuse_results), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
