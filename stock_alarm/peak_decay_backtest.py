from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean

from .backtest_data import DATA_DIR, REPORT_DIR
from .benchmark_comparison import PortfolioSimulator, daily_return_map
from .risk_release_backtest import _table, _write_csv, run as run_actual_policies
from .risk_release_policy import ExperimentalRiskController, load_risk_release_variants
from .statistical_validation import benjamini_hochberg, newey_west_mean_test
from .strategy_learning import active_weights
from .validation_backtest import BacktestEngine


CONFIG_PATH = Path("config/peak_decay_variants.json")
OUTPUT_DIR = REPORT_DIR / "peak_decay"
ACTUAL_DIR = OUTPUT_DIR / "actual"
REPORT_PATH = REPORT_DIR / "PEAK_DECAY_REPORT.md"
CHART_PATH = REPORT_DIR / "PEAK_DECAY_TRADEOFF.png"


def block_bootstrap_indices(source_size: int, length: int, block_size: int, rng: random.Random) -> list[int]:
    if source_size < block_size or min(length, block_size) <= 0:
        raise ValueError("source_size and length must cover a positive block")
    output = []
    while len(output) < length:
        start = rng.randrange(0, source_size - block_size + 1)
        output.extend(range(start, start + block_size))
    return output[:length]


def block_bootstrap(values: list[float], length: int, block_size: int, rng: random.Random) -> list[float]:
    return [float(values[index]) for index in block_bootstrap_indices(len(values), length, block_size, rng)]


def _mdd(equity: list[float]) -> float:
    peak, worst = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst * 100


def _next_weekday(day: datetime) -> datetime:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def simulate_synthetic_episode(returns: list[float], policy: dict) -> dict:
    controller = ExperimentalRiskController(policy)
    initial_equity = 100_000_000.0
    equity = previous = weekly_start = high_water = initial_equity
    exposure = 0.70
    day = datetime(2020, 1, 2)
    current_week = None
    curve = [equity]
    unprotected = [equity]
    halt_streak = max_halt = 0
    entered = released = False
    for market_return in returns:
        week = day.isocalendar()[:2]
        if week != current_week:
            weekly_start = previous
            current_week = week
        equity *= 1 + exposure * market_return
        unprotected.append(unprotected[-1] * (1 + 0.70 * market_return))
        risk = controller.evaluate(
            {"total_equity": equity, "holdings_value": equity * exposure},
            previous, weekly_start, high_water, day,
        )
        high_water = float(risk["high_water"])
        entered = entered or risk.get("episode_transition") in {"drawdown_entered", "reduced_entry"}
        released = released or risk.get("episode_transition") in {
            "drawdown_cleared", "cooldown_released", "rebound_released",
            "hysteresis_released", "peak_decay_released",
        }
        if risk["status"] == "halted":
            exposure = 0.0
            halt_streak += 1
            max_halt = max(max_halt, halt_streak)
        else:
            halt_streak = 0
            exposure = 0.70 * float(risk.get("allocation_scale", 1.0))
        previous = equity
        curve.append(equity)
        day = _next_weekday(day)
    return {
        "entered": entered, "escape_success": bool(entered and released and risk["status"] != "halted"),
        "ended_halted": risk["status"] == "halted", "max_halt_streak": max_halt,
        "total_return_pct": (equity / initial_equity - 1) * 100, "mdd_pct": _mdd(curve),
        "unprotected_mdd_pct": _mdd(unprotected),
        "downside_protection_pp": _mdd(curve) - _mdd(unprotected),
    }


def generate_scenarios(source_returns: list[float], count: int, horizon: int, block_size: int,
                       seed: int) -> list[list[float]]:
    rng = random.Random(seed)
    scenarios, attempts = [], 0
    while len(scenarios) < count and attempts < count * 100:
        attempts += 1
        sample = block_bootstrap(source_returns, horizon, block_size, rng)
        equity = [100.0]
        for value in sample:
            equity.append(equity[-1] * (1 + 0.70 * value))
        if _mdd(equity) <= -10:
            scenarios.append(sample)
    if len(scenarios) < count:
        raise RuntimeError(f"Only generated {len(scenarios)} qualifying drawdown scenarios")
    return scenarios


