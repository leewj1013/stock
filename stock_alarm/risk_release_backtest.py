from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean, median

from .app import load_env
from .backtest_data import BENCHMARK, DATA_DIR, REPORT_DIR
from .benchmark_comparison import PortfolioResult, PortfolioSimulator, daily_return_map, equity_metrics
from .gap_decomposition import weighted_cash_missed_returns
from .risk_release_policy import CONFIG_PATH, load_risk_release_variants
from .statistical_validation import benjamini_hochberg, newey_west_mean_test
from .strategy_learning import active_weights
from .validation_backtest import BacktestEngine


OUTPUT_DIR = REPORT_DIR / "risk_release_variants"
REPORT_PATH = REPORT_DIR / "RISK_RELEASE_VARIANT_REPORT.md"


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
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    def fmt(value):
        if value is None or value == "":
            return "N/A"
        if isinstance(value, bool):
            return "Y" if value else "N"
        return f"{value:.4f}" if isinstance(value, float) else str(value)
    return [
        "| " + " | ".join(label for _key, label in columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
        *["| " + " | ".join(fmt(row.get(key)) for key, _label in columns) + " |" for row in rows],
    ]


def _truth(value) -> bool:
    return value is True or str(value).lower() in {"true", "1", "y"}


def _max_streak(states: list[dict], predicate) -> int:
    longest = current = 0
    for row in states:
        if predicate(row):
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _streaks(states: list[dict], reason: str) -> list[int]:
    output, current = [], 0
    for row in states:
        reasons = set(filter(None, str(row.get("risk_raw_reason", row.get("risk_reason", ""))).split(",")))
        if reason in reasons:
            current += 1
        elif current:
            output.append(current)
            current = 0
    if current:
        output.append(current)
    return output


def trigger_diagnostics(states: list[dict]) -> list[dict]:
    definitions = {
        "daily_loss_limit": ("전일 종가 대비 당일 계좌자산 수익률 ≤ -2%", "다음 평가에서 일간수익률 > -2%"),
        "weekly_loss_limit": ("주 시작 기준 계좌자산 수익률 ≤ -5%", "같은 주 -5% 위로 회복하거나 다음 주 기준 갱신"),
        "drawdown_limit": ("고정 최고수위 대비 계좌자산 낙폭 ≤ -10%", "최고수위의 90% 위로 회복(현행은 새 최고점까지 회복할 필요 없음)"),
        "exposure_limit": ("보유종목 평가액/총자산 > 70%", "매도·가격변동·현금증가로 보유비중 ≤ 70%"),
    }
    rows = []
    for reason, (entry, release) in definitions.items():
        streaks = _streaks(states, reason)
        rows.append({
            "trigger": reason, "entry_condition": entry, "release_condition": release,
            "active_days": sum(streaks), "episodes": len(streaks),
            "max_consecutive_days": max(streaks, default=0),
            "median_episode_days": round(median(streaks), 2) if streaks else 0,
            "natural_escape_path": {
                "daily_loss_limit": "전일 기준 자동 재설정",
                "weekly_loss_limit": "주간 기준 자동 재설정",
                "drawdown_limit": "현금화 후 수익원 부재 시 구조적 잠금 위험",
                "exposure_limit": "기존 종목 매도가 계속되어 자연 해제 가능",
            }[reason],
        })
    return rows


def drawdown_lock_evidence(result: PortfolioResult) -> dict:
    states = result.daily_state
    longest_start = longest_end = ""
    longest_rows: list[dict] = []
    current: list[dict] = []
    for row in states:
        raw = set(filter(None, str(row.get("risk_raw_reason", row.get("risk_reason", ""))).split(",")))
        if row.get("risk_status") == "halted" and "drawdown_limit" in raw:
            current.append(row)
        else:
            if len(current) > len(longest_rows):
                longest_rows = current
            current = []
    if len(current) > len(longest_rows):
        longest_rows = current
    if longest_rows:
        longest_start, longest_end = longest_rows[0]["date"], longest_rows[-1]["date"]
    start = longest_rows[0] if longest_rows else {}
    high_water = float(start.get("equity") or 0) / (1 + float(start.get("drawdown_pct") or 0) / 100) if start else 0.0
    release_boundary = high_water * 0.90
    maximum_after_trigger = max((float(row.get("equity") or 0) for row in longest_rows), default=0.0)
    buys_after = [event for event in result.events if event.get("event") == "buy" and str(event.get("date")) >= longest_start] if longest_start else []
    all_halt_streak = _max_streak(states, lambda row: row.get("risk_status") == "halted")
    all_halt_start = ""
    current_start, current_length = "", 0
    for row in states:
        if row.get("risk_status") == "halted":
            if not current_start:
                current_start = str(row["date"])
            current_length += 1
            if current_length == all_halt_streak:
                all_halt_start = current_start
        else:
            current_start, current_length = "", 0
    full_cash_day = next((str(row["date"]) for row in longest_rows if float(row.get("cash_weight") or 0) >= 0.9999), "")
    return {
        "all_halt_start": all_halt_start, "all_halt_consecutive_days": all_halt_streak,
        "longest_start": longest_start, "longest_end": longest_end, "consecutive_days": len(longest_rows),
        "high_water": round(high_water, 2), "release_boundary_90pct": round(release_boundary, 2),
        "trigger_equity": round(float(start.get("equity") or 0), 2),
        "maximum_equity_during_lock": round(maximum_after_trigger, 2),
        "shortfall_to_release_boundary": round(max(0.0, release_boundary - maximum_after_trigger), 2),
        "average_cash_weight_pct": round(mean(float(row.get("cash_weight") or 0) for row in longest_rows) * 100, 4) if longest_rows else 0.0,
        "ending_cash_weight_pct": round(float(longest_rows[-1].get("cash_weight") or 0) * 100, 4) if longest_rows else 0.0,
        "buy_events_during_lock": len(buys_after),
        "first_full_cash_day": full_cash_day,
        "remaining_sell_events_during_lock": sum(
            str(event.get("event", "")).startswith("sell") and longest_start <= str(event.get("date")) <= longest_end
            for event in result.events
        ) if longest_rows else 0,
    }


def risk_statistics(states: list[dict]) -> dict:
    return {
        "halt_days": sum(row.get("risk_status") == "halted" for row in states),
        "reduced_days": sum(row.get("risk_status") == "reduced" for row in states),
        "max_halt_streak": _max_streak(states, lambda row: row.get("risk_status") == "halted"),
        "halt_entries": sum(row.get("risk_transition") == "halted" for row in states),
        "halt_releases": sum(row.get("risk_transition") == "resumed" for row in states),
        "ended_halted": bool(states and states[-1].get("risk_status") == "halted"),
    }


def _scope_states(states: list[dict], regimes: dict[str, str], scope: str) -> list[dict]:
    return states if scope == "all" else [row for row in states if regimes.get(str(row["date"])) == scope]


def _scope_cash_effect(states: list[dict], market_returns: dict[str, float], regimes: dict[str, str], scope: str) -> float:
    rows = weighted_cash_missed_returns(states, market_returns)
    if scope != "all":
        rows = [row for row in rows if regimes.get(str(row["date"])) == scope]
    return sum(float(row["missed_return"]) for row in rows) * 100


def build_report(parameters: dict, policies: list[dict], summary: list[dict], metrics: list[dict],
                 tests: list[dict], trigger_rows: list[dict], lock: dict, tradeoff: list[dict]) -> str:
    baseline = next(row for row in summary if row["variant"] == "variant_A")
    candidates = [row for row in summary if row["variant"] != "variant_A"]
    feasible = [row for row in candidates if row["escape_secured"] and row["bear_defense_retained"]]
    best = max(feasible, key=lambda row: (row["total_return_pct"], row["sharpe"] or -999), default=None)
    significance = next((row for row in tests if best and row["variant"] == best["variant"] and row["regime"] == "all"), None)
    proposal = (
        f"사람이 다음 검토 대상으로 우선 볼 안은 `{best['variant']}`입니다. 장기 잠금을 해소하면서 정의한 하락장 방어 유지 기준을 통과했습니다. "
        f"다만 baseline 대비 HAC/FDR 유의 개선 여부는 `{bool(significance and significance['significant_superiority'])}`이므로 운영 자동 적용 근거로 사용하면 안 됩니다."
        if best else
        "장기 잠금 해소와 하락장 방어 유지 기준을 동시에 통과한 안이 없어 현재 데이터로 운영 변경을 권할 수 없습니다."
    )
    lines = [
        "# 위험중단 해제정책 대안 백테스트", "", "## 기술 요약", "",
        f"현행 정책은 `{lock['all_halt_start']}`부터 `{lock['longest_end']}`까지 **{lock['all_halt_consecutive_days']}거래일** 연속 중단됐고, 이 중 `{lock['longest_start']}`부터 **{lock['consecutive_days']}거래일**은 낙폭 원시조건이 연속 유지됐습니다. 중단 구간 평균 현금은 **{lock['average_cash_weight_pct']:.2f}%**, 신규매수는 **{lock['buy_events_during_lock']}건**이었고, 최고수위 90% 해제선까지 최대 **{lock['shortfall_to_release_boundary']:,.0f}원** 부족했습니다.",
        f"{proposal}",
        "이 결과는 격리 백테스트의 판단 근거이며 운영 config·DB·가상계좌를 변경하지 않았습니다.", "",
        "## 현행 해제는 새 최고점이 아니라 90% 경계 회복이지만 현금 잠금으로 도달하지 못했다", "",
        "현재 공통 평가기는 매일 네 조건을 다시 계산합니다. 당일·주간 조건은 기준시점이 갱신되고, 보유비중은 매도가 계속되므로 자연 해제가 가능합니다. 반면 최고수위는 내려가지 않기 때문에 낙폭 중단 후 보유종목 매도가 진행되어 현금 비중이 높아지면 90% 경계까지 회복할 수익원이 사라집니다.", "",
    ]
    lines.extend(_table(trigger_rows, [
        ("trigger", "트리거"), ("entry_condition", "진입조건"), ("release_condition", "해제조건"),
        ("active_days", "활성일"), ("episodes", "에피소드"), ("max_consecutive_days", "최장연속"),
        ("natural_escape_path", "자연 해제 경로"),
    ]))
    lines.extend(["", "### 894일 전체 중단 중 893일은 낙폭조건이 연속 유지됐다", ""])
    lines.extend(_table([lock], [
        ("all_halt_start", "전체중단시작"), ("all_halt_consecutive_days", "전체연속일"),
        ("longest_start", "낙폭시작"), ("longest_end", "종료"), ("consecutive_days", "낙폭연속일"),
        ("high_water", "최고수위원"), ("release_boundary_90pct", "90% 해제선원"),
        ("maximum_equity_during_lock", "중단중 최대자산원"), ("shortfall_to_release_boundary", "해제선 부족원"),
        ("average_cash_weight_pct", "평균현금%"), ("first_full_cash_day", "현금100%일"),
        ("buy_events_during_lock", "신규매수"),
        ("remaining_sell_events_during_lock", "매도계속"),
    ]))
    lines.extend(["", "## 대안은 반등·시간·히스테리시스·축소진입을 분리했다", ""])
    lines.extend(_table(policies, [
        ("name", "Variant"), ("label", "정책"), ("mode", "방식"), ("rationale", "설계 근거"),
        ("rebound_pct", "반등%"), ("cooldown_days", "쿨다운"),
        ("release_drawdown_pct", "해제낙폭%"), ("reentry_scale", "재진입배수"),
    ]))
    lines.extend(["", "## 전체 성과는 탈출 가능성과 하락 방어를 함께 봐야 한다", "",
                  "탈출 확보는 평가 종료 시 중단이 아니고 최장 중단이 baseline보다 짧은 경우입니다. 하락장 방어 유지는 하락장 MDD가 baseline보다 2%p 넘게 악화되지 않고 현금 방어효과의 80% 이상을 보존한 경우로 사전 정의했습니다.",
                  f"요청에 인용된 하락장 현금효과 `-30.79%p`는 위험게이트 연결 전 진단값입니다. 이번 동일조건 baseline A에서는 `-53.59%p`로 재산출됐으며, 대안의 80% 방어 기준은 최신 baseline A를 기준으로 적용했습니다.", ""])
    lines.extend(_table(summary, [
        ("variant", "Variant"), ("total_return_pct", "총수익률%"), ("mdd_pct", "MDD%"),
        ("sharpe", "Sharpe"), ("halt_days", "중단일"), ("reduced_days", "축소일"),
        ("max_halt_streak", "최장중단"), ("halt_entries", "진입"), ("halt_releases", "해제"),
        ("ended_halted", "종료시중단"), ("escape_secured", "탈출확보"),
        ("bear_mdd_pct", "하락장MDD%"), ("bear_cash_protection_pp", "하락장현금방어%p"),
        ("bull_return_pct", "상승장수익%"), ("bear_defense_retained", "방어유지"),
    ]))
    lines.extend(["", "## 국면별 성과는 상승 회복과 하락 방어의 교환관계를 보여준다", ""])
    lines.extend(_table(metrics, [
        ("variant", "Variant"), ("regime", "국면"), ("total_return_pct", "수익률%"),
        ("mdd_pct", "MDD%"), ("sharpe", "Sharpe"), ("cash_effect_pp", "현금효과%p"),
        ("halt_days", "중단일"), ("reduced_days", "축소일"),
    ]))
    lines.extend(["", "## 산점도 데이터는 하락장 MDD와 상승장 회복력을 직접 비교한다", "",
                  "아래 표와 `risk_release_tradeoff_scatter.csv`에서 x축은 하락장 MDD, y축은 상승장 수익률입니다. 우측(낙폭 절댓값이 작음)·상단일수록 유리하지만 현금 방어와 최장 중단도 함께 확인해야 합니다.", ""])
    lines.extend(_table(tradeoff, [
        ("variant", "Variant"), ("bear_mdd_pct", "x:하락장MDD%"), ("bull_return_pct", "y:상승장수익%"),
        ("bear_cash_protection_pp", "현금방어%p"), ("max_halt_streak", "최장중단"),
        ("total_return_pct", "전체수익%"),
    ]))
    lines.extend(["", "## Newey-West와 FDR 검정은 억지 승자를 만들지 않는다", "",
                  f"baseline 대비 {len(tests)}개 검정({len(candidates)}개 대안×4국면)을 하나의 가족으로 BH-FDR 보정했습니다. 평균 일수익률 차이가 양수이고 q-value가 {parameters['fdr_alpha']:.2f} 미만일 때만 유의 우위로 표시합니다.", ""])
    lines.extend(_table(tests, [
        ("variant", "Variant"), ("regime", "국면"), ("days", "표본일"),
        ("mean_daily_difference_pct", "일평균차%p"), ("hac_lag", "HAC lag"),
        ("hac_p_two_sided", "양측p"), ("hac_p_one_sided", "우위p"),
        ("fdr_q_one_sided", "FDR q"), ("significant_superiority", "유의우위"),
    ]))
    lines.extend(["", "## 판단 제안", "", f"**{proposal}**", "",
                  "- 통계적 유의성이 없으면 총수익률이 좋아 보여도 탐색적 후보로만 취급합니다.",
                  "- variant_D는 실제 baseline보다 더 엄격한 -5% 해제선이므로 잠금 해소책이 아니라 히스테리시스 음성 대조로 해석해야 합니다.",
                  "- 반등형은 같은 낙폭 에피소드에서 한 번 해제된 뒤 -10% 원시조건이 남아 있어도 즉시 재잠금하지 않습니다. 이는 탈출을 가능하게 하지만 재하락 위험을 키울 수 있습니다.",
                  "- 운영 적용 전에는 완전 미사용 기간과 장중 스냅샷 기반 재검증이 필요합니다.", "",
                  "## 범위·방법·안전장치", "",
                  f"- 평가기간: `{parameters['actual_start_date']}`~`{parameters['actual_end_date']}` ({parameters['trading_days']}거래일)",
                  "- 동일 후보군, 현재 활성 점수 가중치, 다음 거래일 시가, 10% 정상 슬롯, 동일 매도·비용·고상관 제한을 사용했습니다.",
                  "- 일봉 위험평가는 전일 종가와 직전 주 마지막 거래일 종가를 기준으로 하므로 라이브 장중 스냅샷과 관측 빈도가 다릅니다.",
                  "- 현금 방어효과는 전일 현금비중×당일 KOSPI 수익률의 국면별 단순합입니다. 실행 가능한 수익률이 아니라 기회비용/방어 진단값입니다.",
                  "- 모든 variant 로그는 `risk_release_variant_daily_states.csv`와 `risk_release_variant_events.csv`에 저장됩니다.",
                  "- 운영 config·라이브 DB·가상계좌·실주문 API는 수정하지 않았습니다.", "",
                  "## 추가 확인 질문", "",
                  "- 장중 손익 기준으로 당일·주간 트리거를 재현하면 중단 진입 시점이 얼마나 달라지는가?",
                  "- 반등형 해제 후 같은 에피소드 재잠금 기준을 별도로 두면 하락장 손실을 줄이면서 회복력을 유지할 수 있는가?",
                  "- 최고수위를 월별·분기별로 감쇠시키는 방식이 시간 쿨다운보다 안정적인가?", "",
                  "## 재현 파라미터", "", "```json", json.dumps(parameters, ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)


def run(data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR, config_path: Path = CONFIG_PATH,
        output_dir: Path = OUTPUT_DIR, report_path: Path = REPORT_PATH) -> dict:
    load_env()
    policies = load_risk_release_variants(config_path)
    initial_cash = float(os.environ.get("BACKTEST_INITIAL_CASH", "100000000"))
    allocation_pct = float(os.environ.get("BACKTEST_TRADE_ALLOCATION_PCT", "10"))
    seed = int(os.environ.get("BENCHMARK_RANDOM_SEED", "20260828"))
    weights = active_weights()
    engine = BacktestEngine(data_dir, report_dir, score_weights=weights)
    candidate_builder = PortfolioSimulator(engine, initial_cash, allocation_pct)
    candidates = candidate_builder.build_candidate_cache()
    kospi = candidate_builder.buy_and_hold("kospi_buy_hold", [BENCHMARK])
    market_returns = daily_return_map(kospi)
    scopes = ("all", "bull", "bear", "sideways")
    results: dict[str, PortfolioResult] = {}
    metric_rows, state_rows, event_rows = [], [], []
    for name, policy in policies.items():
        simulator = PortfolioSimulator(engine, initial_cash, allocation_pct, risk_release_policy=policy)
        result = simulator.run("stock_alarm", candidates, seed)
        results[name] = result
        for row in result.daily_state:
            state_rows.append({"variant": name, **row})
            if row.get("risk_transition") or row.get("risk_episode_transition"):
                event_rows.append({
                    "variant": name, "date": row["date"], "status": row.get("risk_status"),
                    "transition": row.get("risk_transition"), "episode_transition": row.get("risk_episode_transition"),
                    "effective_reason": row.get("risk_reason"), "raw_reason": row.get("risk_raw_reason"),
                    "equity": row.get("equity"), "cash_weight": row.get("cash_weight"),
                    "drawdown_pct": row.get("drawdown_pct"), "exposure_pct": row.get("exposure_pct"),
                    "allocation_scale": row.get("risk_allocation_scale"),
                    "episode_low_equity": row.get("risk_episode_low_equity"),
                    "episode_trading_days": row.get("risk_episode_trading_days"),
                })
        for scope in scopes:
            risk = risk_statistics(_scope_states(result.daily_state, engine.regimes, scope))
            metric_rows.append({
                "variant": name, "regime": scope,
                **equity_metrics(result.daily_equity, result.trades, engine.regimes, scope),
                "cash_effect_pp": round(_scope_cash_effect(result.daily_state, market_returns, engine.regimes, scope), 4),
                **risk,
            })

    lookup = {(row["variant"], row["regime"]): row for row in metric_rows}
    baseline_all = lookup[("variant_A", "all")]
    baseline_bear = lookup[("variant_A", "bear")]
    baseline_protection = max(0.0, -float(baseline_bear["cash_effect_pp"]))
    summary = []
    for name in policies:
        overall, bull, bear = lookup[(name, "all")], lookup[(name, "bull")], lookup[(name, "bear")]
        protection = max(0.0, -float(bear["cash_effect_pp"]))
        escape = not overall["ended_halted"] and int(overall["max_halt_streak"]) < int(baseline_all["max_halt_streak"])
        defense = float(bear["mdd_pct"]) >= float(baseline_bear["mdd_pct"]) - 2 and (
            baseline_protection == 0 or protection >= baseline_protection * 0.8
        )
        summary.append({
            "variant": name, "label": policies[name].get("label", ""),
            "total_return_pct": overall["total_return_pct"], "mdd_pct": overall["mdd_pct"], "sharpe": overall["sharpe"],
            "halt_days": overall["halt_days"], "reduced_days": overall["reduced_days"],
            "max_halt_streak": overall["max_halt_streak"], "halt_entries": overall["halt_entries"],
            "halt_releases": overall["halt_releases"], "ended_halted": overall["ended_halted"],
            "escape_secured": escape, "bear_return_pct": bear["total_return_pct"], "bear_mdd_pct": bear["mdd_pct"],
            "bear_cash_effect_pp": bear["cash_effect_pp"], "bear_cash_protection_pp": round(protection, 4),
            "bull_return_pct": bull["total_return_pct"], "bear_defense_retained": defense,
        })

    baseline_returns = daily_return_map(results["variant_A"])
    tests = []
    for name, result in results.items():
        if name == "variant_A":
            continue
        variant_returns = daily_return_map(result)
        for scope in scopes:
            days = [day for day in baseline_returns if day in variant_returns and (scope == "all" or engine.regimes.get(day) == scope)]
            differences = [(variant_returns[day] - baseline_returns[day]) * 100 for day in days]
            lag = int(policies[name].get("hac_lag", 5))
            two = newey_west_mean_test(differences, lag, "two-sided")
            one = newey_west_mean_test(differences, lag, "greater")
            tests.append({
                "variant": name, "regime": scope, "days": len(days),
                "mean_daily_difference_pct": round(float(two["mean"] or 0), 6), "hac_lag": two["lag"],
                "hac_p_two_sided": round(float(two["p_value"]), 6),
                "hac_p_one_sided": round(float(one["p_value"]), 6),
                "fdr_q_one_sided": None, "significant_superiority": False,
            })
    adjusted = benjamini_hochberg([row["hac_p_one_sided"] for row in tests])
    alpha = float(next(iter(policies.values())).get("fdr_alpha", 0.05))
    for row, q_value in zip(tests, adjusted):
        row["fdr_q_one_sided"] = round(float(q_value), 6) if q_value is not None else None
        row["significant_superiority"] = bool(row["mean_daily_difference_pct"] > 0 and q_value is not None and q_value < alpha)

    trigger_rows = trigger_diagnostics(results["variant_A"].daily_state)
    lock = drawdown_lock_evidence(results["variant_A"])
    tradeoff = [{key: row[key] for key in (
        "variant", "bear_mdd_pct", "bull_return_pct", "bear_cash_protection_pp", "max_halt_streak", "total_return_pct"
    )} for row in summary]
    policy_rows = [{"name": name, **policy} for name, policy in policies.items()]
    parameters = {
        "created_at": datetime.now().isoformat(timespec="seconds"), "actual_start_date": candidate_builder.days[0],
        "actual_end_date": candidate_builder.days[-1], "trading_days": len(candidate_builder.days),
        "initial_cash": initial_cash, "normal_allocation_pct": allocation_pct, "seed": seed,
        "score_weights": weights, "config_path": str(config_path), "variant_count": len(policies),
        "hac_lag": int(next(iter(policies.values())).get("hac_lag", 5)), "fdr_alpha": alpha,
        "bear_defense_rule": "bear MDD no worse than baseline by >2pp and >=80% baseline cash protection",
        "requested_historical_bear_cash_reference_pp": -30.79,
        "actual_variant_A_bear_cash_effect_pp": baseline_bear["cash_effect_pp"],
        "visual_delivery": "exact Markdown table plus risk_release_tradeoff_scatter.csv; no decorative chart",
        "live_configuration_changed": False, "live_database_modified": False, "virtual_account_modified": False,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "risk_release_variant_summary.csv", summary)
    _write_csv(output_dir / "risk_release_variant_metrics.csv", metric_rows)
    _write_csv(output_dir / "risk_release_variant_daily_states.csv", state_rows)
    _write_csv(output_dir / "risk_release_variant_events.csv", event_rows)
    _write_csv(output_dir / "risk_release_trigger_diagnostics.csv", trigger_rows)
    _write_csv(output_dir / "risk_release_tradeoff_scatter.csv", tradeoff)
    _write_csv(output_dir / "risk_release_hac_fdr.csv", tests)
    (output_dir / "drawdown_lock_evidence.json").write_text(json.dumps(lock, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "parameters.json").write_text(json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(parameters, policy_rows, summary, metric_rows, tests, trigger_rows, lock, tradeoff), encoding="utf-8")
    result = {
        "report": str(report_path), "variants": len(policies), "baseline_lock_days": lock["consecutive_days"],
        "eligible_variants": [row["variant"] for row in summary if row["escape_secured"] and row["bear_defense_retained"]],
        "live_state_modified": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest alternative drawdown release policies")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    run(config_path=args.config)


if __name__ == "__main__":
    main()
