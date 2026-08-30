from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .benchmark_comparison import PortfolioSimulator
from .risk_release_backtest import drawdown_lock_evidence, trigger_diagnostics
from .strategy_learning import active_weights
from .validation_backtest import BacktestEngine, DATA_DIR, REPORT_DIR

OUTPUT = REPORT_DIR / "market_filter_risk_revalidation"
REPORT = REPORT_DIR / "MARKET_FILTER_RISK_REVALIDATION_REPORT.md"
OLD_OUTPUT = REPORT_DIR / "risk_release_variants"
TRIGGERS = ("daily_loss_limit", "weekly_loss_limit", "drawdown_limit", "exposure_limit")


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def raw_reasons(row: dict) -> set[str]:
    return set(filter(None, str(row.get("risk_raw_reason", row.get("risk_reason", ""))).split(",")))


def trigger_transition_events(states: list[dict]) -> list[dict]:
    events, previous = [], set()
    for row in states:
        current = raw_reasons(row)
        for trigger in sorted(current - previous):
            events.append({"date": row["date"], "trigger": trigger, "event": "entered", "equity": row["equity"],
                           "cash_weight_pct": round(float(row["cash_weight"]) * 100, 4),
                           "drawdown_pct": row.get("drawdown_pct"), "exposure_pct": row.get("exposure_pct")})
        for trigger in sorted(previous - current):
            events.append({"date": row["date"], "trigger": trigger, "event": "released", "equity": row["equity"],
                           "cash_weight_pct": round(float(row["cash_weight"]) * 100, 4),
                           "drawdown_pct": row.get("drawdown_pct"), "exposure_pct": row.get("exposure_pct")})
        previous = current
    return events


def regime_distribution(states: list[dict], regimes: dict[str, str]) -> list[dict]:
    rows = []
    for regime in ("bull", "bear", "sideways"):
        scoped = [row for row in states if regimes.get(str(row["date"])) == regime]
        counts = Counter(reason for row in scoped for reason in raw_reasons(row))
        for trigger in TRIGGERS:
            rows.append({"regime": regime, "trigger": trigger, "trading_days": len(scoped),
                         "active_days": counts[trigger],
                         "active_share_pct": round(counts[trigger] / len(scoped) * 100, 4) if scoped else 0.0})
    return rows


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    result = ["| " + " | ".join(label for _key, label in columns) + " |",
              "|" + "|".join("---" for _ in columns) + "|"]
    result.extend("| " + " | ".join(str(row.get(key, "")) for key, _label in columns) + " |" for row in rows)
    return result


