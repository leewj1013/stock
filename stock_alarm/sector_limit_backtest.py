from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from statistics import mean

from .backtest_data import DATA_DIR, REPORT_DIR
from .benchmark_comparison import PortfolioSimulator, daily_return_map, equity_metrics
from .sector_reference import DEFAULT_CACHE, load_sector_mapping
from .statistical_validation import benjamini_hochberg, newey_west_mean_test
from .strategy_learning import active_weights
from .validation_backtest import BacktestEngine


CONFIG = Path("config/sector_limit_variants.json")
OUTPUT = REPORT_DIR / "sector_limit"
REPORT = REPORT_DIR / "SECTOR_LIMIT_REPORT.md"


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        if rows:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)


def _table(rows: list[dict], fields: list[tuple[str, str]]) -> list[str]:
    return ["| " + " | ".join(label for _, label in fields) + " |", "|" + "|".join("---" for _ in fields) + "|",
            *["| " + " | ".join(str(row.get(key, "")) for key, _ in fields) + " |" for row in rows]]


def activation(states: list[dict]) -> dict:
    return {"sector_limited_signals": sum(int(row.get("sector_limited_count") or 0) for row in states),
            "sector_rejected_signals": sum(int(row.get("sector_rejected_count") or 0) for row in states),
            "sector_reduced_pct_sum": round(sum(float(row.get("sector_reduced_pct") or 0) for row in states), 4),
            "active_days": sum(int(row.get("sector_limited_count") or 0) > 0 for row in states)}