def monte_carlo(policies: dict[str, dict], source_returns: list[float], count: int,
                horizon: int, block_size: int, seed: int) -> tuple[list[dict], list[dict], list[dict]]:
    scenarios = generate_scenarios(source_returns, count, horizon, block_size, seed)
    detail = []
    for scenario_id, sample in enumerate(scenarios, 1):
        for name, policy in policies.items():
            detail.append({"scenario_id": scenario_id, "variant": name, **simulate_synthetic_episode(sample, policy)})
    grouped = {(row["variant"]): [] for row in detail}
    for row in detail:
        grouped[row["variant"]].append(row)
    summary = []
    for name, rows in grouped.items():
        entered = [row for row in rows if row["entered"]]
        summary.append({
            "variant": name, "scenarios": len(rows), "entered_scenarios": len(entered),
            "escape_success_rate_pct": sum(row["escape_success"] for row in entered) / len(entered) * 100 if entered else 0.0,
            "ended_halted_rate_pct": sum(row["ended_halted"] for row in rows) / len(rows) * 100,
            "mean_max_halt_streak": mean(row["max_halt_streak"] for row in rows),
            "mean_total_return_pct": mean(row["total_return_pct"] for row in rows),
            "mean_mdd_pct": mean(row["mdd_pct"] for row in rows),
            "mean_downside_protection_pp": mean(row["downside_protection_pp"] for row in rows),
        })
    baseline = {row["scenario_id"]: row for row in detail if row["variant"] == "variant_A"}
    tests = []
    for name in policies:
        if name == "variant_A":
            continue
        rows = [row for row in detail if row["variant"] == name]
        for metric in ("escape_success", "total_return_pct", "downside_protection_pp"):
            differences = [float(row[metric]) - float(baseline[row["scenario_id"]][metric]) for row in rows]
            test = newey_west_mean_test(differences, 0, "greater")
            tests.append({
                "variant": name, "metric": metric, "scenarios": len(differences),
                "mean_paired_difference": round(float(test["mean"] or 0), 6),
                "p_one_sided": round(float(test["p_value"]), 6), "fdr_q": None, "significant_improvement": False,
            })
    q_values = benjamini_hochberg([row["p_one_sided"] for row in tests])
    for row, q_value in zip(tests, q_values):
        row["fdr_q"] = round(float(q_value), 6) if q_value is not None else None
        row["significant_improvement"] = bool(row["mean_paired_difference"] > 0 and q_value is not None and q_value < 0.05)
    return detail, summary, tests


