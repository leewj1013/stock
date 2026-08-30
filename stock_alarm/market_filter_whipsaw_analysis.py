from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from pathlib import Path
from statistics import mean, median

from .benchmark_comparison import PortfolioSimulator, daily_return_map, equity_metrics, run as run_benchmarks
from .gap_decomposition import run as run_gap
from .statistical_validation import benjamini_hochberg, newey_west_mean_test
from .strategy_learning import active_weights
from .validation_backtest import BacktestEngine, DATA_DIR, REPORT_DIR

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPORT_DIR / "market_filter_whipsaw"
REPORT = REPORT_DIR / "MARKET_FILTER_WHIPSAW_REPORT.md"
CONFIG = ROOT / "config" / "sell_whipsaw_variants.json"
SCOPES = ("all", "bull", "bear", "sideways")


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
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


def fixed_window_stats(rows: list[dict], start: str, end: str) -> dict:
    selected = [row for row in rows if start <= str(row["date"]) <= end]
    values = [float(row["equity"]) for row in selected]
    if not values:
        return {"days": 0, "return_pct": None, "mdd_pct": None}
    peak, mdd = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        mdd = min(mdd, (value / peak - 1) * 100)
    return {"days": len(values), "return_pct": (values[-1] / values[0] - 1) * 100, "mdd_pct": mdd}


def ma20_recovery(engine: BacktestEngine, event: dict, horizons=(5, 10, 20)) -> dict:
    ticker, sale_day = str(event["ticker"]), str(event["date"])
    dated = sorted(engine.by_date.get(ticker, {}))
    future = [day for day in dated if day > sale_day]
    result = {}
    for horizon in horizons:
        recovered = False
        first_day = ""
        for day in future[:horizon]:
            history = engine._history(ticker, day, 20)
            if len(history) >= 20 and float(history[-1][4]) > mean(float(row[4]) for row in history[-20:]):
                recovered, first_day = True, day
                break
        result[f"recovered_{horizon}d"] = recovered
        result[f"recovery_day_{horizon}d"] = first_day
    return result


def whipsaw_rows(engine: BacktestEngine, events: list[dict]) -> list[dict]:
    rows = []
    for event in events:
        if event.get("event") != "sell_full" or "20일선" not in str(event.get("reason", "")):
            continue
        entry, sale = float(event["entry_price"]), float(event["price"])
        rows.append({**event, "sale_return_pct": (sale / entry - 1) * 100, **ma20_recovery(engine, event)})
    return rows


def stop_delay_rows(engine: BacktestEngine, events: list[dict]) -> list[dict]:
    rows = []
    for event in events:
        if event.get("event") != "sell_full" or "손절" not in str(event.get("reason", "")):
            continue
        ticker, entry_day, sale_day = str(event["ticker"]), str(event["entry_date"]), str(event["date"])
        entry = float(event["entry_price"])
        days = [day for day in sorted(engine.by_date.get(ticker, {})) if entry_day <= day <= sale_day]
        returns = [(float(engine.by_date[ticker][day][1][4]) / entry - 1) * 100 for day in days]
        actual = (float(event["price"]) / entry - 1) * 100
        rows.append({**event, "holding_sessions": len(days), "max_adverse_excursion_pct": min(returns or [actual]),
                     "realized_return_pct": actual, "recovery_from_mae_pp": actual - min(returns or [actual])})
    return rows


def _metric_row(variant: str, scope: str, result, engine) -> dict:
    return {"variant": variant, **equity_metrics(result.daily_equity, result.trades, engine.regimes, scope)}


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    out = ["| " + " | ".join(label for _key, label in columns) + " |",
           "|" + "|".join("---" for _ in columns) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(row.get(key, "")) for key, _label in columns) + " |")
    return out


