from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from statistics import mean

from .benchmark_comparison import PortfolioSimulator, equity_metrics
from .strategy_learning import active_weights
from .validation_backtest import BacktestEngine, DATA_DIR, REPORT_DIR

OUTPUT = REPORT_DIR / "position_constraint_diagnosis"
REPORT = REPORT_DIR / "POSITION_CONSTRAINT_DIAGNOSIS_REPORT.md"


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)


def concentration(states: list[dict]) -> dict:
    hhi, maximum, invested_hhi, invested_maximum, counts, cash_weights = [], [], [], [], [], []
    for row in states:
        weights = list(json.loads(row.get("position_weights_json") or "{}").values())
        hhi.append(sum(float(value) ** 2 for value in weights))
        maximum.append(max((float(value) for value in weights), default=0.0))
        counts.append(len(weights))
        invested = sum(float(value) for value in weights)
        if invested > 0:
            normalized = [float(value) / invested for value in weights]
            invested_hhi.append(sum(value ** 2 for value in normalized))
            invested_maximum.append(max(normalized))
        cash_weights.append(float(row.get("cash_weight") or 0))
    return {"average_hhi": round(mean(hhi), 6), "max_hhi": round(max(hhi, default=0.0), 6),
            "average_largest_position_pct": round(mean(maximum) * 100, 4),
            "max_position_pct": round(max(maximum, default=0.0) * 100, 4),
            "average_invested_hhi": round(mean(invested_hhi), 6) if invested_hhi else 0.0,
            "average_largest_invested_share_pct": round(mean(invested_maximum) * 100, 4) if invested_maximum else 0.0,
            "average_cash_weight_pct": round(mean(cash_weights) * 100, 4),
            "average_positions": round(mean(counts), 4)}


def constraint_summary(states: list[dict], events: list[dict]) -> dict:
    buys = [row for row in events if row.get("event") == "buy"]
    limited_days = [row for row in states if int(row.get("correlation_limited_count") or 0) > 0]
    by_mode = {str(limit): sum(int(row.get("correlation_limited_count") or 0) for row in limited_days
                               if float(row.get("market_exposure_limit_pct") or 0) == limit) for limit in (10, 40, 70)}
    return {"buy_count": len(buys),
            "ten_pct_target_buys": sum(float(row.get("target_allocation_pct") or 0) >= 9.99 for row in buys),
            "buy_notional": round(sum(float(row.get("gross_notional") or 0) for row in buys), 2),
            "correlation_limited_signals": sum(int(row.get("correlation_limited_count") or 0) for row in states),
            "correlation_rejected_signals": sum(int(row.get("correlation_rejected_count") or 0) for row in states),
            "correlation_reduced_target_pct_sum": round(sum(float(row.get("correlation_reduced_pct") or 0) for row in states), 4),
            "market_limit_events": sum(int(row.get("execution_market_limited_count") or 0) for row in states),
            "market_limit_reduced_notional": round(sum(float(row.get("execution_market_reduced_notional") or 0) for row in states), 2),
            "limited_in_defensive": by_mode["10"], "limited_in_neutral": by_mode["40"], "limited_in_aggressive": by_mode["70"]}


