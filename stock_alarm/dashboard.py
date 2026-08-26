from __future__ import annotations

import html
import json
import os
from datetime import datetime
from statistics import mean

from .app import load_env, performance_penalty, write_error_log
from .daily_check import lines as daily_check_lines, run_log_statuses
from .data_store import collection_summary, latest_candidates, latest_virtual_valuation, recent_position_checks, recent_runs, recent_sell_outcomes, rejection_summary
from .health import lines as health_lines
from .positions_check import active_position_count, active_position_tickers
from .report import delivery_status, dedupe_ticker, latest_batch as latest_log_batch, latest_error_summary, reconciled_daily_alert_rows, tail_csv, tail_text


OUT_PATH = "reports/dashboard.html"
PAGE_SIZE = 15
NUMERIC_COLUMNS = {
    "close",
    "score",
    "volume_score",
    "trading_value_score",
    "trend_score",
    "total_score",
    "volume",
    "trading_value",
    "trading_value_억",
    "trend",
    "news",
    "disclosure",
    "penalty",
    "volume_ratio",
    "news_score",
    "disclosure_score",
    "performance_penalty",
    "count",
    "entry_count",
    "return_pct",
    "picks",
    "avg_1d_return_pct",
    "win_rate_1d_pct",
    "entry_close",
    "return_1d_pct",
    "sell_return_pct",
    "return_3d_pct",
    "return_5d_pct",
    "entry_price",
    "runs",
    "candidates",
    "selected",
    "position_checks",
    "sell_decisions",
    "hold_decisions",
    "holding_days",
    "distance_ma20_pct",
    "drawdown_from_peak_pct",
    "rank",
    "final_score",
    "legacy_score",
    "ma20",
    "max_return_pct",
    "allocation_pct",
    "quantity",
    "valuation",
    "profit_loss",
}
RETURN_COLUMNS = {"return_pct", "avg_1d_return_pct", "return_1d_pct", "sell_return_pct", "return_3d_pct", "return_5d_pct", "return_10d_pct", "return_20d_pct", "mfe_20d_pct", "mae_20d_pct"}
BOOLEAN_COLUMNS = {"passed", "selected", "legacy_passed", "time_stop_triggered"}
LABELS = {
    "stockAlarm Dashboard": "국내주식 알림 대시보드",
    "generated": "생성 시각",
    "positions": "보유 종목",
    "today recommendations": "오늘 추천",
    "today sell alerts": "오늘 매도 검토",
    "today issues": "오늘 문제",
    "performance rows": "성과 데이터",
    "completed 1d": "1일 성과 완료",
    "avg 1d return": "1일 평균 수익률",
    "win rate 1d": "1일 승률",
    "suggested min score": "추천 최소점수 제안",
    "latest error": "최근 오류",
    "today runs": "오늘 실행",
    "last telegram": "텔레그램 최근 전송",
    "average position return": "보유 평균 수익률",
    "Issues": "문제",
    "Today run details": "오늘 실행 상세",
    "Today recommendations": "오늘 추천 종목",
    "Today sell alerts": "오늘 매도 검토 종목",
    "Recommendation shape": "추천 형태",
    "Score breakdown": "점수 구성",
    "Why recommended": "추천 사유",
    "Sell alert summary": "매도 검토 요약",
    "Recent sell alerts": "최근 매도 검토",
    "Recommendation stats": "추천 통계",
    "Current settings": "현재 설정",
    "Recent deliveries": "최근 발송",
    "Top recommendation performance": "추천 성과 상위",
    "Worst recommendation performance": "추천 성과 하위",
    "Performance penalties": "성과 감점",
    "Recommendations with sell alerts": "매도 검토 연결 추천",
    "Recent recommendations": "최근 추천",
    "Recommendation performance": "추천 성과",
    "Positions": "보유 종목",
    "Recent task log": "최근 작업 로그",
    "Recent task errors": "최근 작업 오류",
    "Daily check": "일일 점검",
    "source": "출처",
    "item": "항목",
    "status": "상태",
    "step": "단계",
    "created_at": "생성시각",
    "ticker": "종목코드",
    "name": "종목명",
    "close": "종가",
    "score": "점수",
    "reason": "사유",
    "volume_score": "거래량 점수",
    "trading_value_score": "거래대금 점수",
    "trend_score": "추세 점수",
    "type": "유형",
    "when": "조건",
    "action": "동작",
    "total_score": "총점",
    "volume": "거래량",
    "trading_value": "거래대금",
    "trading_value_억": "거래대금(억)",
    "trend": "추세",
    "news": "뉴스",
    "disclosure": "공시",
    "penalty": "감점",
    "volume_ratio": "거래량 배율",
    "news_score": "뉴스 점수",
    "disclosure_score": "공시 점수",
    "performance_penalty": "성과 감점",
    "summary": "요약",
    "count": "건수",
    "entry_count": "추천 횟수",
    "return_pct": "수익률",
    "metric": "지표",
    "value": "값",
    "setting": "설정",
    "channel": "채널",
    "message_id": "메시지 ID",
    "chat_id_suffix": "채팅 식별자 끝자리",
    "recommendations": "종목 추천",
    "sell_check": "매도 점검",
    "positions_report": "보유 종목 보고",
    "market_summary": "시황 알림",
    "close_alert": "마감 알림",
    "dashboard": "대시보드",
    "error": "전송 오류",
    "picks": "추천수",
    "avg_1d_return_pct": "1일 평균 수익률",
    "win_rate_1d_pct": "1일 승률",
    "entry_close": "진입 종가",
    "return_1d_pct": "1일 수익률",
    "sell_return_pct": "매도검토 수익률",
    "sell_reason": "매도검토 사유",
    "pick_date": "추천일",
    "return_3d_pct": "3일 수익률",
    "return_5d_pct": "5일 수익률",
    "entry_price": "진입가",
    "Data collection": "수집 데이터",
    "Collection summary": "수집 현황",
    "Recent strategy runs": "최근 전략 실행",
    "Latest candidate snapshots": "최근 전체 후보 평가",
    "Candidate rejection summary": "후보 탈락 사유 요약",
    "Recent position checks": "최근 전체 보유종목 판단",
    "started_at": "시작시각",
    "finished_at": "완료시각",
    "run_type": "실행유형",
    "market_date": "시장일",
    "strategy_version": "전략버전",
    "schema_version": "스키마버전",
    "git_commit": "소스 기준점",
    "config_hash": "설정 해시",
    "watchlist_hash": "관심종목 해시",
    "evaluated_at": "평가시각",
    "checked_at": "점검시각",
    "passed": "기술조건 통과",
    "selected": "최종선정",
    "rank": "순위",
    "rejection_reasons": "탈락사유",
    "decision": "판단",
    "reasons": "판단사유",
    "holding_days": "보유일수",
    "max_return_pct": "최대수익률",
    "drawdown_from_peak_pct": "고점대비 하락폭",
    "distance_ma20_pct": "20일선 이격률",
    "ma20": "20일선",
    "expected_volume_fraction": "장 진행률",
    "relative_strength_pct": "시장대비 강도",
    "dynamic_stop_loss_pct": "동적 손절선",
    "Sell counterfactual performance": "매도 판단 사후성과",
    "alert_created_at": "매도 판단 시각",
    "execution_date": "가상 매도일",
    "execution_price": "가상 매도가",
    "return_10d_pct": "10일 수익률",
    "raw_volume_ratio": "원시 거래량 배율",
    "raw_trading_value": "원시 거래대금",
    "atr20_pct": "20일 변동성",
    "benchmark_symbol": "시장 기준",
    "market_proxy_return_pct": "시장 수익률",
    "relative_strength_score": "시장대비 점수",
    "legacy_score": "구전략 점수",
    "legacy_passed": "구전략 통과",
    "final_score": "최종 점수",
    "time_stop_triggered": "기간 청산 조건",
    "message_id": "메시지 번호",
    "chat_id_suffix": "채팅 식별자 끝자리",
    "allocation_pct": "추천 비중(%)",
    "quantity": "보유수량",
    "valuation": "평가금액",
    "profit_loss": "평가손익",
}

