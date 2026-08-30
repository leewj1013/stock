from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from statistics import NormalDist

from .app import env_date, load_env
from .backtest_data import collect
from .factor_analysis import FACTOR_LABELS, TECHNICAL_FACTORS, run as run_factor_analysis
from .robustness_analysis import factor_hac_results


EXPANDED_ROOT = Path("data/backtest_expanded")
EXPANDED_DATA_DIR = EXPANDED_ROOT / "ohlcv"
EXPANDED_WATCHLIST = EXPANDED_ROOT / "experimental_watchlist.csv"
EXPANDED_UNIVERSE_META = EXPANDED_ROOT / "experimental_universe_meta.json"
EXPANDED_REPORT_DIR = Path("reports/backtest/expanded")
BASELINE_REPORT_DIR = Path("reports/backtest")


def _clean_text(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).replace("\xa0", " ").strip()


def parse_market_cap_page(content: str, market: str) -> list[dict]:
    """Parse Naver's public market-cap table and exclude zero-face-value funds.

    The page mixes equities with ETFs/ETNs. Their face-value column is zero on
    this table, so the experimental equity universe excludes those rows before
    ranking. Preferred shares remain because they are exchange-listed equities.
    """
    rows = []
    for table_row in re.findall(r"<tr[^>]*>(.*?)</tr>", content, flags=re.I | re.S):
        code_match = re.search(r"(?:code=|/item/main\.naver\?code=)(\d{6})", table_row)
        if not code_match:
            continue
        cells = [_clean_text(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", table_row, flags=re.I | re.S)]
        if len(cells) < 7:
            continue
        try:
            rank = int(cells[0].replace(",", ""))
            face_value = int(cells[5].replace(",", ""))
            market_cap = int(cells[6].replace(",", ""))
        except ValueError:
            continue
        if face_value == 0:
            continue
        name_match = re.search(r"class=[\"']tltle[\"'][^>]*>(.*?)</a>", table_row, flags=re.I | re.S)
        name = _clean_text(name_match.group(1)) if name_match else cells[1]
        rows.append({
            "ticker": code_match.group(1), "name": name, "market": market,
            "market_rank": rank, "market_cap_100m_krw": market_cap, "face_value": face_value,
        })
    return rows


def fetch_market_cap_page(market: str, page: int) -> list[dict]:
    sosok = "0" if market == "KOSPI" else "1"
    url = "https://finance.naver.com/sise/sise_market_sum.naver?" + urllib.parse.urlencode({"sosok": sosok, "page": page})
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 stockAlarm research"})
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read()
    for encoding in ("euc-kr", "cp949", "utf-8"):
        try:
            return parse_market_cap_page(body.decode(encoding), market)
        except UnicodeDecodeError:
            continue
    return []


def select_experimental_universe(target_count: int = 300, pages_per_market: int = 8) -> tuple[list[dict], dict]:
    candidates = []
    for market in ("KOSPI", "KOSDAQ"):
        for page in range(1, pages_per_market + 1):
            candidates.extend(fetch_market_cap_page(market, page))
    unique = {row["ticker"]: row for row in candidates}
    ranked = sorted(unique.values(), key=lambda row: (-int(row["market_cap_100m_krw"]), row["ticker"]))
    selected = ranked[:target_count]
    for rank, row in enumerate(selected, 1):
        row["combined_rank"] = rank
    metadata = {
        "requested_count": target_count, "candidate_equity_count": len(unique), "selected_count": len(selected),
        "market_counts": {market: sum(row["market"] == market for row in selected) for market in ("KOSPI", "KOSDAQ")},
        "selection_method": "current Naver KOSPI/KOSDAQ market-cap ranking; zero-face-value ETF/ETN rows excluded",
        "pages_per_market": pages_per_market,
    }
    return selected, metadata


def write_experimental_watchlist(rows: list[dict], path: Path = EXPANDED_WATCHLIST) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["combined_rank", "ticker", "name", "market", "market_rank", "market_cap_100m_krw", "face_value"])
        writer.writeheader()
        writer.writerows(rows)


def read_experimental_watchlist(path: Path = EXPANDED_WATCHLIST) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def required_periods_for_power(effect_size: float, alpha: float = 0.05, power: float = 0.8, test_count: int = 1) -> int | None:
    """Approximate required independent periods for a two-sided mean test.

    ``test_count`` applies a conservative Bonferroni alpha/test_count proxy.
    FDR power depends on the full unknown p-value distribution, so this bound is
    documented as planning guidance rather than an exact BH-FDR guarantee.
    """
    effect_size = abs(float(effect_size))
    if effect_size <= 0 or not 0 < alpha < 1 or not 0 < power < 1 or test_count < 1:
        return None
    normal = NormalDist()
    critical = normal.inv_cdf(1 - alpha / test_count / 2)
    target = normal.inv_cdf(power)
    return math.ceil(((critical + target) / effect_size) ** 2)


def estimated_power(effect_size: float, sample_count: int, alpha: float = 0.05, test_count: int = 1) -> float:
    if sample_count <= 0 or effect_size <= 0:
        return 0.0
    normal = NormalDist()
    critical = normal.inv_cdf(1 - alpha / test_count / 2)
    shifted = abs(effect_size) * math.sqrt(sample_count)
    return max(0.0, min(1.0, 1 - normal.cdf(critical - shifted) + normal.cdf(-critical - shifted)))


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        if not rows:
            return
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def summarize_period_quality(data_dir: Path, quality_rows: list[dict], end_exclusive: str) -> dict:
    """Count accepted and rejected rows before a cutoff using collected artifacts.

    Accepted CSV rows have already passed ``validate_backtest_rows``; rejected
    rows come from that same validator's data-quality log.  Keeping this as an
    artifact-only summary avoids touching either the live database or watchlist.
    """
    accepted = 0
    for path in data_dir.glob("*.csv"):
        with path.open(newline="", encoding="utf-8-sig") as file:
            accepted += sum(1 for row in csv.DictReader(file) if (row.get("date") or "") < end_exclusive)
    rejected = sum(1 for row in quality_rows if (row.get("date") or "") < end_exclusive)
    total = accepted + rejected
    return {
        "accepted": accepted,
        "rejected": rejected,
        "rejection_rate_pct": round(rejected / total * 100, 4) if total else 0.0,
    }


def compare_factor_results(baseline_rows: list[dict], expanded_rows: list[dict]) -> list[dict]:
    baseline = {(row["scope"], row["factor"], row["target"]): row for row in baseline_rows}
    output = []
    for after in expanded_rows:
        if after["target"] not in ("excess_3d_pct", "excess_5d_pct"):
            continue
        before = baseline.get((after["scope"], after["factor"], after["target"]), {})
        output.append({
            "scope": after["scope"], "factor": after["factor"], "target": after["target"],
            "before_ic_days": before.get("ic_period_count"), "after_ic_days": after.get("ic_period_count"),
            "before_mean_ic": before.get("mean_daily_ic"), "after_mean_ic": after.get("mean_daily_ic"),
            "before_hac_p": before.get("newey_west_p_value"), "after_hac_p": after.get("newey_west_p_value"),
            "before_fdr_q": before.get("fdr_p_value_primary_80"), "after_fdr_q": after.get("fdr_p_value_primary_80"),
            "after_fdr_significant": after.get("fdr_significant_primary_80", False),
        })
    return output


def _fmt(value) -> str:
    if value in (None, ""):
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, bool):
        return "Y" if value else "N"
    return str(value)