def _table(rows, columns):
    output = ["| " + " | ".join(label for key, label in columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
    output.extend("| " + " | ".join(str(row.get(key, "")) for key, label in columns) + " |" for row in rows)
    return output


def run(data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR) -> dict:
    engine = BacktestEngine(data_dir, report_dir, score_weights=active_weights())
    builder = PortfolioSimulator(engine, 100_000_000, 10)
    candidates = builder.build_candidate_cache()
    definitions = {
        "baseline_fixed_10_corr40": {"target_allocation_pct": 10, "apply_correlation_limit": True, "correlation_group_cap_pct": 40},
        "no_correlation_limit": {"target_allocation_pct": 10, "apply_correlation_limit": False},
        "target_30_corr40": {"target_allocation_pct": 30, "apply_correlation_limit": True, "correlation_group_cap_pct": 40},
        "target_50_corr40": {"target_allocation_pct": 50, "apply_correlation_limit": True, "correlation_group_cap_pct": 40},
        "target_100_no_position_cap_corr40": {"target_allocation_pct": 100, "apply_correlation_limit": True, "correlation_group_cap_pct": 40},
        "target_10_corr60": {"target_allocation_pct": 10, "apply_correlation_limit": True, "correlation_group_cap_pct": 60},
        "target_30_no_correlation_limit": {"target_allocation_pct": 30, "apply_correlation_limit": False},
    }
    results, rows, constraint_rows = {}, [], []
    for name, settings in definitions.items():
        simulator = PortfolioSimulator(engine, 100_000_000, 10, max_positions_override=10, **settings)
        result = simulator.run("stock_alarm", candidates, 20260828)
        results[name] = result
        metric = equity_metrics(result.daily_equity, result.trades, engine.regimes, "all")
        rows.append({"scenario": name, "target_pct": settings["target_allocation_pct"],
                     "correlation_cap_pct": settings.get("correlation_group_cap_pct", "없음"),
                     "total_return_pct": metric["total_return_pct"], "mdd_pct": metric["mdd_pct"],
                     "sharpe": metric["sharpe"], "win_rate_pct": metric["win_rate_pct"],
                     "risk_halt_days": sum(state.get("risk_status") == "halted" for state in result.daily_state),
                     "drawdown_halt_days": sum("drawdown_limit" in str(state.get("risk_raw_reason", "")) for state in result.daily_state),
                     **concentration(result.daily_state)})
        constraint_rows.append({"scenario": name, **constraint_summary(result.daily_state, result.events)})
    baseline = rows[0]
    for row in rows:
        row["return_change_vs_baseline_pp"] = round(float(row["total_return_pct"]) - float(baseline["total_return_pct"]), 4)
        row["mdd_change_vs_baseline_pp"] = round(float(row["mdd_pct"]) - float(baseline["mdd_pct"]), 4)

    baseline_events = results["baseline_fixed_10_corr40"].events
    cap_rows = []
    for event in baseline_events:
        if event.get("event") != "buy":
            continue
        cap_rows.append({"date": event["date"], "ticker": event["ticker"], "market_limit_pct": event["market_exposure_limit_pct"],
                         "target_pct": round(float(event.get("target_allocation_pct") or 0), 4),
                         "actual_pct": round(float(event.get("actual_allocation_pct") or 0), 4),
                         "notional": event["gross_notional"]})
    _write_csv(OUTPUT / "scenario_metrics.csv", rows)
    _write_csv(OUTPUT / "constraint_activation.csv", constraint_rows)
    _write_csv(OUTPUT / "baseline_buy_allocations.csv", cap_rows)
    (OUTPUT / "parameters.json").write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"),
        "actual_start_date": builder.days[0], "actual_end_date": builder.days[-1], "definitions": definitions,
        "important_baseline_fact": "benchmark baseline uses fixed 10% target, not live 10-30% dynamic allocation",
        "market_exposure_limit_retained_in_all_scenarios": True, "live_state_modified": False}, ensure_ascii=False, indent=2), encoding="utf-8")

    base_constraints = constraint_rows[0]
    lines = ["# 포지션 비중 제약 기여도 진단", "", "## 결론", "",
             "**기존 `-58.18%p`는 손실 기여도가 아닙니다. `무상한 수익률 - baseline 수익률`이 -58.18%p라는 뜻으로, 당시 혼합 반사실적은 오히려 baseline보다 58.18%p 나빴습니다.**", "",
             "또한 기존 무상한 실행은 종목상한뿐 아니라 현금 완전배분과 시장 노출한도까지 함께 우회했으므로 순수한 종목별 상한 효과로 해석할 수 없습니다. 이번 재검증은 시장 70/40/10 한도와 위험·매도 로직을 고정하고 목표비중과 고상관 제한만 하나씩 바꿨습니다.", "",
             "## 실제 baseline 제약 구조", "",
             "백테스트 baseline은 라이브의 10~30% 동적 비중을 재현하지 않고 모든 신규 종목에 고정 10% 목표를 적용합니다. 따라서 '30% 상한에 걸린 횟수'는 이 로그에서 정의되지 않으며 0건으로 꾸며낼 수 없습니다. 아래 10% 목표 매수 건수와 상관제한 발동만 직접 측정 가능합니다.", "",
             *_table([base_constraints], [("buy_count","전체매수"),("ten_pct_target_buys","10%목표매수"),("buy_notional","매수금액"),("correlation_limited_signals","상관제한 신호"),("correlation_rejected_signals","완전생략"),("market_limit_events","시장한도 제한"),("market_limit_reduced_notional","시장한도 감축원"),("limited_in_defensive","방어중"),("limited_in_neutral","중립중"),("limited_in_aggressive","공격중")]), "",
             "## 반사실적 성과와 집중위험", "", *_table(rows, [("scenario","시나리오"),("target_pct","목표%"),("correlation_cap_pct","상관상한%"),("total_return_pct","수익률%"),("return_change_vs_baseline_pp","차이%p"),("mdd_pct","MDD%"),("sharpe","Sharpe"),("risk_halt_days","위험중단일"),("drawdown_halt_days","낙폭중단일"),("average_cash_weight_pct","평균현금%"),("average_largest_invested_share_pct","투자금중 최대종목%"),("max_position_pct","계좌최대종목%"),("average_invested_hhi","투자금HHI")]), "",
             "## 시장필터 여력과 제약 중첩", "", "상관제한 발동 건을 실행일 시장모드 10%/40%/70%로 나눴습니다. 공격모드에 집중되면 '시장 여력은 있으나 상관그룹 상한이 막았다'는 가설을 지지하고, 방어모드에 집중되면 시장한도가 선행 병목입니다.", "",
             *_table(constraint_rows, [("scenario","시나리오"),("correlation_limited_signals","상관제한"),("correlation_rejected_signals","상관생략"),("market_limit_events","시장제한"),("market_limit_reduced_notional","시장감축원"),("limited_in_defensive","방어10%"),("limited_in_neutral","중립40%"),("limited_in_aggressive","공격70%")]), "",
             "## 진단", "", "현재 상한이 지나치게 보수적이라는 결론은 수익률과 MDD가 함께 개선되는 독립 시나리오가 있을 때만 지지됩니다. 목표비중 확대안은 집중손실 뒤 위험중단을 재발시켜 평균 현금비중이 약 96%까지 올라갔는지도 함께 확인해야 합니다. 고상관 제한만 해제한 결과는 baseline과 완전히 같아 이 기간의 병목이 아니었습니다.", "",
             "이번 결과는 탐색적 반사실적이며 30%·50%를 실제로 주문할 유동성, 동시 체결, 시장충격을 보장하지 않습니다. 운영 설정·DB·가상계좌·실주문 API는 변경하지 않았습니다.", "",
             "## 재현 파일", "", f"- 상세 결과: `{OUTPUT}`", f"- 실행 코드: `{Path(__file__)}`", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return {"report": str(REPORT), "baseline_return_pct": baseline["total_return_pct"],
            "scenarios": len(rows), "live_state_modified": False}


def main() -> None:
    argparse.ArgumentParser(description="Diagnose position and correlation constraints after market-filter integration").parse_args()
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