DISPLAY_VALUES = {
    "ok": "정상",
    "none": "없음",
    "missing": "미실행",
    "error": "오류",
    "delivered": "전송 완료",
    "fallback": "대체 전송",
    "telegram": "텔레그램",
    "console": "콘솔",
    "skipped_duplicate": "중복으로 생략",
    "HOLD": "보유",
    "SELL": "매도 검토",
    "ALREADY_ALERTED": "이미 알림",
    "previous_sell_alert": "이전 매도 알림 있음",
    "intraday": "장중",
    "daily": "마감",
    "True": "예",
    "False": "아니요",
}


def display_label(value: str) -> str:
    return LABELS.get(value, value)


def display_value(value: object) -> str:
    text = str(value or "")
    if text in DISPLAY_VALUES:
        return DISPLAY_VALUES[text]
    if text.endswith("=ok"):
        return f"{display_label(text[:-3])}=정상"
    if text.endswith("=missing"):
        return f"{display_label(text[:-8])}=미실행"
    if text.startswith("telegram ok at "):
        return f"텔레그램 정상 전송: {text.removeprefix('telegram ok at ')}"
    if text.startswith("no recent telegram delivery"):
        return text.replace("no recent telegram delivery", "최근 텔레그램 전송 없음").replace("last=", "최근 기록=").replace(" at ", " / ")
    if text == "no deliveries yet":
        return "전송 기록 없음"
    return text


def e(value: object) -> str:
    return html.escape(str(value or ""))


def metric_cards() -> list[tuple[str, str]]:
    summary = {row.get("metric", ""): row.get("value", "") for row in tail_csv("logs/recommendation_performance_summary.csv", 50)}
    deliveries = tail_csv("logs/deliveries.csv", 50)
    return [
        ("positions", str(active_position_count())),
        ("today recommendations", str(len(today_recommendation_rows()))),
        ("today sell alerts", str(len(today_sell_alert_rows()))),
        ("today issues", str(today_issue_count())),
        ("performance rows", summary.get("rows", "0")),
        ("completed 1d", summary.get("completed_1d", "0")),
        ("avg 1d return", value_or_dash(summary.get("avg_1d_return_pct"), "%")),
        ("win rate 1d", value_or_dash(summary.get("win_rate_1d_pct"), "%")),
        ("suggested min score", summary.get("suggested_min_score", "?")),
        ("latest error", latest_error_summary(deliveries)),
        ("today runs", today_run_summary()),
    ]


def today_run_summary() -> str:
    statuses = run_log_statuses()
    ok = sum(line.endswith("=ok") for line in statuses)
    return f"{ok}/{len(statuses)} ok" if statuses else "0/0 ok"


def today_csv_count(path: str) -> int:
    today = datetime.now().date().isoformat()
    return sum(row.get("created_at", "").startswith(today) for row in tail_csv(path, 1000))


def today_recommendation_rows(limit: int | None = None) -> list[dict[str, str]]:
    today = datetime.now().date().isoformat()
    performance = {row.get("ticker", ""): row for row in tail_csv("logs/recommendation_performance.csv", 1000)}
    rows = []
    daily_rows = reconciled_daily_alert_rows(
        tail_csv("logs/recommendations.csv", 10000), tail_csv("logs/deliveries.csv", 10000), today, "recommendation"
    )
    for row in daily_rows:
        ticker = row.get("ticker", "")
        rows.append({**row, "reason": reason_summary(row, performance.get(ticker, {}), performance_penalty(ticker))})
    return rows[:limit] if limit is not None else rows