def run(data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR) -> dict:
    old_triggers = _read_csv(OLD_OUTPUT / "risk_release_trigger_diagnostics.csv")
    old_lock = json.loads((OLD_OUTPUT / "drawdown_lock_evidence.json").read_text(encoding="utf-8"))
    engine = BacktestEngine(data_dir, report_dir, score_weights=active_weights())
    simulator = PortfolioSimulator(engine, 100_000_000, 10, apply_market_exposure_limit=True)
    result = simulator.run("stock_alarm", simulator.build_candidate_cache(), 20260828)
    states = result.daily_state
    new_triggers = trigger_diagnostics(states)
    new_lock = drawdown_lock_evidence(result)
    comparisons = []
    for trigger in TRIGGERS:
        old = next(row for row in old_triggers if row["trigger"] == trigger)
        new = next(row for row in new_triggers if row["trigger"] == trigger)
        comparisons.append({"trigger": trigger, "before_active_days": int(old["active_days"]),
                            "after_active_days": int(new["active_days"]),
                            "before_max_streak": int(old["max_consecutive_days"]),
                            "after_max_streak": int(new["max_consecutive_days"]),
                            "after_episodes": int(new["episodes"])})
    events = trigger_transition_events(states)
    regimes = regime_distribution(states, engine.regimes)
    drawdown_exists = next(row for row in comparisons if row["trigger"] == "drawdown_limit")["after_active_days"] > 0
    long_lock = next(row for row in comparisons if row["trigger"] == "drawdown_limit")["after_max_streak"] >= 100
    verdict = ("시장필터 연결 후에도 100거래일 이상의 drawdown 장기잠금이 재현되어 해제정책 재검증이 필요합니다."
               if long_lock else
               "시장필터 연결 후 drawdown 100거래일 이상 장기잠금이 재현되지 않았습니다. 이전 8개 해제정책·감쇠형 결과는 새 baseline의 문제를 설명하지 못하므로 즉시 운영 후보로 사용할 근거가 없고, 현재로서는 해제정책 variant 전체 재실행보다 시장필터 연결 상태의 지속 모니터링이 우선입니다.")
    detail = (f"새 최장 drawdown 구간은 `{new_lock['longest_start']}`~`{new_lock['longest_end']}` "
              f"{new_lock['consecutive_days']}거래일입니다. 당시 최고수위는 {new_lock['high_water']:,.0f}원, "
              f"90% 경계는 {new_lock['release_boundary_90pct']:,.0f}원입니다."
              if drawdown_exists else "새 baseline에서는 최고수위 대비 -10% 이하인 drawdown_limit 활성일이 한 번도 없었습니다.")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    _write_csv(OUTPUT / "daily_risk_state.csv", states)
    _write_csv(OUTPUT / "trigger_events.csv", events)
    _write_csv(OUTPUT / "trigger_before_after.csv", comparisons)
    _write_csv(OUTPUT / "regime_trigger_distribution.csv", regimes)
    (OUTPUT / "drawdown_lock_after.json").write_text(json.dumps(new_lock, ensure_ascii=False, indent=2), encoding="utf-8")
    parameters = {"created_at": datetime.now().isoformat(timespec="seconds"), "actual_start_date": simulator.days[0],
                  "actual_end_date": simulator.days[-1], "trading_days": len(simulator.days), "seed": 20260828,
                  "market_exposure_limit_enabled": True, "market_limits_pct": {"aggressive": 70, "neutral": 40, "defensive": 10},
                  "risk_evaluator": "stock_alarm.portfolio_risk.evaluate_risk_state via PortfolioSimulator",
                  "old_baseline_source": str(OLD_OUTPUT), "live_state_modified": False}
    (OUTPUT / "parameters.json").write_text(json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 시장필터 연결 후 위험중단 재검증", "", "## 결론", "", f"**{verdict}**", "", detail, "",
             "이 리포트는 2022-06-30~2026-08-28 격리 OHLCV와 고정 시드로 재생한 진단 결과입니다. 운영 config·DB·가상계좌·실주문 API는 수정하지 않았습니다.", "",
             "## 연결 전후 트리거 비교", "", *_table(comparisons, [("trigger","트리거"),("before_active_days","연결전 활성일"),("after_active_days","연결후 활성일"),("before_max_streak","연결전 최장"),("after_max_streak","연결후 최장"),("after_episodes","연결후 횟수")]), "",
             "연결 전 수치는 기존 `risk_release_variants`의 variant_A 저장 결과이며, 연결 후 수치는 시장필터가 실제 주문일 포트폴리오 노출을 70%/40%/10%로 제한하는 현재 `PortfolioSimulator` 결과입니다.", "",
             "## 기존 894일 잠금과 새 baseline", "", *_table([{"metric":"전체 위험중단 최장", "before": old_lock.get("all_halt_consecutive_days"), "after": new_lock.get("all_halt_consecutive_days")},
              {"metric":"drawdown 최장", "before": old_lock.get("consecutive_days"), "after": new_lock.get("consecutive_days")},
              {"metric":"drawdown 시작", "before": old_lock.get("longest_start"), "after": new_lock.get("longest_start") or "없음"},
              {"metric":"drawdown 종료", "before": old_lock.get("longest_end"), "after": new_lock.get("longest_end") or "없음"},
              {"metric":"잠금 중 평균현금%", "before": old_lock.get("average_cash_weight_pct"), "after": new_lock.get("average_cash_weight_pct")}], [("metric","항목"),("before","연결전"),("after","연결후")]), "",
             "## 국면별 위험상태 분포", "", *_table(regimes, [("regime","국면"),("trigger","트리거"),("trading_days","거래일"),("active_days","활성일"),("active_share_pct","비율%")]), "",
             "## 판정", "", verdict, "", "이 판정은 시장필터가 구조적으로 모든 미래 낙폭을 막는다는 뜻이 아닙니다. 이번 고정 데이터·현재 후보군에서는 과거 894일 잠금의 선행 원인이 제거됐는지를 검증한 결과이며, 생존편향·일봉 체결 근사·장중 위험평가 차이는 남습니다.", "",
             "## 재현과 감사", "", f"- 일별 상태: `{OUTPUT / 'daily_risk_state.csv'}`", f"- 진입·해제 이벤트: `{OUTPUT / 'trigger_events.csv'}`", f"- 파라미터: `{OUTPUT / 'parameters.json'}`", "- 위험상태는 별도 복제 함수가 아니라 `PortfolioSimulator`가 라이브 `portfolio_risk.evaluate_risk_state`를 직접 호출해 산출했습니다.", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return {"report": str(REPORT), "drawdown_active": drawdown_exists, "drawdown_long_lock": long_lock,
            "drawdown_max_streak": next(row for row in comparisons if row["trigger"] == "drawdown_limit")["after_max_streak"],
            "live_state_modified": False}


def main() -> None:
    argparse.ArgumentParser(description="Revalidate portfolio risk lock after market-breadth sizing connection").parse_args()
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