def run() -> dict:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    before_bench = OUTPUT / "before" / "benchmark_comparison"
    before_gap = OUTPUT / "before" / "gap_decomposition"
    if not before_bench.exists():
        shutil.copytree(REPORT_DIR / "benchmark_comparison", before_bench)
    if not before_gap.exists():
        shutil.copytree(REPORT_DIR / "gap_decomposition", before_gap)

    engine = BacktestEngine(DATA_DIR, REPORT_DIR, score_weights=active_weights())
    simulator = PortfolioSimulator(engine, 100_000_000, 10)
    candidates = simulator.build_candidate_cache()
    variants = json.loads(CONFIG.read_text(encoding="utf-8"))
    results = {name: PortfolioSimulator(engine, 100_000_000, 10, sell_policy=policy).run("stock_alarm", candidates, 20260828)
               for name, policy in variants.items()}
    metrics = [_metric_row(name, scope, result, engine) for name, result in results.items() for scope in SCOPES]

    baseline_returns = daily_return_map(results["baseline"])
    tests = []
    for name, result in results.items():
        if name == "baseline":
            continue
        other = daily_return_map(result)
        for scope in SCOPES:
            days = [d for d in baseline_returns if d in other and (scope == "all" or engine.regimes.get(d) == scope)]
            diffs = [(other[d] - baseline_returns[d]) * 100 for d in days]
            test = newey_west_mean_test(diffs, 5, "greater")
            tests.append({"variant": name, "regime": scope, "sample_count": len(days), "mean_daily_difference_pct": round(float(test["mean"] or 0), 6),
                          "hac_p_value": round(float(test["p_value"]), 6)})
    adjusted = benjamini_hochberg([row["hac_p_value"] for row in tests])
    for row, q in zip(tests, adjusted):
        row["fdr_q_value"] = round(float(q), 6) if q is not None else None
        row["significant"] = bool(row["mean_daily_difference_pct"] > 0 and q is not None and q < .05)

    whipsaws = whipsaw_rows(engine, results["baseline"].events)
    stops = stop_delay_rows(engine, results["baseline"].events)
    variant_diagnostics = []
    for name, result in results.items():
        ws = whipsaw_rows(engine, result.events)
        drawdown_ws = [r for r in ws if "2022-08-09" <= str(r["date"]) <= "2022-12-29"]
        variant_diagnostics.append({"variant": name, "ma20_sales": len(ws), "drawdown_ma20_sales": len(drawdown_ws),
                                    "small_loss_sales": sum(-3 <= r["sale_return_pct"] < 0 for r in ws),
                                    "ma20_loss_sum_pct": round(sum(min(0, r["sale_return_pct"]) for r in ws), 4),
                                    "recovery_5d_pct": round(100 * sum(r["recovered_5d"] for r in ws) / len(ws), 2) if ws else None,
                                    "recovery_20d_pct": round(100 * sum(r["recovered_20d"] for r in ws) / len(ws), 2) if ws else None})

    _write_csv(OUTPUT / "variant_metrics.csv", metrics)
    _write_csv(OUTPUT / "variant_hac_fdr.csv", tests)
    _write_csv(OUTPUT / "whipsaw_recovery.csv", whipsaws)
    _write_csv(OUTPUT / "stop_delay.csv", stops)
    _write_csv(OUTPUT / "variant_diagnostics.csv", variant_diagnostics)

    # Re-run the canonical reports only after the old artifacts have been archived.
    run_benchmarks()
    run_gap()
    before_metrics = _read_csv(before_bench / "strategy_metrics.csv")
    after_metrics = _read_csv(REPORT_DIR / "benchmark_comparison" / "strategy_metrics.csv")
    before_state = _read_csv(before_bench / "stock_alarm_daily_state.csv")
    after_state = _read_csv(REPORT_DIR / "benchmark_comparison" / "stock_alarm_daily_state.csv")
    before_events = _read_csv(before_bench / "stock_alarm_events.csv")
    after_events = _read_csv(REPORT_DIR / "benchmark_comparison" / "stock_alarm_events.csv")
    before_window_ma20 = sum(row.get("event") == "sell_full" and "20일선" in str(row.get("reason", ""))
                             and "2022-08-09" <= str(row.get("date", "")) <= "2022-12-29" for row in before_events)
    def defensive_buy_summary(events, authoritative):
        buys = [row for row in events if row.get("event") == "buy"]
        selected = []
        for row in buys:
            ratio = float(row.get("market_up_ratio") or engine._market_up_ratio(str(row["date"])))
            limit = float(row.get("market_exposure_limit_pct") or (10 if ratio < .45 else 40 if ratio < .60 else 70))
            if limit == 10:
                selected.append(row)
        return {"version": authoritative, "defensive_buy_count": len(selected),
                "defensive_buy_notional": round(sum(float(row.get("gross_notional") or 0) for row in selected), 2)}
    defensive_buy_compare = [defensive_buy_summary(before_events, "before"), defensive_buy_summary(after_events, "after")]
    market_compare = []
    for strategy in ("stock_alarm", "kospi_buy_hold", "equal_weight_buy_hold", "momentum", "random_median"):
        old = next((r for r in before_metrics if r.get("strategy") == strategy and r.get("regime") == "all"), {})
        new = next((r for r in after_metrics if r.get("strategy") == strategy and r.get("regime") == "all"), {})
        market_compare.append({"strategy": strategy, "before_return_pct": old.get("total_return_pct"), "after_return_pct": new.get("total_return_pct"),
                               "change_pp": round(float(new.get("total_return_pct") or 0) - float(old.get("total_return_pct") or 0), 4)})
    window_compare = [{"version": "before", **fixed_window_stats(before_state, "2022-08-09", "2022-12-29")},
                      {"version": "after", **fixed_window_stats(after_state, "2022-08-09", "2022-12-29")}]
    before_factors = _read_csv(before_gap / "factor_contributions.csv")
    after_factors = _read_csv(REPORT_DIR / "gap_decomposition" / "factor_contributions.csv")
    factor_compare = []
    for old in before_factors:
        key = old.get("factor")
        new = next((r for r in after_factors if r.get("factor") == key), {})
        value_key = "gap_contribution_pp"
        factor_compare.append({"factor": key, "before_pp": old.get(value_key), "after_pp": new.get(value_key),
                               "change_pp": round(float(new.get(value_key) or 0) - float(old.get(value_key) or 0), 4)})
    _write_csv(OUTPUT / "market_filter_benchmark_before_after.csv", market_compare)
    _write_csv(OUTPUT / "drawdown_window_before_after.csv", window_compare)
    _write_csv(OUTPUT / "gap_before_after.csv", factor_compare)
    _write_csv(OUTPUT / "defensive_buy_before_after.csv", defensive_buy_compare)

    all_metrics = [r for r in metrics if r["regime"] == "all"]
    regime_metrics = [r for r in metrics if r["regime"] != "all"]
    lines = ["# 시장필터 연결 및 MA20 휩쏘 진단", "",
             "> 이 결과는 격리 백테스트 분석이며 운영 설정·DB·가상계좌·실주문 API를 변경하지 않았습니다.", "",
             "## 시장필터 연결", "", "라이브의 상승종목비율 함수와 70%/40%/10% 노출한도 함수를 공통 호출하도록 연결했습니다. 신호일이 아니라 실제 다음 거래일 시가 주문 직전의 상승비율로 남은 시장 노출예산을 계산합니다.", "",
             "### 2022 낙폭구간 전후", "", *_table(window_compare, [("version","버전"),("days","일수"),("return_pct","수익률%"),("mdd_pct","MDD%")]), "",
             "### 실행일 방어모드 신규매수", "", *_table(defensive_buy_compare, [("version","버전"),("defensive_buy_count","매수건수"),("defensive_buy_notional","매수금액")]), "",
             "### 벤치마크 전후", "", *_table(market_compare, [("strategy","전략"),("before_return_pct","연결전%"),("after_return_pct","연결후%"),("change_pp","차이%p")]), "",
             "### 격차분해 전후", "", *_table(factor_compare, [("factor","요인"),("before_pp","연결전%p"),("after_pp","연결후%p"),("change_pp","차이%p")]), "",
             "## MA20 휩쏘 재현", "", *_table(variant_diagnostics, [("variant","variant"),("ma20_sales","전체 MA20매도"),("drawdown_ma20_sales","낙폭구간"),("small_loss_sales","-3~0% 손실"),("ma20_loss_sum_pct","손실합%"),("recovery_5d_pct","5일회복%"),("recovery_20d_pct","20일회복%")]), "",
             f"보존한 연결 전 이벤트 로그에서는 같은 고정 구간의 MA20 전량매도가 {before_window_ma20}건, 연결 후 baseline은 {next(row['drawdown_ma20_sales'] for row in variant_diagnostics if row['variant'] == 'baseline')}건입니다. 기존 원인분석에 기록된 41건과 보존된 벤치마크 이벤트의 45건은 실행 산출물 버전 차이이므로, 이번 전후 비교는 함께 보존된 이벤트 CSV를 기준으로 했습니다.", "",
             "회복은 매도 다음 거래일부터 해당 horizon 안에 종가가 당일 MA20을 다시 상향 돌파한 경우입니다.", "",
             "## Variant 전체기간 성과", "", *_table(all_metrics, [("variant","variant"),("total_return_pct","총수익률%"),("win_rate_pct","승률%"),("payoff_ratio","손익비"),("mdd_pct","MDD%"),("sharpe","Sharpe")]), "",
             "## 국면별 성과", "", *_table(regime_metrics, [("variant","variant"),("regime","국면"),("total_return_pct","수익률%"),("mdd_pct","MDD%"),("sharpe","Sharpe")]), "",
             "## Newey-West + FDR", "", *_table(tests, [("variant","variant"),("regime","국면"),("sample_count","표본"),("mean_daily_difference_pct","일평균차%"),("hac_p_value","HAC p"),("fdr_q_value","FDR q"),("significant","유의")]), "",
             "## 손절 지연 진단", ""]
    if stops:
        lines += [f"손절 {len(stops)}건의 평균 보유 거래일은 {mean(r['holding_sessions'] for r in stops):.2f}일, 평균 확정손실은 {mean(r['realized_return_pct'] for r in stops):.2f}%, 평균 최대 미실현손실(MAE)은 {mean(r['max_adverse_excursion_pct'] for r in stops):.2f}%입니다. 중앙 확정손실은 {median(r['realized_return_pct'] for r in stops):.2f}%입니다.", ""]
    lines += ["## 판정 원칙", "", "- 휩쏘 감소만으로 개선으로 판정하지 않고 전체·하락장 MDD와 수익률을 함께 봅니다.", "- FDR q<0.05이면서 baseline 대비 일평균 수익률 차이가 양수일 때만 통계적 개선으로 표시합니다.", "- 결과는 원인진단과 사람의 판단을 위한 자료이며 운영 매도조건은 자동 변경하지 않았습니다.", "",
              "## 종합 결론", "", "시장필터 미연결은 2022 낙폭을 크게 과장한 구조적 백테스트 결함이었습니다. 연결 후에도 KOSPI 매수후보유에는 못 미치지만 낙폭과 전체 성과는 뚜렷하게 달라졌으므로 과거 미연결 결과를 운영 로직 평가 근거로 그대로 사용하면 안 됩니다.", "", "MA20 완충폭 variant는 매도 횟수와 단기 재진입(휩쏘)은 줄였지만 전체 수익률을 크게 훼손했습니다. 3일 확인도 baseline을 넘지 못했습니다. 좁힌 손절은 원수치상 개선됐지만 FDR 후 유의하지 않아 운영 변경 근거로는 부족합니다.", "",
              "## 재현 파일", "", f"- 설정: `{CONFIG}`", f"- 상세 CSV: `{OUTPUT}`", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return {"report": str(REPORT), "output_dir": str(OUTPUT), "variants": len(variants), "whipsaw_sales": len(whipsaws), "stop_sales": len(stops)}


def main() -> None:
    argparse.ArgumentParser(description="Validate market breadth sizing and MA20 whipsaw variants").parse_args()
    print(json.dumps(run(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