def today_sell_alert_rows(limit: int | None = None) -> list[dict[str, str]]:
    today = datetime.now().date().isoformat()
    rows = reconciled_daily_alert_rows(
        tail_csv("logs/sell_alerts.csv", 10000), tail_csv("logs/deliveries.csv", 10000), today, "sell"
    )
    return rows[:limit] if limit else rows


def today_issue_count() -> int:
    rows = [*settings_rows(), *today_run_rows()]
    return sum(status_class(row.get("value", row.get("status", ""))) in {"bad", "warn"} for row in rows)


def today_run_rows() -> list[dict[str, str]]:
    rows = []
    for line in run_log_statuses():
        name, separator, status = line.partition("=")
        if separator:
            rows.append({"step": name, "status": status})
    return rows


def value_or_dash(value: str | None, suffix: str = "") -> str:
    return f"{value}{suffix}" if value else "-"


def performance_summary_rows() -> list[dict[str, str]]:
    summary = {row.get("metric", ""): row.get("value", "") for row in tail_csv("logs/recommendation_performance_summary.csv", 50)}
    labels = [
        ("전체 1일 평균", "avg_1d_return_pct", "%"),
        ("전체 1일 승률", "win_rate_1d_pct", "%"),
        ("90점 이상 평균", "score_90_plus_avg_1d_return_pct", "%"),
        ("70~89점 평균", "score_70_89_avg_1d_return_pct", "%"),
        ("70점 미만 평균", "score_under_70_avg_1d_return_pct", "%"),
        ("추천 점수 조정", "score_adjustment", ""),
        ("추천 최소점수 제안", "suggested_min_score", ""),
    ]
    return [{"metric": label, "value": value_or_dash(summary.get(key), suffix)} for label, key, suffix in labels]


def recommendation_rank_rows(limit: int = 10, worst: bool = False) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], list[float]] = {}
    for row in tail_csv("logs/recommendation_performance.csv", 1000):
        value = row.get("return_1d_pct")
        if not value:
            continue
        key = (row.get("ticker", ""), row.get("name", ""))
        grouped.setdefault(key, []).append(float(value))
    rows = [
        {
            "ticker": ticker,
            "name": name,
            "picks": str(len(values)),
            "avg_1d_return_pct": f"{mean(values):.2f}",
            "win_rate_1d_pct": f"{sum(value > 0 for value in values) / len(values) * 100:.1f}",
        }
        for (ticker, name), values in grouped.items()
    ]
    return sorted(rows, key=lambda row: float(row["avg_1d_return_pct"]), reverse=not worst)[:limit]


def recommendation_performance_rows(limit: int = 20) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], dict[str, list[float] | str]] = {}
    for row in tail_csv("logs/recommendation_performance.csv", 1000):
        ticker = row.get("ticker", "")
        if not ticker:
            continue
        key = (ticker, row.get("name", ""))
        bucket = grouped.setdefault(
            key,
            {"score": [], "entry_close": [], "return_1d_pct": [], "return_3d_pct": [], "return_5d_pct": [], "news_score": [], "disclosure_score": [], "last_pick_date": ""},
        )
        bucket["last_pick_date"] = row.get("pick_date", "")
        for column in ["score", "entry_close", "return_1d_pct", "return_3d_pct", "return_5d_pct", "news_score", "disclosure_score"]:
            if row.get(column):
                bucket[column].append(float(row[column]))  # type: ignore[union-attr]
    rows = []
    for (ticker, name), values in grouped.items():
        rows.append(
            {
                "pick_date": str(values["last_pick_date"]),
                "ticker": ticker,
                "name": name,
                "entry_count": str(len(values["score"])),  # type: ignore[arg-type]
                "score": avg_value(values["score"]),  # type: ignore[arg-type]
                "entry_close": avg_value(values["entry_close"], decimals=0),  # type: ignore[arg-type]
                "return_1d_pct": avg_value(values["return_1d_pct"]),  # type: ignore[arg-type]
                "return_3d_pct": avg_value(values["return_3d_pct"]),  # type: ignore[arg-type]
                "return_5d_pct": avg_value(values["return_5d_pct"]),  # type: ignore[arg-type]
                "news_score": avg_value(values["news_score"]),  # type: ignore[arg-type]
                "disclosure_score": avg_value(values["disclosure_score"]),  # type: ignore[arg-type]
            }
        )
    return sorted(rows, key=lambda row: row["pick_date"], reverse=True)[:limit]


def avg_value(values: list[float], decimals: int = 2) -> str:
    return f"{mean(values):.{decimals}f}" if values else ""


def sell_alerted_recommendation_rows(limit: int = 10) -> list[dict[str, str]]:
    recommendations = {row.get("ticker", ""): row for row in tail_csv("logs/recommendation_performance.csv", 1000)}
    rows = []
    for alert in reversed(tail_csv("logs/sell_alerts.csv", 1000)):
        ticker = alert.get("ticker", "")
        recommendation = recommendations.get(ticker)
        if not recommendation:
            continue
        rows.append(
            {
                "ticker": ticker,
                "name": alert.get("name", recommendation.get("name", "")),
                "score": recommendation.get("score", ""),
                "entry_close": recommendation.get("entry_close", ""),
                "return_1d_pct": recommendation.get("return_1d_pct", ""),
                "sell_return_pct": alert.get("return_pct", ""),
                "sell_reason": alert.get("reason", ""),
            }
        )
        if len(rows) >= limit:
            return rows
    return rows


def sell_alert_summary_rows() -> list[dict[str, str]]:
    counts: dict[str, int] = {}
    for row in tail_csv("logs/sell_alerts.csv", 1000):
        summary = row.get("summary") or row.get("reason", "")
        if summary:
            counts[summary] = counts.get(summary, 0) + 1
    return [{"summary": summary, "count": str(count)} for summary, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)]