def run(data_dir: Path = DATA_DIR, report_dir: Path = REPORT_DIR, config_path: Path = CONFIG, cache_path: Path = DEFAULT_CACHE) -> dict:
    settings = json.loads(config_path.read_text(encoding="utf-8"))
    engine = BacktestEngine(data_dir, report_dir, score_weights=active_weights())
    mapping = load_sector_mapping(set(engine.names), cache_path)
    base_builder = PortfolioSimulator(engine, 100_000_000, 10)
    candidates = base_builder.build_candidate_cache()
    results, metrics, activations = {}, [], []
    for name, variant in settings["variants"].items():
        simulator = PortfolioSimulator(
            engine, 100_000_000, 10, max_positions_override=10,
            apply_correlation_limit=True, correlation_group_cap_pct=40,
            apply_sector_limit=bool(variant["enabled"]), sector_group_cap_pct=float(variant["sector_cap_pct"]),
            sector_by_ticker=mapping,
        )
        result = simulator.run("stock_alarm", candidates, int(settings["seed"]))
        results[name] = result
        for scope in ("all", "bull", "bear", "sideways"):
            metrics.append({"variant": name, "sector_cap_pct": variant["sector_cap_pct"], **equity_metrics(result.daily_equity, result.trades, engine.regimes, scope)})
        activations.append({"variant": name, "sector_cap_pct": variant["sector_cap_pct"], **activation(result.daily_state)})
    baseline_name = "baseline_sector_off"
    tests = []
    baseline_returns = daily_return_map(results[baseline_name])
    for name in settings["variants"]:
        if name == baseline_name:
            continue
        proposed = daily_return_map(results[name])
        for scope in ("all", "bull", "bear", "sideways"):
            days = sorted(set(baseline_returns) & set(proposed))
            if scope != "all":
                days = [day for day in days if engine.regimes.get(day) == scope]
            differences = [(proposed[day] - baseline_returns[day]) * 100 for day in days]
            test = newey_west_mean_test(differences, int(settings["hac_lag"]), "greater")
            tests.append({"variant": name, "regime": scope, "paired_days": len(days),
                          "mean_daily_difference_pp": round(mean(differences), 8) if differences else None,
                          "hac_lag": test["lag"], "newey_west_p_value": round(float(test["p_value"]), 8)})
    adjusted = benjamini_hochberg([float(row["newey_west_p_value"]) for row in tests])
    for row, qvalue in zip(tests, adjusted):
        row["fdr_q_value"] = round(float(qvalue), 8) if qvalue is not None else None
        row["fdr_significant_improvement"] = bool(qvalue is not None and qvalue < float(settings["fdr_alpha"]) and (row["mean_daily_difference_pp"] or 0) > 0)
    baseline_metrics = {(row["regime"]): row for row in metrics if row["variant"] == baseline_name}
    for row in metrics:
        base = baseline_metrics[row["regime"]]
        row["return_delta_pp"] = round(float(row["total_return_pct"]) - float(base["total_return_pct"]), 4)
        row["mdd_delta_pp"] = round(float(row["mdd_pct"]) - float(base["mdd_pct"]), 4)
    _write(OUTPUT / "variant_metrics.csv", metrics); _write(OUTPUT / "constraint_activation.csv", activations); _write(OUTPUT / "hac_fdr.csv", tests)
    (OUTPUT / "parameters.json").write_text(json.dumps({"created_at": datetime.now().isoformat(timespec="seconds"), "config": settings,
        "mapping_cache": str(cache_path), "mapped_watchlist": len(set(engine.names) & set(mapping)), "watchlist_count": len(engine.names),
        "live_config_changed": False}, ensure_ascii=False, indent=2), encoding="utf-8")
    significant = [row for row in tests if row["fdr_significant_improvement"]]
    lines = ["# 섹터별 투자 한도 격리 백테스트", "", "## 결론", "",
             (f"BH-FDR 후 baseline 대비 유의한 개선은 {len(significant)}건입니다." if significant else "**BH-FDR 후 baseline 대비 통계적으로 유의한 개선은 확인되지 않았습니다. 운영 한도는 활성화하지 않습니다.**"), "",
             "## 전체·국면별 성과", "", *_table(metrics, [("variant","안"),("regime","국면"),("total_return_pct","수익률%"),("return_delta_pp","차이%p"),("mdd_pct","MDD%"),("mdd_delta_pp","MDD차이%p"),("sharpe","Sharpe"),("win_rate_pct","승률%")]), "",
             "## 제약 발동", "", *_table(activations, [("variant","안"),("sector_cap_pct","섹터상한%"),("sector_limited_signals","축소"),("sector_rejected_signals","생략"),("sector_reduced_pct_sum","감축합%p"),("active_days","발동일")]), "",
             "## Newey-West + BH-FDR", "", *_table(tests, [("variant","안"),("regime","국면"),("paired_days","일수"),("mean_daily_difference_pp","일평균차이pp"),("newey_west_p_value","HAC p"),("fdr_q_value","FDR q"),("fdr_significant_improvement","유의개선")]), "",
             "## 해석 주의", "", "- 현재 시점 네이버 업종 분류를 과거 전체 기간에 고정 적용했습니다. 업종 편입·분류 변경을 복원하지 못하므로 생존편향과 유사한 point-in-time 분류 편향이 있습니다.",
             "- 섹터 한도는 고상관 제한 적용 후 남은 배분에 추가 적용되며 어느 단계도 앞 단계 배분을 늘리지 않습니다. 두 제한 중 더 타이트한 결과가 최종 주문비중입니다.",
             "- 기존 보유종목은 강제매도하지 않고 섹터 여력만 소비합니다. 신규 주문만 축소하거나 생략합니다.",
             "- 결과는 격리 OHLCV만 읽었고 운영 config·라이브 DB·가상계좌·실주문 API를 수정하지 않았습니다.", ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return {"report": str(REPORT), "mapped": len(set(engine.names) & set(mapping)), "significant_tests": len(significant), "live_config_changed": False}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare sector-cap variants in isolated portfolio replay")
    parser.add_argument("--config", type=Path, default=CONFIG); parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    args = parser.parse_args(); print(json.dumps(run(config_path=args.config, cache_path=args.cache), ensure_ascii=False, indent=2))


if __name__ == "__main__": main()