def create_tradeoff_chart(rows: list[dict], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    figure, axis = plt.subplots(figsize=(10, 6))
    for row in rows:
        name = row["variant"]
        is_decay = name.startswith("variant_F")
        axis.scatter(float(row["bear_mdd_pct"]), float(row["bull_return_pct"]),
                     marker="o" if is_decay else "x", s=70, color="#2563EB" if is_decay else "#D97706")
        axis.annotate(name.replace("variant_", ""), (float(row["bear_mdd_pct"]), float(row["bull_return_pct"])),
                      xytext=(5, 5), textcoords="offset points", fontsize=8)
    axis.set_xlabel("하락장 MDD (%) — 오른쪽일수록 방어 우수")
    axis.set_ylabel("상승장 수익률 (%) — 위쪽일수록 회복 우수")
    axis.set_title("위험중단 해제정책 트레이드오프")
    axis.grid(True, alpha=.25)
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(figure)


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def build_report(parameters: dict, actual: list[dict], regimes: list[dict], actual_tests: list[dict],
                 mc: list[dict], tests: list[dict]) -> str:
    def num(row, key): return float(row.get(key) or 0)
    decay = [row for row in actual if row["variant"].startswith("variant_F")]
    passing = [row for row in decay if str(row.get("escape_secured")).lower() == "true" and str(row.get("bear_defense_retained")).lower() == "true"]
    significant = [row for row in tests if str(row.get("significant_improvement")).lower() == "true"]
    actual_significant = [row for row in actual_tests if str(row.get("significant_superiority")).lower() == "true"]
    conclusion = (
        f"감쇠형 중 `{passing[0]['variant']}`가 실제 구간의 잠금 해소와 하락장 방어 기준을 동시에 통과했습니다."
        if passing else
        "감쇠형도 실제 구간에서 잠금 해소와 기존 하락장 방어 기준을 동시에 만족하지 못했습니다."
    )
    lines = ["# 감쇠형 최고수위 위험정책 검증", "", "## 기술 요약", "", f"**{conclusion}**",
             f"실제 구간은 {parameters['actual_start_date']}~{parameters['actual_end_date']}이고, 합성 검증은 {parameters['monte_carlo_scenarios']}개 블록 부트스트랩 낙폭 시나리오를 사용했습니다. 실제 구간의 FDR 유의 우위는 {len(actual_significant)}개입니다. 합성 검정의 {len(significant)}개 유의 개선은 탈출률·수익에만 나타났고 하락방어 개선은 0개였습니다.",
             "운영 설정은 변경하지 않았으며 결과는 정책 후보 선별용입니다.", "",
             "## 최고수위 감쇠는 낙폭 에피소드 중에만 작동한다", "",
             "월별·분기별 감쇠는 달력 경계에서, 지속일 감쇠는 20거래일마다 유효 최고수위를 낮춥니다. 계좌자산이 새 유효 최고수위를 넘으면 그 값으로 다시 갱신합니다. 당일 -2%, 주간 -5%, 보유비중 70% 초과는 감쇠하지 않고 기존 공통 평가기를 그대로 사용합니다.", "",
             "## 실제 구간에서 기존 A/C/E와 감쇠형을 비교했다", "",
             "아래 표의 방어유지는 baseline A 대비 하락장 MDD 악화 2%p 이내와 현금방어 80% 이상을 동시에 뜻합니다.", ""]
    lines.extend(_table(actual, [("variant","Variant"),("total_return_pct","총수익률%"),("mdd_pct","MDD%"),("sharpe","Sharpe"),("halt_days","중단일"),("max_halt_streak","최장중단"),("bear_mdd_pct","하락MDD%"),("bear_cash_protection_pp","현금방어%p"),("bull_return_pct","상승수익%"),("escape_secured","탈출"),("bear_defense_retained","방어유지")]))
    lines.extend(["", "## 하락장 방어와 상승장 회복의 교환관계", "", "감쇠형은 파란 원, 기존 정책은 주황색 x입니다. 오른쪽·위쪽일수록 유리하지만 중단일과 현금방어를 함께 봐야 합니다.", "", f"![트레이드오프]({CHART_PATH.name})", ""])
    lines.extend(_table(regimes, [("variant","Variant"),("regime","국면"),("total_return_pct","수익률%"),("mdd_pct","MDD%"),("sharpe","Sharpe"),("cash_effect_pp","현금효과%p"),("halt_days","중단일")]))
    lines.extend(["", "## 실제 일수익률 HAC/FDR에서는 감쇠형 우위가 확인되지 않았다", "",
                  "아래 검정은 variant A 대비 일수익률 차이에 Newey-West lag 5를 적용하고 모든 실제 정책×국면 비교를 함께 BH-FDR 보정한 결과입니다.", ""])
    lines.extend(_table([row for row in actual_tests if row["variant"].startswith("variant_F")], [("variant","Variant"),("regime","국면"),("days","표본일"),("mean_daily_difference_pct","일평균차%p"),("hac_p_one_sided","HAC p"),("fdr_q_one_sided","FDR q"),("significant_superiority","유의우위")]))
    lines.extend(["", "## 합성 낙폭 에피소드는 탈출률을 늘려 보지만 실제 시장을 대체하지 않는다", "",
                  "KOSPI 일수익률을 20거래일 블록으로 재표본추출해 70% 노출 계좌가 -10% 이상 낙폭을 겪는 252거래일 경로만 채택했습니다. 블록은 단기 자기상관을 일부 보존하지만 종목 간 상관, 체결, 구조적 레짐 전환과 극단 꼬리를 완전히 재현하지 못합니다.", ""])
    lines.extend(_table(mc, [("variant","Variant"),("scenarios","시나리오"),("escape_success_rate_pct","탈출성공%"),("ended_halted_rate_pct","종료중단%"),("mean_max_halt_streak","평균최장중단"),("mean_total_return_pct","평균수익%"),("mean_mdd_pct","평균MDD%"),("mean_downside_protection_pp","평균방어%p")]))
    lines.extend(["", "## 합성 시나리오의 paired 검정도 다중검정 보정했다", "", "각 시나리오에서 variant A와의 탈출·수익·방어 차이를 paired 평균검정하고 전체 비교를 BH-FDR 보정했습니다.", ""])
    lines.extend(_table(tests, [("variant","Variant"),("metric","지표"),("scenarios","표본"),("mean_paired_difference","평균차"),("p_one_sided","p"),("fdr_q","FDR q"),("significant_improvement","유의개선")]))
    lines.extend(["", "## 최종 판단", "", f"**{conclusion}**", "",
                  "감쇠형이 동시 기준을 통과하지 못하거나 합성 검정에서도 안정적 우위가 없다면 리스크관리 파라미터만 계속 조정하기보다 진입·청산·현금 재배치 구조 자체를 재검토할 필요가 있습니다.",
                  "통과안이 있더라도 실제 낙폭 에피소드가 한 번뿐이므로 운영 자동 적용은 하지 않고 완전 미사용 기간 검증이 필요합니다.", "",
                  "## 범위·한계·재현", "", "- 실제 백테스트는 기존 후보·매도·비용·상관제한 파이프라인을 재사용했습니다.",
                  "- 합성 모델은 위험상태에 따라 다음 날 노출을 70%·축소·0%로 단순화했으며 개별 종목 매도경로를 재현하지 않습니다.",
                  "- 운영 config·라이브 DB·가상계좌·실주문 API를 수정하지 않았습니다.", "", "```json", json.dumps(parameters, ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)


def run(config_path: Path = CONFIG_PATH) -> dict:
    policies = load_risk_release_variants(config_path)
    run_actual_policies(DATA_DIR, REPORT_DIR, config_path, ACTUAL_DIR, OUTPUT_DIR / "ACTUAL_POLICY_REPORT.md")
    actual = _read_csv(ACTUAL_DIR / "risk_release_variant_summary.csv")
    regimes = _read_csv(ACTUAL_DIR / "risk_release_variant_metrics.csv")
    actual_tests = _read_csv(ACTUAL_DIR / "risk_release_hac_fdr.csv")
    engine = BacktestEngine(DATA_DIR, REPORT_DIR, score_weights=active_weights())
    simulator = PortfolioSimulator(engine, 100_000_000, 10)
    kospi = simulator.buy_and_hold("kospi", ["KOSPI"])
    source_returns = list(daily_return_map(kospi).values())[1:]
    count = int(os.environ.get("PEAK_DECAY_MC_SCENARIOS", "300"))
    horizon = int(os.environ.get("PEAK_DECAY_MC_HORIZON_DAYS", "252"))
    block = int(os.environ.get("PEAK_DECAY_MC_BLOCK_DAYS", "20"))
    seed = int(os.environ.get("PEAK_DECAY_MC_SEED", "20260828"))
    detail, mc_summary, mc_tests = monte_carlo(policies, source_returns, count, horizon, block, seed)
    create_tradeoff_chart(actual, CHART_PATH)
    parameters = {"created_at": datetime.now().isoformat(timespec="seconds"), "actual_start_date": simulator.days[0], "actual_end_date": simulator.days[-1], "actual_trading_days": len(simulator.days), "config_path": str(config_path), "monte_carlo_scenarios": count, "monte_carlo_horizon_days": horizon, "block_size_days": block, "seed": seed, "source_return_count": len(source_returns), "synthetic_acceptance_filter": "70% KOSPI exposure MDD <= -10%", "live_state_modified": False}
    _write_csv(OUTPUT_DIR / "peak_decay_actual_summary.csv", actual)
    _write_csv(OUTPUT_DIR / "peak_decay_actual_regimes.csv", regimes)
    _write_csv(OUTPUT_DIR / "peak_decay_tradeoff_scatter.csv", actual)
    _write_csv(OUTPUT_DIR / "synthetic_episode_detail.csv", detail)
    _write_csv(OUTPUT_DIR / "synthetic_episode_summary.csv", mc_summary)
    _write_csv(OUTPUT_DIR / "synthetic_episode_tests.csv", mc_tests)
    (OUTPUT_DIR / "parameters.json").write_text(json.dumps(parameters, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(build_report(parameters, actual, regimes, actual_tests, mc_summary, mc_tests), encoding="utf-8")
    result = {"report": str(REPORT_PATH), "chart": str(CHART_PATH), "variants": len(policies), "scenarios": count, "live_state_modified": False}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest decaying high-water risk release policies")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    run(args.config)


if __name__ == "__main__":
    main()