def _table(rows: list[dict], columns: list[tuple[str, str]]) -> list[str]:
    return [
        "| " + " | ".join(label for _key, label in columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
        *["| " + " | ".join(_fmt(row.get(key)) for key, _label in columns) + " |" for row in rows],
    ]


def build_report(universe_meta: dict, manifest: dict, baseline_manifest: dict, comparisons: list[dict],
                 power_rows: list[dict], expanded_hac: list[dict], quality_summary: list[dict], parameters: dict) -> str:
    hits = [row for row in expanded_hac if row["fdr_significant_primary_80"]]
    key = next(row for row in comparisons if row["scope"] == "all" and row["factor"] == "volume_score" and row["target"] == "excess_5d_pct")
    key_power = next(row for row in power_rows if row["test_family"] == "primary_80")
    adequately_powered = float(key_power["expanded_estimated_power"]) >= float(parameters["target_power"])
    if hits:
        conclusion = f"확장 후 BH-FDR 유의 신호가 {len(hits)}개 발견됐다. 국면·요인별 재현성을 추가 확인해야 한다."
    elif adequately_powered:
        conclusion = "확장 후에도 FDR 유의 신호가 없고 거래량 5일 효과에 대한 계획 검정력은 목표를 충족했다. 표본 부족만이 아니라 실제 신호가 약할 가능성이 커졌다."
    else:
        conclusion = "확장 후에도 FDR 유의 신호가 없지만 목표 검정력에 미달해 표본 부족 가능성을 아직 배제할 수 없다."
    lines = [
        "# stockAlarm 확장 표본 요인 재검증", "", "## 기술 요약", "", f"**{conclusion}**", "",
        f"운영 watchlist 102개를 수정하지 않고 현재 시가총액 기준 실험 종목군 {universe_meta['selected_count']}개를 별도 구성했습니다. 데이터는 `{manifest.get('actual_start_date')}`부터 `{manifest.get('actual_end_date')}`까지 확보했고 기존 가격 품질검사·매수 필수조건·요인·HAC·FDR 코드를 그대로 적용했습니다.",
        f"거래량 5일 전체 초과수익률은 IC 일수가 {_fmt(key['before_ic_days'])}일에서 {_fmt(key['after_ic_days'])}일로 늘었고, HAC p는 {_fmt(key['before_hac_p'])}→{_fmt(key['after_hac_p'])}, FDR q는 {_fmt(key['before_fdr_q'])}→{_fmt(key['after_fdr_q'])}입니다.", "",
        "## 확장 범위는 현재 시총 상위 종목과 2018년 이후 기간이다", "",
        f"- 요청/선정 종목: {universe_meta['requested_count']}개 / {universe_meta['selected_count']}개",
        f"- 시장 구성: KOSPI {universe_meta['market_counts']['KOSPI']}개, KOSDAQ {universe_meta['market_counts']['KOSDAQ']}개",
        f"- 후보 풀: 액면가 0인 ETF·ETN 제외 후 {universe_meta['candidate_equity_count']}개",
        f"- 선택 기준: {universe_meta['selection_method']}",
        f"- 기간: 기존 `{baseline_manifest.get('actual_start_date', baseline_manifest.get('start_date'))}`~`{baseline_manifest.get('actual_end_date', baseline_manifest.get('end_date'))}` → 확장 `{manifest.get('actual_start_date')}`~`{manifest.get('actual_end_date')}`", "",
        "300개는 기존 102개의 약 3배로 횡단면 5종목 최소조건을 통과하는 날짜를 늘리면서, 상위 500개보다 소형·저유동 종목 편입과 수집 실패 위험을 낮추는 절충안입니다. 2018년 시작은 2020년 급락장을 포함해 2022년 단일 하락장 의존을 줄입니다.", "",
        "## 사전 power analysis는 FDR 가족까지 보수적으로 계산했다", "",
        "기존 거래량 5일 평균 IC를 HAC 장기표준편차로 표준화한 효과크기를 사용했습니다. BH-FDR의 정확한 power는 다른 p-value 분포에 따라 달라지므로 80개 검정 Bonferroni 기준을 보수적 계획치로 병기합니다.", "",
    ]
    lines.extend(_table(power_rows, [
        ("test_family", "검정 가족"), ("effect_size", "표준화 효과"), ("alpha_per_test", "계획 α"),
        ("required_ic_days", "80% 필요 IC일"), ("expanded_ic_days", "확장 IC일"),
        ("expanded_estimated_power", "추정 power"), ("target_met", "80% 충족"),
    ]))
    lines.extend(["", "## 확장 전후 3·5일 IC·HAC·FDR 비교", "",
                  "표는 네 기술요인의 전체·상승·하락·횡보 결과를 모두 포함합니다. 동일한 80개 초과수익률 검정 가족 안에서 q-value를 비교했습니다.", ""])
    display = []
    for row in comparisons:
        display.append({**row, "factor_label": FACTOR_LABELS[row["factor"]]})
    lines.extend(_table(display, [
        ("scope", "국면"), ("factor_label", "요인"), ("target", "목표"),
        ("before_ic_days", "기존 IC일"), ("after_ic_days", "확장 IC일"),
        ("before_mean_ic", "기존 IC"), ("after_mean_ic", "확장 IC"),
        ("before_hac_p", "기존 HAC p"), ("after_hac_p", "확장 HAC p"),
        ("before_fdr_q", "기존 FDR q"), ("after_fdr_q", "확장 FDR q"),
        ("after_fdr_significant", "확장 유의"),
    ]))
    pre_2022 = parameters["pre_2022_quality"]
    lines.extend(["", "## 하락장 표본과 데이터 품질", "",
                  f"하락장 거래일은 기존 {baseline_manifest.get('regime_counts', {}).get('bear', 'N/A')}일에서 확장 {manifest.get('regime_counts', {}).get('bear', 'N/A')}일로 바뀌었습니다. IC일수는 종목 수뿐 아니라 매수 필수조건을 같은 날 최소 5종목이 통과해야 하므로 별도로 확인해야 합니다.", ""])
    lines.extend(_table(quality_summary, [
        ("reason", "품질 결과"), ("count", "건수"), ("rate_pct", "유효행 대비%"), ("impact", "분석 영향"),
    ]))
    lines.extend(["",
                  f"2022년 이전(2018~2021) 구간도 동일 검사로 검증했습니다. 통과 {pre_2022['accepted']:,}행, 제외 {pre_2022['rejected']:,}행이며 제외율은 {pre_2022['rejection_rate_pct']:.4f}%입니다."])
    lines.extend(["", "## 방법과 안전장치", "",
                  "- 가격 행마다 라이브와 같은 결측·OHLC·음수 거래량 검사를 적용했습니다.",
                  "- 요인값은 라이브 평가 함수에 해당 날짜까지의 OHLCV만 넘겨 계산했습니다.",
                  "- 뉴스·공시·재무는 point-in-time 데이터가 없어 기존과 동일하게 검증 불가로 유지했습니다.",
                  "- 실험 목록과 OHLCV는 `data/backtest_expanded`에만 저장하며 운영 `data/watchlist.csv`는 수정하지 않았습니다.",
                  "- 리포트 외 라이브 DB·가상계좌·운영 가중치에 접근하거나 기록하지 않았습니다.", "",
                  "## 한계와 검증 평가", "",
                  "**공유 판단: Share with caveats.** 표본 확대와 계산은 재현 가능하지만 현재 시점 시총 상위 종목을 과거에 적용하는 생존편향이 더 커질 수 있습니다.", "",
                  "- 현재 구성종목을 2018년 이후 전체에 적용해 상장폐지·과거 탈락 종목이 빠집니다.",
                  "- 현재 시총 기준 선택은 미래의 시총 정보를 과거 표본 선택에 사용하므로 결과를 실제 운용 성과로 해석할 수 없습니다. 이번 분석은 검정력 진단용입니다.",
                  "- 종목 수 증가는 횡단면 관측을 늘리지만 독립 거래일 수를 직접 늘리지는 않습니다. 기간 확장이 시간축 검정력에 더 중요합니다.",
                  "- 보수적 Bonferroni power는 BH-FDR의 정확한 검정력을 과소평가할 수 있습니다.", "",
                  "## 종합 결론과 제안", "", f"**{conclusion}**", "",
                  "- 실제 운영 watchlist와 가중치는 자동 변경하지 않습니다.",
                  "- 새 유의 신호가 있으면 현재 시총 선택과 겹치지 않는 과거 구성종목 또는 완전 미사용 기간에서 재검증합니다.",
                  "- 유의 신호가 없고 power가 충분하면 동일 요인 재가중치 반복보다 새로운 독립 요인 설계를 우선 검토합니다.", "",
                  "## 추가 질문", "",
                  "- 당시 KOSPI/KOSDAQ 구성종목과 당시 시총을 복원하면 결론이 유지되는가?",
                  "- 거래일 블록 부트스트랩을 적용해도 확장 결과가 유지되는가?",
                  "- point-in-time 뉴스·공시·재무가 추가되면 7요인 전체 FDR 결과가 달라지는가?", ""])
    return "\n".join(lines)


def run(reuse_data: bool = False, refresh_universe: bool = False) -> dict:
    load_env()
    target_count = int(os.environ.get("EXPANDED_UNIVERSE_SIZE", "300"))
    pages = int(os.environ.get("EXPANDED_MARKET_CAP_PAGES", "8"))
    start_day = env_date("EXPANDED_BACKTEST_START_DATE", date(2018, 1, 1))
    end_day = env_date("EXPANDED_BACKTEST_END_DATE", date.today())
    workers = int(os.environ.get("EXPANDED_COLLECTION_WORKERS", "6"))
    existing = read_experimental_watchlist()
    if existing and not refresh_universe:
        universe = existing[:target_count]
        if EXPANDED_UNIVERSE_META.exists():
            universe_meta = json.loads(EXPANDED_UNIVERSE_META.read_text(encoding="utf-8"))
            universe_meta.update({"requested_count": target_count, "selected_count": len(universe)})
        else:
            universe_meta = {
                "requested_count": target_count, "candidate_equity_count": len(existing), "selected_count": len(universe),
                "market_counts": {market: sum(row["market"] == market for row in universe) for market in ("KOSPI", "KOSDAQ")},
                "selection_method": "reused experimental current-market-cap snapshot; zero-face-value ETF/ETN rows excluded",
                "pages_per_market": pages,
            }
    else:
        universe, universe_meta = select_experimental_universe(target_count, pages)
        if len(universe) < target_count:
            raise RuntimeError(f"Expanded universe is too small: {len(universe)}/{target_count}")
        write_experimental_watchlist(universe)
        EXPANDED_UNIVERSE_META.write_text(json.dumps(universe_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    names = {row["ticker"]: row["name"] for row in universe}
    if not reuse_data:
        manifest = collect(start_day, end_day, EXPANDED_DATA_DIR, EXPANDED_REPORT_DIR, names, workers)
    else:
        manifest_path = EXPANDED_REPORT_DIR / "data_manifest.json"
        if not manifest_path.exists():
            raise RuntimeError("Expanded data manifest missing; run without --reuse-data first")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Names already come from the frozen experimental universe. Avoid any
    # additional current-name lookup while replaying historical observations.
    previous_name_lookup = os.environ.get("KOREAN_STOCK_NAMES")
    os.environ["KOREAN_STOCK_NAMES"] = "0"
    try:
        run_factor_analysis(EXPANDED_DATA_DIR, EXPANDED_REPORT_DIR, names)
    finally:
        if previous_name_lookup is None:
            os.environ.pop("KOREAN_STOCK_NAMES", None)
        else:
            os.environ["KOREAN_STOCK_NAMES"] = previous_name_lookup
    alpha = float(os.environ.get("STAT_FDR_ALPHA", "0.05"))
    min_cross = int(os.environ.get("FACTOR_MIN_CROSS_SECTION", "5"))
    lag_policy = os.environ.get("STAT_HAC_LAG_POLICY", "holding_period_minus_one")
    max_lag = int(os.environ.get("STAT_HAC_MAX_LAG", "19"))
    fixed_lag = int(os.environ.get("STAT_HAC_FIXED_LAG", "4"))
    expanded_hac = factor_hac_results(EXPANDED_REPORT_DIR, alpha, min_cross, lag_policy, max_lag, fixed_lag)
    _write_csv(EXPANDED_REPORT_DIR / "factor_hac_fdr.csv", expanded_hac)
    baseline_hac = list(csv.DictReader((BASELINE_REPORT_DIR / "factor_hac_fdr.csv").open(newline="", encoding="utf-8-sig")))
    comparisons = compare_factor_results(baseline_hac, expanded_hac)
    _write_csv(EXPANDED_REPORT_DIR / "before_after_factor_comparison.csv", comparisons)
    baseline_manifest = json.loads((BASELINE_REPORT_DIR / "data_manifest.json").read_text(encoding="utf-8"))
    baseline_key = next(row for row in baseline_hac if row["scope"] == "all" and row["factor"] == "volume_score" and row["target"] == "excess_5d_pct")
    expanded_key = next(row for row in expanded_hac if row["scope"] == "all" and row["factor"] == "volume_score" and row["target"] == "excess_5d_pct")
    baseline_n = int(baseline_key["ic_period_count"])
    baseline_mean = abs(float(baseline_key["mean_daily_ic"]))
    baseline_se = float(baseline_key["newey_west_standard_error"])
    effect_size = baseline_mean / (baseline_se * math.sqrt(baseline_n)) if baseline_se > 0 else 0.0
    target_power = float(os.environ.get("EXPANDED_TARGET_POWER", "0.80"))
    expanded_n = int(expanded_key["ic_period_count"])
    power_rows = []
    for label, tests in (("single_test", 1), ("primary_80", 80)):
        needed = required_periods_for_power(effect_size, alpha, target_power, tests)
        power_rows.append({
            "test_family": label, "effect_size": round(effect_size, 6), "alpha_per_test": round(alpha / tests, 8),
            "required_ic_days": needed, "expanded_ic_days": expanded_n,
            "expanded_estimated_power": round(estimated_power(effect_size, expanded_n, alpha, tests), 6),
            "target_met": needed is not None and expanded_n >= needed,
        })
    _write_csv(EXPANDED_REPORT_DIR / "power_analysis.csv", power_rows)
    quality_rows = list(csv.DictReader((EXPANDED_REPORT_DIR / "data_quality.csv").open(newline="", encoding="utf-8-sig")))
    pre_2022_quality = summarize_period_quality(EXPANDED_DATA_DIR, quality_rows, "20220101")
    reason_counts: dict[str, int] = {}
    for row in quality_rows:
        reason = row.get("reason") or "unknown"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    valid_rows = max(1, int(manifest.get("valid_row_count") or 0))
    quality_summary = [
        {"reason": reason, "count": count, "rate_pct": round(count / valid_rows * 100, 4),
         "impact": "해당 행/세션 제외 후 분석"}
        for reason, count in sorted(reason_counts.items())
    ] or [{"reason": "문제 없음", "count": 0, "rate_pct": 0.0, "impact": "없음"}]
    parameters = {
        "created_at": datetime.now().isoformat(timespec="seconds"), "target_power": target_power,
        "universe_size": target_count, "start_date": start_day.isoformat(), "end_date": end_day.isoformat(),
        "workers": workers, "alpha": alpha, "live_watchlist_changed": False, "database_access": "none",
        "pre_2022_quality": pre_2022_quality,
        "visual_omission_reason": "Exact before/after IC, HAC p, and FDR q tables are more auditable than charts.",
    }
    (EXPANDED_REPORT_DIR / "expanded_parameters.json").write_text(
        json.dumps({"parameters": parameters, "universe": universe_meta, "manifest": manifest}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path = BASELINE_REPORT_DIR / "EXPANDED_SAMPLE_REPORT.md"
    report_path.write_text(build_report(universe_meta, manifest, baseline_manifest, comparisons, power_rows, expanded_hac, quality_summary, parameters), encoding="utf-8")
    result = {
        "report": str(report_path), "selected_tickers": len(names), "actual_start_date": manifest.get("actual_start_date"),
        "expanded_ic_days": expanded_n,
        "expanded_primary_fdr_hits": sum(row["fdr_significant_primary_80"] for row in expanded_hac),
        "live_watchlist_changed": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Expanded-universe factor power analysis")
    parser.add_argument("--reuse-data", action="store_true", help="reuse isolated expanded OHLCV and skip network collection")
    parser.add_argument("--refresh-universe", action="store_true", help="refresh the experimental current-market-cap universe")
    arguments = parser.parse_args()
    run(arguments.reuse_data, arguments.refresh_universe)


if __name__ == "__main__":
    main()