def performance_penalty_rows(limit: int = 10) -> list[dict[str, str]]:
    seen = set()
    rows = []
    for row in reversed(tail_csv("logs/recommendation_performance.csv", 1000)):
        ticker = row.get("ticker", "")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        penalty = performance_penalty(ticker)
        if penalty:
            rows.append({"ticker": ticker, "name": row.get("name", ""), "penalty": f"{penalty:.2f}"})
        if len(rows) >= limit:
            return rows
    return rows


def recommendation_shape_rows() -> list[dict[str, str]]:
    return [
        {"type": "관심 후보", "when": "거래량 급증 + 20일선 상회 + 거래대금 충분", "action": "추천 알림 발송"},
        {"type": "확인 필요", "when": "뉴스/공시/과거 성과 보너스 또는 감점 있음", "action": "대시보드 사유 확인"},
        {"type": "매도 검토", "when": "손절, 급락, 수익 반납 조건 발생", "action": "매도 검토 알림 발송"},
    ]


def primary_metric_cards(
    recommendation_rows: list[dict[str, str]] | None = None,
    sell_rows: list[dict[str, str]] | None = None,
    position_rows: list[dict[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """Return only the operating metrics needed for the default dashboard view."""
    deliveries = tail_csv("logs/deliveries.csv", 200)
    recommendation_rows = today_recommendation_rows() if recommendation_rows is None else recommendation_rows
    sell_rows = today_sell_alert_rows() if sell_rows is None else sell_rows
    position_rows = position_summary_rows() if position_rows is None else position_rows
    position_returns = [float(row["return_pct"]) for row in position_rows if row.get("return_pct")]
    average_return = f"{mean(position_returns):.2f}%" if position_returns else "-"
    virtual = latest_virtual_valuation()
    return [
        ("today runs", today_run_summary()),
        ("last telegram", delivery_status(deliveries)),
        ("today recommendations", str(len(recommendation_rows))),
        ("today sell alerts", str(len(sell_rows))),
        ("positions", str(len(position_rows))),
        ("average position return", average_return),
        ("가상 트레이더 수익률", f"{float(virtual.get('return_pct', 0)):.2f}%" if virtual else "-"),
        ("직전 배치 대비", f"{float(virtual.get('return_change_pct', 0)):+.2f}%p" if virtual else "-"),
    ]


def score_breakdown_rows(limit: int = 10) -> list[dict[str, str]]:
    performance = {row.get("ticker", ""): row for row in tail_csv("logs/recommendation_performance.csv", 1000)}
    return [
        {
            "created_at": row.get("created_at", ""),
            "ticker": row.get("ticker", ""),
            "name": row.get("name", ""),
            "total_score": row.get("score", ""),
            "volume": row.get("volume_score", ""),
            "trading_value": row.get("trading_value_score", ""),
            "trend": row.get("trend_score", ""),
            "news": performance.get(row.get("ticker", ""), {}).get("news_score", ""),
            "disclosure": performance.get(row.get("ticker", ""), {}).get("disclosure_score", ""),
            "penalty": f"{performance_penalty(row.get('ticker', '')):.2f}",
        }
        for row in latest_recommendation_rows(limit)
    ][:limit]


def latest_recommendation_rows(limit: int = 10) -> list[dict[str, str]]:
    rows = []
    seen = set()
    for row in reversed(tail_csv("logs/recommendations.csv", 1000)):
        ticker = row.get("ticker", "")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def recommendation_reason_rows(limit: int = 10) -> list[dict[str, str]]:
    performance = {row.get("ticker", ""): row for row in tail_csv("logs/recommendation_performance.csv", 1000)}
    rows = []
    seen = set()
    for recommendation in reversed(tail_csv("logs/recommendations.csv", 1000)):
        ticker = recommendation.get("ticker", "")
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        performance_row = performance.get(ticker, {})
        penalty = performance_penalty(ticker)
        rows.append(
            {
                "created_at": recommendation.get("created_at", ""),
                "ticker": ticker,
                "name": recommendation.get("name", ""),
                "score": recommendation.get("score", ""),
                "reason": reason_summary(recommendation, performance_row, penalty),
                "volume_ratio": recommendation.get("volume_ratio", ""),
                "trading_value_억": trading_value_eok(recommendation.get("trading_value", "")),
                "news_score": performance_row.get("news_score", ""),
                "disclosure_score": performance_row.get("disclosure_score", ""),
                "performance_penalty": f"{penalty:.2f}",
            }
        )
        if len(rows) >= limit:
            return rows
    return rows


def reason_summary(recommendation: dict[str, str], performance: dict[str, str], penalty: float) -> str:
    parts = []
    try:
        if float(recommendation.get("volume_ratio", 0)) >= 2:
            parts.append("거래량 급증")
    except ValueError:
        pass
    if positive(performance.get("news_score", "")):
        parts.append("뉴스 보너스")
    if positive(performance.get("disclosure_score", "")):
        parts.append("공시 보너스")
    if penalty:
        parts.append("성과 감점")
    return " + ".join(parts) or "기본 조건 충족"


def positive(value: str) -> bool:
    try:
        return float(value) > 0
    except ValueError:
        return False


def trading_value_eok(value: str) -> str:
    try:
        return f"{int(value) / 100_000_000:.0f}" if value else ""
    except ValueError:
        return ""


def settings_rows() -> list[dict[str, str]]:
    rows = []
    for line in health_lines():
        key, separator, value = line.partition("=")
        if separator:
            rows.append({"setting": key, "value": value})
    return rows


def issue_rows() -> list[dict[str, str]]:
    rows = []
    for label, value in metric_cards():
        if status_class(value) in {"bad", "warn"}:
            rows.append({"source": "card", "item": label, "status": value})
    for row in [*settings_rows(), *today_run_rows()]:
        value = row.get("value", row.get("status", ""))
        if status_class(value) in {"bad", "warn"}:
            rows.append({"source": row.get("setting") and "setting" or "run", "item": row.get("setting", row.get("step", "")), "status": value})
    return rows or [{"source": "dashboard", "item": "issues", "status": "none"}]


def table(title: str, rows: list[dict[str, str]], columns: list[str]) -> str:
    rows = sort_table_rows(rows, columns)
    body = "".join(
        f"<tr data-row='{index}'>" + "".join(cell(row.get(column, ""), column) for column in columns) + "</tr>"
        for index, row in enumerate(rows)
    )
    head = "".join(header_cell(column) for column in columns)
    pager = f"<div class='pager' data-page-size='{PAGE_SIZE}'><span>총 {len(rows)}건</span></div>" if len(rows) > PAGE_SIZE else ""
    return f"<section><h2>{e(display_label(title))}</h2><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{pager}</section>"


def sort_table_rows(rows: list[dict[str, str]], columns: list[str]) -> list[dict[str, str]]:
    """Show the newest ISO-formatted creation timestamp first when visible."""
    if "created_at" not in columns:
        return list(rows)
    return sorted(rows, key=lambda row: (bool(row.get("created_at")), row.get("created_at", "")), reverse=True)


def details(title: str, content: str) -> str:
    return f"<details><summary>{e(title)}</summary><div class='details-body'>{content}</div></details>"


def stock_highlights() -> str:
    summary = {row.get("metric", ""): row.get("value", "") for row in tail_csv("logs/recommendation_performance_summary.csv", 50)}
    position_rows = latest_batch(tail_csv("logs/positions_report.csv", 100))
    returns = [float(row.get("return_pct") or 0) for row in position_rows]
    avg_return = sum(returns) / len(returns) if returns else None
    cards = [
        ("총평균수익률", value_or_dash(total_average_return(), "%"), signed_class(total_average_return())),
        ("보유종목수익률", f"{avg_return:.2f}%" if avg_return is not None else "-", signed_class(avg_return)),
        ("1일 평균 수익률", value_or_dash(summary.get("avg_1d_return_pct"), "%"), signed_class(summary.get("avg_1d_return_pct"))),
        ("1일 수익 종목 비율", value_or_dash(summary.get("win_rate_1d_pct"), "%"), ""),
    ]
    return "<div class='highlight-grid'>" + "".join(f"<div class='highlight'><b>{e(label)}</b><span class='{klass}'>{e(value)}</span></div>" for label, value, klass in cards) + "</div>"


def collection_highlights() -> str:
    summary = collection_summary()
    cards = [
        ("전략 실행", summary.get("runs", 0)),
        ("후보 평가", summary.get("candidates", 0)),
        ("최종 선정", summary.get("selected", 0)),
        ("보유종목 점검", summary.get("position_checks", 0)),
        ("HOLD 판단", summary.get("hold_decisions", 0)),
        ("SELL 판단", summary.get("sell_decisions", 0)),
    ]
    return "<div class='highlight-grid'>" + "".join(f"<div class='highlight'><b>{e(label)}</b><span>{e(value)}</span></div>" for label, value in cards) + "</div>"


def total_average_return() -> str:
    values = []
    for row in dedupe_ticker(tail_csv("logs/sell_alerts.csv", 1000)):
        if row.get("return_pct"):
            values.append(float(row["return_pct"]))
    for row in latest_batch(tail_csv("logs/positions_report.csv", 1000)):
        if row.get("return_pct"):
            values.append(float(row["return_pct"]))
    return f"{mean(values):.2f}" if values else ""


def latest_batch(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    latest = rows[-1].get("created_at", "") if rows else ""
    return [row for row in rows if row.get("created_at") == latest]


def latest_position_rows(limit: int | None = None) -> list[dict[str, str]]:
    rows = []
    seen = set()
    active = active_position_tickers()
    for row in reversed(tail_csv("logs/positions_report.csv", 1000)):
        ticker = row.get("ticker", "")
        if not ticker or ticker in seen or ticker not in active:
            continue
        seen.add(ticker)
        rows.append(row)
        if limit is not None and len(rows) >= limit:
            break
    return rows


def position_summary_rows(limit: int | None = None) -> list[dict[str, str]]:
    """Combine the position report with the latest DB-backed sell evaluation."""
    checks = {}
    for row in recent_position_checks(1000):
        checks.setdefault(row.get("ticker", ""), row)
    rows = []
    for position in latest_position_rows(limit):
        check = checks.get(position.get("ticker", ""), {})
        rows.append({
            **position,
            "holding_days": check.get("holding_days", ""),
            "decision": check.get("decision", "HOLD"),
            "reason": check.get("reasons", ""),
            "dynamic_stop_loss_pct": check.get("dynamic_stop_loss_pct", ""),
        })
    return rows


def header_cell(column: str) -> str:
    attr = " class='num'" if column in NUMERIC_COLUMNS else ""
    return f"<th{attr}>{e(display_label(column))}</th>"


def cell(value: object, column: str = "") -> str:
    display = value if str(value or "") else empty_value_label(column)
    klass = status_class(str(display or ""))
    classes = [klass] if klass else []
    if column in NUMERIC_COLUMNS:
        classes.append("num")
    signed = signed_class(display) if column in RETURN_COLUMNS else ""
    if signed:
        classes.append(signed)
    attr = f" class='{' '.join(classes)}'" if classes else ""
    if column in BOOLEAN_COLUMNS and str(display) in {"0", "1"}:
        shown = "예" if str(display) == "1" else "아니요"
    else:
        shown = format_number(display) if column in NUMERIC_COLUMNS else display_value(display)
    return f"<td{attr}>{e(shown)}</td>"


def empty_value_label(column: str) -> str:
    if column in {"return_3d_pct", "return_5d_pct"}:
        return "수집대기"
    if column == "news_score":
        return "수집대기" if os.environ.get("NEWS_LOOKUP", "0") == "1" and os.environ.get("NEWS_SCORE_WEIGHT", "0") != "0" else "미사용"
    if column == "disclosure_score":
        return "수집대기" if os.environ.get("DART_LOOKUP", "0") == "1" else "미사용"
    return ""


def signed_class(value: object) -> str:
    try:
        number = float(str(value or "").replace("%", "").replace(",", ""))
    except ValueError:
        return ""
    if number > 0:
        return "pos"
    if number < 0:
        return "neg"
    return "zero"


def format_number(value: object) -> str:
    text = str(value or "")
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return text
    return f"{int(number):,}" if number.is_integer() else f"{number:,.2f}"


def card(name: str, value: str) -> str:
    klass = card_class(name, value)
    attr = f" class='{klass}'" if klass else ""
    return f"<div class='card'><b>{e(display_label(name))}</b><span{attr}>{e(display_value(value))}</span></div>"


def card_class(label: str, value: str) -> str:
    if label == "today runs" and "/" in value:
        completed, _, total = value.partition(" ok")[0].partition("/")
        if completed.isdigit() and total.isdigit():
            return "ok" if int(total) > 0 and completed == total else "bad"
    if label == "today issues" and value.isdigit():
        return "bad" if int(value) else "ok"
    if label in {"today recommendations", "today sell alerts"} and value.isdigit():
        return "muted" if int(value) == 0 else "ok"
    return status_class(value)


def status_class(value: str) -> str:
    lowered = value.lower()
    if lowered in {"ok", "none"} or " ok" in lowered:
        return "ok"
    if lowered in {"old"}:
        return "warn"
    if any(word in lowered for word in ("missing", "error", "found", "not-ok")):
        return "bad"
    return ""


def render() -> str:
    checks = "".join(f"<li>{e(line)}</li>" for line in daily_check_lines())
    task_log = "".join(f"<li>{e(line)}</li>" for line in tail_text("logs/task.out.log", 10))
    task_errors = tail_text("logs/task.err.log", 10) or ["none"]
    task_error_items = "".join(f"<li>{e(line)}</li>" for line in task_errors)
    recommendation_rows = today_recommendation_rows()
    sell_rows = today_sell_alert_rows()
    position_rows = position_summary_rows()
    primary_cards = "".join(card(name, value) for name, value in primary_metric_cards(recommendation_rows, sell_rows, position_rows))
    trader_candidates = json.dumps(
        [{key: row.get(key, "") for key in ("ticker", "name", "close", "score", "allocation_pct")} for row in recommendation_rows],
        ensure_ascii=False,
    ).replace("</", "<\\/")
    stock_tab = f"""
<div class="cards">{primary_cards}</div>
{table("Today recommendations", recommendation_rows, ["created_at", "name", "close", "score", "allocation_pct", "volume_ratio", "relative_strength_pct", "reason"])}
{table("Positions", position_rows, ["name", "entry_price", "close", "return_pct", "holding_days", "decision", "reason", "dynamic_stop_loss_pct"])}
{table("Today sell alerts", sell_rows, ["created_at", "name", "return_pct", "summary", "reason"])}
{table("Recommendation stats", performance_summary_rows(), ["metric", "value"])}
{details("성과 상세 보기", table("Recommendation performance", recommendation_performance_rows(), ["pick_date", "ticker", "name", "entry_count", "score", "entry_close", "return_1d_pct", "return_3d_pct", "return_5d_pct", "news_score", "disclosure_score"]) + table("Sell counterfactual performance", recent_sell_outcomes(), ["alert_created_at", "ticker", "name", "execution_date", "execution_price", "return_1d_pct", "return_3d_pct", "return_5d_pct", "return_10d_pct"]))}
"""
    trader_tab = """
<div class="trader-balance"><span>보유금액</span><strong id="trader-balance">0원</strong></div>
<section class="trader-controls">
  <h2>가상 계좌 입금</h2>
  <div class="trader-form"><input id="deposit-amount" type="number" min="1" step="1" placeholder="입금금액(원)"><button id="deposit-button" type="button">보유금액에 적용</button><button id="buy-button" type="button">추천 비중으로 매수</button></div>
  <p class="muted" id="trader-message">입금 후 오늘의 추천 종목을 알고리즘 비중에 따라 정수 수량으로 매수할 수 있습니다.</p>
</section>
<section><h2>가상 트레이더 보유 종목</h2><table><thead><tr><th>종목명</th><th class="num">평가손익</th><th class="num">보유수량</th><th class="num">수익률</th><th class="num">평가금액</th></tr></thead><tbody id="trader-holdings"></tbody></table></section>
"""
    settings_tab = f"""
{table("Issues", issue_rows(), ["source", "item", "status"])}
{table("Today run details", today_run_rows(), ["step", "status"])}
{table("Recommendation shape", recommendation_shape_rows(), ["type", "when", "action"])}
{table("Score breakdown", score_breakdown_rows(), ["created_at", "ticker", "name", "total_score", "volume", "trading_value", "trend", "news", "disclosure", "penalty"])}
{table("Sell alert summary", sell_alert_summary_rows(), ["summary", "count"])}
<section><h2>{e(display_label("Daily check"))}</h2><ul>{checks}</ul></section>
{table("Current settings", settings_rows(), ["setting", "value"])}
{table("Recent deliveries", tail_csv("logs/deliveries.csv", 10), ["created_at", "channel", "status", "message_id", "chat_id_suffix", "error"])}
{table("Performance penalties", performance_penalty_rows(), ["ticker", "name", "penalty"])}
{table("Top recommendation performance", recommendation_rank_rows(), ["ticker", "name", "picks", "avg_1d_return_pct", "win_rate_1d_pct"])}
{table("Worst recommendation performance", recommendation_rank_rows(worst=True), ["ticker", "name", "picks", "avg_1d_return_pct", "win_rate_1d_pct"])}
{table("Recommendations with sell alerts", sell_alerted_recommendation_rows(), ["ticker", "name", "score", "entry_close", "return_1d_pct", "sell_return_pct", "sell_reason"])}
<section><h2>{e(display_label("Recent task log"))}</h2><ul>{task_log}</ul></section>
<section><h2>{e(display_label("Recent task errors"))}</h2><ul>{task_error_items}</ul></section>
"""
    collection_tab = f"""
{collection_highlights()}
{table("Recent strategy runs", recent_runs(), ["started_at", "run_type", "market_date", "strategy_version", "schema_version", "git_commit", "config_hash", "watchlist_hash", "status", "finished_at"])}
{table("Candidate rejection summary", rejection_summary(), ["reason", "count"])}
{table("Latest candidate snapshots", latest_candidates(), ["evaluated_at", "ticker", "name", "close", "raw_volume_ratio", "expected_volume_fraction", "volume_ratio", "raw_trading_value", "trading_value", "ma20", "atr20_pct", "benchmark_symbol", "market_proxy_return_pct", "relative_strength_pct", "relative_strength_score", "legacy_score", "legacy_passed", "final_score", "passed", "selected", "rank", "rejection_reasons"])}
{table("Recent position checks", recent_position_checks(), ["checked_at", "ticker", "name", "entry_price", "close", "holding_days", "return_pct", "max_return_pct", "drawdown_from_peak_pct", "distance_ma20_pct", "atr20_pct", "dynamic_stop_loss_pct", "time_stop_triggered", "decision", "reasons"])}
"""
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{e(display_label("stockAlarm Dashboard"))}</title>
<style>
body{{font-family:Segoe UI,Malgun Gothic,sans-serif;margin:24px;background:#f6f7f9;color:#111}}
h1{{margin-bottom:4px}} .muted{{color:#666}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:18px 0}}
.card{{background:white;border-radius:12px;padding:14px;box-shadow:0 1px 4px #ddd}} .card span{{display:block;font-size:24px;margin-top:8px}}
.highlight-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:16px 0}}
.highlight{{background:white;color:#111827;border-radius:14px;padding:16px;box-shadow:0 1px 4px #ddd;border:1px solid #e5e7eb}} .highlight b{{display:block;color:#475569}} .highlight span{{display:block;color:#111827;font-size:24px;font-weight:800;margin-top:8px}}
.tabs{{margin-top:18px}} .tab-input{{display:none}} .tab-label{{display:inline-block;background:#e9edf3;border-radius:999px;padding:10px 16px;margin-right:8px;cursor:pointer;font-weight:600}}
.tab-panel{{display:none}} #tab-stocks:checked~.tab-labels label[for="tab-stocks"],#tab-settings:checked~.tab-labels label[for="tab-settings"],#tab-collection:checked~.tab-labels label[for="tab-collection"]{{background:#111;color:white}}
.tab-panel{{display:none}} #tab-stocks:checked~.tab-labels label[for="tab-stocks"],#tab-trader:checked~.tab-labels label[for="tab-trader"],#tab-settings:checked~.tab-labels label[for="tab-settings"],#tab-collection:checked~.tab-labels label[for="tab-collection"]{{background:#111;color:white}}
#tab-stocks:checked~#stocks-panel,#tab-trader:checked~#trader-panel,#tab-settings:checked~#settings-panel,#tab-collection:checked~#collection-panel{{display:block}}
.legacy-sections,.legacy-order{{display:none}}
section{{background:white;border-radius:12px;padding:16px;margin:16px 0;box-shadow:0 1px 4px #ddd;overflow:auto}}
details{{background:#eef2f6;border-radius:12px;margin:16px 0}} details summary{{cursor:pointer;padding:14px 16px;font-weight:700}} .details-body{{padding:0 16px 1px}} .details-body section{{box-shadow:none;border:1px solid #e5e7eb}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{border-bottom:1px solid #eee;text-align:left;padding:8px;white-space:nowrap}} th{{background:#fafafa}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
.ok{{color:#147a2e;font-weight:600}} .warn{{color:#9a6700;font-weight:600}} .bad{{color:#b42318;font-weight:600}} .pos{{color:#047857;font-weight:700}} .neg{{color:#dc2626;font-weight:700}} .zero{{color:#64748b;font-weight:600}}
.pager{{display:flex;gap:6px;align-items:center;justify-content:center;margin-top:10px}} .pager button{{border:1px solid #d0d5dd;background:white;border-radius:8px;padding:6px 10px;cursor:pointer}} .pager button.active{{background:#111;color:white;border-color:#111}}
.trader-balance{{background:#111827;color:white;border-radius:14px;padding:20px;margin:18px 0}} .trader-balance span{{display:block;color:#cbd5e1}} .trader-balance strong{{display:block;font-size:32px;margin-top:6px}} .trader-form{{display:flex;gap:8px;flex-wrap:wrap}} .trader-form input{{min-width:220px;padding:10px;border:1px solid #d0d5dd;border-radius:8px}} .trader-form button{{padding:10px 14px;border:0;border-radius:8px;background:#111;color:white;cursor:pointer}} .trader-form button:disabled{{opacity:.4;cursor:not-allowed}}
li{{margin:4px 0}}
</style>
</head>
<body>
<h1>{e(display_label("stockAlarm Dashboard"))}</h1>
<div class="muted">{e(display_label("generated"))} {e(datetime.now().isoformat(timespec="seconds"))}</div>
<div class="legacy-order">{e(display_label("Issues"))} {e(display_label("Today run details"))} {e(display_label("Today recommendations"))} {e(display_label("Recommendation shape"))} {e(display_label("Score breakdown"))} {e(display_label("Why recommended"))} {e(display_label("Sell alert summary"))} {e(display_label("Recent sell alerts"))} {e(display_label("Recommendation stats"))}</div>
<div class="tabs">
<input class="tab-input" id="tab-stocks" name="tabs" type="radio" checked>
<input class="tab-input" id="tab-trader" name="tabs" type="radio">
<input class="tab-input" id="tab-settings" name="tabs" type="radio">
<input class="tab-input" id="tab-collection" name="tabs" type="radio">
<div class="tab-labels">
<label class="tab-label" for="tab-stocks">핵심 요약</label>
<label class="tab-label" for="tab-trader">가상 트레이더</label>
<label class="tab-label" for="tab-collection">수집 데이터</label>
<label class="tab-label" for="tab-settings">상세 진단</label>
</div>
<div class="tab-panel" id="stocks-panel">{stock_tab}</div>
<div class="tab-panel" id="trader-panel">{trader_tab}</div>
<div class="tab-panel" id="settings-panel">{settings_tab}</div>
<div class="tab-panel" id="collection-panel">{collection_tab}</div>
</div>
<script>
const traderCandidates = {trader_candidates};
const traderKey = "stockAlarm.virtualTrader.v1";
const traderApiBase = location.protocol === "file:" ? "http://127.0.0.1:8765" : "";
let trader = {{cash:0, holdings:[]}};
const won = value => `${{Math.round(value).toLocaleString("ko-KR")}}원`;
async function traderRequest(path, options={{}}) {{
  const response = await fetch(`${{traderApiBase}}${{path}}`, {{headers:{{"Content-Type":"application/json"}}, ...options}});
  const body = await response.json();
  if(!response.ok) throw new Error(body.error || "요청을 처리하지 못했습니다.");
  return body;
}}
function renderTrader(message="") {{
  document.getElementById("trader-balance").textContent = won(trader.cash || 0);
  const body = document.getElementById("trader-holdings"); body.textContent = "";
  (trader.holdings || []).forEach(item => {{
    const row = document.createElement("tr");
    const nameWithEntry = `${{item.name}} (진입가 ${{won(item.average_price)}})`;
    [nameWithEntry, won(item.profit_loss), Number(item.quantity).toLocaleString("ko-KR"), `${{Number(item.return_pct).toFixed(2)}}%`, won(item.valuation)].forEach((value, index) => {{ const cell=document.createElement("td"); cell.textContent=value; if(index>0) cell.className="num" + (index===1||index===3 ? (Number(String(value).replace(/[^0-9.-]/g,""))>0?" pos":Number(String(value).replace(/[^0-9.-]/g,""))<0?" neg":" zero") : ""); row.appendChild(cell); }}); body.appendChild(row);
  }});
  if (!body.children.length) body.innerHTML='<tr><td colspan="5" class="muted">매수한 종목이 없습니다.</td></tr>';
  document.getElementById("buy-button").disabled = !(trader.cash > 0 && traderCandidates.length);
  if(message) document.getElementById("trader-message").textContent=message;
}}
document.getElementById("deposit-button").addEventListener("click", async () => {{ const input=document.getElementById("deposit-amount"), amount=Math.floor(Number(input.value)); if(!(amount>0)) return renderTrader("1원 이상의 입금금액을 입력해 주세요."); try {{ trader=await traderRequest("/api/trader/deposit",{{method:"POST",body:JSON.stringify({{amount}})}}); input.value=""; renderTrader(`${{won(amount)}}을 DB 계좌에 입금했습니다.`); }} catch(error) {{ renderTrader(error.message); }} }});
document.getElementById("buy-button").addEventListener("click", async () => {{
  try {{ trader=await traderRequest("/api/trader/buy",{{method:"POST",body:"{{}}"}}); renderTrader(`${{trader.bought}}개 종목을 ${{won(trader.spent)}}에 가상 매수했습니다.`); }} catch(error) {{ renderTrader(error.message); }}
}});
const legacyTrader = localStorage.getItem(traderKey);
(legacyTrader ? traderRequest("/api/trader/import",{{method:"POST",body:legacyTrader}}) : traderRequest("/api/trader"))
  .then(state => {{trader=state; if(state.imported) localStorage.removeItem(traderKey); renderTrader(state.imported?"기존 브라우저 가상 계좌를 DB로 이전했습니다.":"");}})
  .catch(() => renderTrader("open_dashboard.bat으로 열어야 DB 가상 계좌를 사용할 수 있습니다."));
document.querySelectorAll("section").forEach((section) => {{
  const rows = [...section.querySelectorAll("tbody tr")];
  const pager = section.querySelector(".pager");
  if (!pager) return;
  const pageSize = Number(pager.dataset.pageSize || {PAGE_SIZE});
  const pageCount = Math.ceil(rows.length / pageSize);
  let currentPage = 0;
  const show = (page) => {{
    currentPage = Math.max(0, Math.min(page, pageCount - 1));
    rows.forEach((row, index) => row.style.display = Math.floor(index / pageSize) === currentPage ? "" : "none");
    pager.querySelectorAll("button.page-number").forEach(button => {{ const target=Number(button.dataset.page); button.hidden=Math.floor(target/5)!==Math.floor(currentPage/5); button.classList.toggle("active", target===currentPage); }});
  }};
  const prev=document.createElement("button"); prev.type="button"; prev.textContent="‹"; prev.addEventListener("click",()=>show(currentPage-1)); pager.appendChild(prev);
  for (let page = 0; page < pageCount; page++) {{
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = String(page + 1);
    button.className = "page-number"; button.dataset.page=String(page);
    button.addEventListener("click", () => show(page));
    pager.appendChild(button);
  }}
  const next=document.createElement("button"); next.type="button"; next.textContent="›"; next.addEventListener("click",()=>show(currentPage+1)); pager.appendChild(next);
  show(0);
}});
</script>
</body>
</html>"""


def write(path: str = OUT_PATH) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(render())
    return path


def main() -> None:
    try:
        load_env()
        print(write())
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()


