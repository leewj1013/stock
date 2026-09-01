from __future__ import annotations

import html
import json
import os
import csv
from datetime import datetime
from statistics import mean

from .app import load_env, performance_penalty, write_error_log
from .daily_check import lines as daily_check_lines, run_log_statuses
from .data_store import (
    latest_portfolio_risk, recent_position_checks, recent_price_quality, recent_runs,
    recent_sell_outcomes, recent_virtual_trades, rejection_summary,
)
from .health import lines as health_lines
from .positions_check import active_position_tickers
from .report import daily_ticker_rows, reconciled_daily_alert_rows, tail_csv, tail_text
from .sell_check import position_was_alerted


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
    "virtual_target_pct",
    "dynamic_stop_loss_pct",
    "atr20_pct",
    "raw_volume_ratio",
    "expected_volume_fraction",
    "relative_strength_pct",
    "relative_strength_score",
    "avg_realized_return_pct",
    "rebound_5d_rate_pct",
    "total_return_pct",
    "mdd_pct",
    "sharpe",
    "current_price",
    "sell_alert_return_pct",
    "sell_alert_price",
}
RETURN_COLUMNS = {"return_pct", "avg_1d_return_pct", "return_1d_pct", "sell_return_pct", "return_3d_pct", "return_5d_pct", "return_10d_pct", "return_20d_pct", "mfe_20d_pct", "mae_20d_pct", "avg_realized_return_pct", "total_return_pct", "mdd_pct", "sell_alert_return_pct"}
TIMESTAMP_COLUMNS = {"created_at", "started_at", "finished_at", "evaluated_at", "checked_at", "alert_created_at"}
BOOLEAN_COLUMNS = {"passed", "selected", "legacy_passed", "time_stop_triggered"}
LABELS = {
    "stockAlarm Dashboard": "국내주식 알림 대시보드",
    "generated": "생성 시각",
    "positions": "보유 종목",
    "tracked positions": "추천 성과 추적 종목",
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
    "Positions": "추천 성과 추적 종목",
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
    "virtual_target_pct": "가상주문 목표 비중(%)",
    "allocation_rule": "비중 규칙",
    "quantity": "보유수량",
    "valuation": "평가금액",
    "profit_loss": "평가손익",
    "notification_status": "알림 상태",
    "virtual_order_status": "가상매수",
    "delivery failures": "오늘 알림 실패",
    "Price quality": "가격 데이터 품질",
    "Strategy versions": "전략 버전 이력",
    "Portfolio risk": "포트폴리오 위험 상태",
    "price_date": "가격 기준일",
    "version_id": "버전 번호",
    "effective_date": "적용일",
    "sample_count": "학습 표본 수",
    "objective_return": "신규전략 수익률",
    "baseline_return": "기존전략 수익률",
    "max_drawdown": "최대 낙폭",
    "daily_return_pct": "일간 수익률",
    "weekly_return_pct": "주간 수익률",
    "drawdown_pct": "고점 대비 낙폭",
    "exposure_pct": "보유 비중",
    "version": "버전",
    "rows": "데이터 수",
    "completed_1d": "1일 성과 완료",
    "suggested_min_score": "추천 최소점수 제안",
    "learning samples": "학습 가능 표본",
    "remaining samples": "300표본까지 남음",
    "completed 5d": "5일 성과 완료",
    "completed 20d": "20일 성과 완료",
    "last collected": "최근 수집 시각",
    "sell_reason_group": "매도 사유",
    "avg_realized_return_pct": "평균 매도수익률",
    "rebound_5d_rate_pct": "5일 내 반등 비율",
    "assessment": "판정",
    "strategy": "전략",
    "total_return_pct": "총수익률",
    "mdd_pct": "최대낙폭",
    "sharpe": "샤프지수",
    "statistical_result": "통계 판정",
    "tracking_status": "추적 상태",
    "sell_alert_date": "매도 알림일",
    "sell_alert_return_pct": "매도 알림 수익률",
    "sell_alert_price": "매도가",
    "virtual_bought": "가상매수",
    "current_price": "현재가",
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
    "ALREADY_ALERTED": "매도 알림 완료",
    "sell_alert_after_entry": "이 종목은 이미 매도 검토 알림을 보냄",
    "recommendation": "추천 점검",
    "completed": "완료",
    "volume_ratio": "거래량 기준 미달",
    "below_ma20": "20일선 아래",
    "entry_day_change": "당일 변동폭 과다",
    "extended_above_ma20": "20일선 과대 이격",
    "trading_value": "거래대금 기준 미달",
    "day_change": "등락률 기준 미달",
    "insufficient_history": "과거 데이터 부족",
    "previous_sell_alert": "이전 매도 알림 있음",
    "intraday": "장중",
    "daily": "마감",
    "True": "예",
    "False": "아니요",
    "active": "정상 운영",
    "reduced": "축소 운영",
    "halted": "신규매수 중단",
    "baseline": "기준 전략",
    "entry": "신규 진입",
    "price": "가격",
    "quality": "품질",
    "success": "성공",
    "failed": "실패",
    "run": "실행 작업",
    "positions_report": "보유종목 평가",
    "recommendation_performance": "추천 성과 계산",
    "naver": "네이버 금융",
    "valid": "정상",
    "invalid": "비정상",
    "reference_unavailable": "비교가격 확인 불가",
    "notifier returned false": "알림 전송 실패",
    "stock_alarm": "stockAlarm",
    "kospi_buy_hold": "KOSPI 매수후보유",
    "equal_weight_buy_hold": "동일가중 매수후보유",
    "momentum": "단순 모멘텀",
    "random_median": "랜덤 선택 중앙값",
}


def display_label(value: str) -> str:
    return LABELS.get(value, value)


def display_value(value: object) -> str:
    text = str(value or "")
    if text in DISPLAY_VALUES:
        return DISPLAY_VALUES[text]
    if "," in text and all(part.strip() in DISPLAY_VALUES for part in text.split(",")):
        return ", ".join(DISPLAY_VALUES[part.strip()] for part in text.split(","))
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
    if text.endswith(" error") and text.removesuffix(" error").isdigit():
        return f"{text.removesuffix(' error')}건 오류"
    return text


def e(value: object) -> str:
    return html.escape(str(value or ""))


def today_delivery_failure_count() -> int:
    today = datetime.now().date().isoformat()
    return sum(
        1
        for row in tail_csv("logs/deliveries.csv", 10000)
        if row.get("created_at", "").startswith(today)
        and not (row.get("channel") == "telegram" and row.get("status") == "delivered")
        and row.get("status") != "skipped_duplicate"
    )


def today_recommendation_rows(limit: int | None = None) -> list[dict[str, str]]:
    today = datetime.now().date().isoformat()
    performance = {row.get("ticker", ""): row for row in tail_csv("logs/recommendation_performance.csv", 1000)}
    deliveries = tail_csv("logs/deliveries.csv", 10000)
    delivery_statuses = {}
    for delivery in deliveries:
        if not delivery.get("created_at", "").startswith(today) or delivery.get("event_type") != "recommendation":
            continue
        status = "전송 완료" if delivery.get("channel") == "telegram" and delivery.get("status") == "delivered" else "전송 실패"
        for ticker in delivery.get("tickers", "").split("|"):
            if ticker.strip():
                delivery_statuses[ticker.strip()] = status
    bought = {
        row.get("ticker", "") for row in recent_virtual_trades(1000)
        if str(row.get("created_at", "")).startswith(today)
    }
    rows = []
    daily_rows = daily_ticker_rows(tail_csv("logs/recommendations.csv", 10000), today)
    for row in daily_rows:
        ticker = row.get("ticker", "")
        rows.append({
            **row,
            "virtual_target_pct": f"{min(30.0, max(10.0, float(row.get('allocation_pct') or 10))):.2f}",
            "allocation_rule": "현재 총자산 10% 고정" if abs(float(row.get("allocation_pct") or 0) - 10) < 0.01 else "이전 규칙 산출값",
            "notification_status": delivery_statuses.get(ticker, "미전송"),
            "virtual_order_status": "체결" if ticker in bought else "미체결",
            "reason": reason_summary(row, performance.get(ticker, {}), performance_penalty(ticker)),
        })
    return rows[:limit] if limit is not None else rows


def today_sell_alert_rows(limit: int | None = None) -> list[dict[str, str]]:
    today = datetime.now().date().isoformat()
    rows = reconciled_daily_alert_rows(
        tail_csv("logs/sell_alerts.csv", 10000), tail_csv("logs/deliveries.csv", 10000), today, "sell"
    )
    return rows[:limit] if limit else rows


def today_issue_count() -> int:
    return len(actionable_issue_rows())


def today_run_rows() -> list[dict[str, str]]:
    rows = []
    for line in run_log_statuses():
        name, separator, status = line.partition("=")
        if separator:
            rows.append({"step": name, "status": status})
    return rows


def recommendation_shape_rows() -> list[dict[str, str]]:
    return [
        {"type": "관심 후보", "when": "거래량 급증 + 20일선 상회 + 거래대금 충분", "action": "추천 알림 발송"},
        {"type": "확인 필요", "when": "뉴스/공시/과거 성과 보너스 또는 감점 있음", "action": "대시보드 사유 확인"},
        {"type": "매도 검토", "when": "손절, 급락, 수익 반납 조건 발생", "action": "매도 검토 알림 발송"},
    ]


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


def settings_rows() -> list[dict[str, str]]:
    rows = []
    for line in health_lines():
        key, separator, value = line.partition("=")
        if separator:
            rows.append({"setting": key, "value": value})
    return rows


def issue_rows() -> list[dict[str, str]]:
    return actionable_issue_rows() or [{"source": "대시보드", "item": "조치할 문제", "status": "없음"}]


def actionable_issue_rows() -> list[dict[str, str]]:
    """Return distinct problems that can affect alerts, prices, or virtual trading.

    Expected non-trading-day omissions and transient health probes are deliberately
    excluded. Repeated delivery failures are grouped so the badge counts causes,
    not raw log events.
    """
    rows: list[dict[str, str]] = []
    delivery_failures = today_delivery_failure_count()
    if delivery_failures:
        rows.append({
            "source": "텔레그램",
            "item": "알림 전송 실패",
            "status": f"{delivery_failures}회 반복 · 콘솔로 대체",
        })

    if datetime.now().weekday() < 5:
        for row in today_run_rows():
            status = str(row.get("status") or "")
            if status.lower() in {"error", "failed", "not-ok"}:
                rows.append({"source": "자동 작업", "item": display_value(row.get("step", "")), "status": display_value(status)})

    latest_by_ticker: dict[str, dict] = {}
    for row in recent_price_quality(200):
        latest_by_ticker.setdefault(str(row.get("ticker") or ""), row)
    for ticker, row in latest_by_ticker.items():
        if ticker and row.get("status") == "invalid":
            rows.append({"source": "가격 데이터", "item": ticker, "status": display_value(row.get("reason") or "invalid")})

    risk = latest_portfolio_risk()
    if risk.get("status") == "halted":
        rows.append({"source": "가상매매", "item": "신규매수 중단", "status": display_value(risk.get("reason") or "위험 한도 도달")})
    return rows


def table(title: str, rows: list[dict[str, str]], columns: list[str]) -> str:
    rows = sort_table_rows(rows, columns)
    body = "".join(
        f"<tr data-row='{index}'>" + "".join(cell(row.get(column, ""), column) for column in columns) + "</tr>"
        for index, row in enumerate(rows)
    )
    head = "".join(header_cell(column) for column in columns)
    pager = f"<div class='pager' data-page-size='{PAGE_SIZE}'><span>총 {len(rows)}건</span></div>" if len(rows) > PAGE_SIZE else ""
    return f"<section><h2>{e(display_label(title))}</h2><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{pager}</section>"


def user_table(title: str, rows: list[dict], columns: list[str], empty_message: str) -> str:
    """Render a user-facing empty state instead of an unexplained header-only table."""
    if rows:
        return table(title, rows, columns)
    return f"<section class='empty-section'><h2>{e(display_label(title))}</h2><div class='empty-state'><b>현재 항목이 없습니다</b><span>{e(empty_message)}</span></div></section>"


def user_run_rows() -> list[dict[str, str]]:
    rows = today_run_rows()
    if datetime.now().weekday() >= 5:
        return [row for row in rows if str(row.get("status") or "").lower() != "missing"]
    return rows


def learning_progress() -> dict[str, int]:
    rows = data_accumulation_rows()
    sample_row = next((row for row in rows if row.get("metric") == "20일 학습 표본"), {})
    text = str(sample_row.get("value") or "0 / 300")
    current_text, _, target_text = text.partition("/")
    try:
        current, target = int(current_text.strip()), int(target_text.strip())
    except ValueError:
        current, target = 0, 300
    return {"current": current, "target": target, "percent": min(100, round(current / target * 100)) if target else 0}


def sort_table_rows(rows: list[dict[str, str]], columns: list[str]) -> list[dict[str, str]]:
    """Show the newest ISO-formatted creation timestamp first when visible."""
    if "created_at" not in columns:
        return list(rows)
    return sorted(rows, key=lambda row: (bool(row.get("created_at")), row.get("created_at", "")), reverse=True)


def details(title: str, content: str) -> str:
    return f"<details><summary>{e(title)}</summary><div class='details-body'>{content}</div></details>"


def latest_position_rows(limit: int | None = None) -> list[dict[str, str]]:
    rows = []
    seen = set()
    active = active_position_tickers()
    for row in reversed(tail_csv("logs/positions_report.csv", 1000)):
        ticker = row.get("ticker", "")
        if not ticker or ticker in seen or ticker not in active:
            continue
        # A ticker can be recommended again after a prior sell alert. Filter by
        # the individual entry date so the old, already-alerted entry is not
        # shown as an active recommendation for the newer entry.
        if position_was_alerted(row):
            continue
        seen.add(ticker)
        rows.append(row)
        if limit is not None and len(rows) >= limit:
            break
    return rows


def position_summary_rows(limit: int | None = None) -> list[dict[str, str]]:
    """Combine the position report with the latest DB-backed sell evaluation."""
    checks: dict[tuple[str, str], dict] = {}
    ticker_checks: dict[str, dict] = {}
    for row in recent_position_checks(1000):
        ticker = str(row.get("ticker") or "")
        position_id = str(row.get("position_id") or "")
        if position_id:
            checks.setdefault((ticker, position_id), row)
        ticker_checks.setdefault(ticker, row)
    rows = []
    for position in latest_position_rows(limit):
        ticker = str(position.get("ticker") or "")
        position_id = str(position.get("position_id") or "")
        check = checks.get((ticker, position_id), ticker_checks.get(ticker, {}))
        if check.get("decision") == "ALREADY_ALERTED":
            continue
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
        if column in TIMESTAMP_COLUMNS and isinstance(shown, str) and "T" in shown:
            shown = shown.replace("T", " ")
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


def data_accumulation_rows() -> list[dict[str, str]]:
    """Summarize only the sample milestones a user needs to judge learning readiness."""
    rows = tail_csv("logs/recommendation_performance.csv", 100000)
    unique = {(row.get("pick_date", ""), row.get("ticker", "")): row for row in rows}
    samples = list(unique.values())
    completed_5d = sum(bool(row.get("return_5d_pct")) for row in samples)
    completed_20d = sum(bool(row.get("return_20d_pct")) for row in samples)
    minimum = max(300, int(os.environ.get("STRATEGY_LEARNING_MIN_SAMPLES", "300")))
    usable = completed_20d
    timestamps = [
        str(row.get(key) or "")
        for source, key in ((recent_runs(1), "finished_at"), (recent_price_quality(1), "created_at"))
        for row in source
        if row.get(key)
    ]
    return [
        {"metric": "누적 추천 표본", "value": str(len(samples)), "status": "축적 중"},
        {"metric": "5일 성과 완료", "value": str(completed_5d), "status": "분석 가능" if completed_5d else "대기"},
        {"metric": "20일 학습 표본", "value": f"{usable} / {minimum}", "status": "학습 가능" if usable >= minimum else "축적 중"},
        {"metric": "최근 수집 시각", "value": max(timestamps) if timestamps else "기록 없음", "status": "정상" if timestamps else "확인 필요"},
    ]


def benchmark_summary_rows() -> list[dict[str, str]]:
    """Read the latest formal, isolated benchmark output without rerunning analysis."""
    path = os.path.join("reports", "backtest", "benchmark_comparison", "strategy_metrics.csv")
    rows: list[dict[str, str]] = []
    try:
        with open(path, encoding="utf-8-sig", newline="") as file:
            source = list(csv.DictReader(file))
    except OSError:
        return []
    wanted = ("stock_alarm", "kospi_buy_hold", "equal_weight_buy_hold", "momentum")
    by_strategy = {row.get("strategy"): row for row in source if row.get("regime") == "all"}
    for strategy in wanted:
        row = by_strategy.get(strategy)
        if row:
            rows.append({
                "strategy": strategy,
                "total_return_pct": row.get("total_return_pct", ""),
                "mdd_pct": row.get("mdd_pct", ""),
                "sharpe": row.get("sharpe", ""),
            })
    random_path = os.path.join("reports", "backtest", "benchmark_comparison", "random_summary.csv")
    try:
        with open(random_path, encoding="utf-8-sig", newline="") as file:
            random_row = next((row for row in csv.DictReader(file) if row.get("regime") == "all"), None)
        if random_row:
            rows.append({"strategy": "random_median", "total_return_pct": random_row.get("median_total_return_pct", ""), "mdd_pct": random_row.get("mdd_pct", ""), "sharpe": random_row.get("sharpe", "")})
    except OSError:
        pass
    return rows


def sell_quality_rows() -> list[dict[str, str]]:
    """Aggregate sell alerts into user-facing quality indicators."""
    alerts = tail_csv("logs/sell_alerts.csv", 10000)
    outcomes = {(row.get("ticker"), row.get("alert_created_at")): row for row in recent_sell_outcomes(10000)}
    groups: dict[str, list[tuple[float, float | None]]] = {}
    for alert in alerts:
        reason = f"{alert.get('summary', '')} {alert.get('reason', '')}"
        group = "손절" if "손절" in reason or "손실" in reason else "20일선 이탈" if "20일선" in reason else "기간청산" if "기간" in reason else "수익보호" if "수익" in reason or "익절" in reason else "기타"
        try:
            realized = float(alert.get("return_pct") or 0)
        except ValueError:
            continue
        outcome = outcomes.get((alert.get("ticker"), alert.get("created_at")), {})
        try:
            rebound = float(outcome["return_5d_pct"]) if outcome.get("return_5d_pct") not in (None, "") else None
        except (ValueError, TypeError):
            rebound = None
        groups.setdefault(group, []).append((realized, rebound))
    result = []
    for group, values in sorted(groups.items(), key=lambda item: len(item[1]), reverse=True):
        rebounds = [value for _, value in values if value is not None]
        rebound_rate = sum(value > 0 for value in rebounds) / len(rebounds) * 100 if rebounds else None
        assessment = "반복 손실 주의" if mean(value for value, _ in values) < 0 and rebound_rate is not None and rebound_rate >= 50 else "관찰"
        result.append({
            "sell_reason_group": group,
            "count": str(len(values)),
            "avg_realized_return_pct": f"{mean(value for value, _ in values):.2f}",
            "rebound_5d_rate_pct": f"{rebound_rate:.1f}" if rebound_rate is not None else "수집 중",
            "assessment": assessment,
        })
    return result


def recommendation_tracking_rows() -> list[dict[str, str]]:
    """Join recommendation outcomes, sell alerts and paper orders for user review."""
    performance = tail_csv("logs/recommendation_performance.csv", 100000)
    recommendations = tail_csv("logs/recommendations.csv", 100000)
    base: dict[tuple[str, str], dict[str, str]] = {}
    for row in [*performance, *recommendations]:
        key = (str(row.get("pick_date") or str(row.get("created_at") or "")[:10]), str(row.get("ticker") or ""))
        if not key[0] or not key[1]:
            continue
        base.setdefault(key, {}).update({key_name: str(value or "") for key_name, value in row.items()})

    current = {str(row.get("ticker") or ""): row for row in latest_position_rows()}
    alerts: dict[str, list[dict[str, str]]] = {}
    for row in tail_csv("logs/sell_alerts.csv", 100000):
        ticker = str(row.get("ticker") or "")
        if ticker:
            alerts.setdefault(ticker, []).append(row)
    for ticker in alerts:
        alerts[ticker].sort(key=lambda item: str(item.get("created_at") or ""))
    bought_dates: dict[str, list[str]] = {}
    for row in recent_virtual_trades(100000):
        ticker = str(row.get("ticker") or "")
        created_date = str(row.get("created_at") or "")[:10]
        if ticker and created_date:
            bought_dates.setdefault(ticker, []).append(created_date)

    result = []
    for (pick_date, ticker), row in base.items():
        position = current.get(ticker, {})
        alert = next(
            (item for item in alerts.get(ticker, []) if str(item.get("created_at") or "")[:10] >= pick_date),
            {},
        )
        alert_date = str(alert.get("created_at") or "")[:10]
        if alert_date and alert_date >= pick_date:
            status = "매도 알림"
        elif row.get("return_20d_pct"):
            status = "성과 완료"
        elif position:
            status = "추적 중"
        else:
            status = "성과 수집 중"
        virtual_bought = False
        try:
            pick_day = datetime.fromisoformat(pick_date).date()
            virtual_bought = any(
                0 <= (datetime.fromisoformat(day).date() - pick_day).days <= 7
                for day in bought_dates.get(ticker, [])
            )
        except ValueError:
            virtual_bought = False
        return_value = position.get("return_pct") or alert.get("return_pct") or next(
            (row.get(column) for column in ("return_20d_pct", "return_10d_pct", "return_5d_pct", "return_3d_pct", "return_1d_pct") if row.get(column)), ""
        )
        result.append({
            "name": str(row.get("name") or position.get("name") or alert.get("name") or ticker),
            "pick_date": pick_date,
            "entry_price": str(row.get("entry_close") or row.get("close") or position.get("entry_price") or ""),
            "current_price": str(position.get("close") or alert.get("close") or ""),
            "return_pct": str(return_value or ""),
            "tracking_status": status,
            "sell_alert_date": alert_date if alert_date >= pick_date else "",
            "sell_alert_price": str(alert.get("close") or "") if alert_date >= pick_date else "",
            "sell_alert_return_pct": str(alert.get("return_pct") or "") if alert_date >= pick_date else "",
            "sell_reason": str(alert.get("summary") or alert.get("reason") or "") if alert_date >= pick_date else "",
            "virtual_bought": "매수" if virtual_bought else "미매수",
        })
    return sorted(result, key=lambda row: (row["pick_date"], row["name"]), reverse=True)


def recommendation_tracking_summary(rows: list[dict[str, str]]) -> list[tuple[str, str]]:
    returns = []
    for row in rows:
        try:
            returns.append(float(row.get("return_pct") or ""))
        except ValueError:
            continue
    return [
        ("현재 추적", f"{sum(row['tracking_status'] in {'추적 중', '성과 수집 중'} for row in rows)}종목"),
        ("매도 알림", f"{sum(row['tracking_status'] == '매도 알림' for row in rows)}종목"),
        ("평균 수익률", f"{mean(returns):+.2f}%" if returns else "-"),
        ("수익 종목 비율", f"{sum(value > 0 for value in returns) / len(returns) * 100:.1f}%" if returns else "-"),
    ]


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
    tracking_rows = recommendation_tracking_rows()
    issue_count = today_issue_count()
    progress = learning_progress()
    trader_candidates = json.dumps(
        [{key: row.get(key, "") for key in ("ticker", "name", "close", "score", "allocation_pct")} for row in recommendation_rows],
        ensure_ascii=False,
    ).replace("</", "<\\/")
    stock_tab = f"""
<div class="home-heading"><div><h2>오늘의 투자 현황</h2><p class="muted">추천과 가상 주문 결과를 한눈에 확인하세요.</p></div><span class="system-pill {'bad' if issue_count else 'ok'}">{'확인할 문제 ' + str(issue_count) + '건' if issue_count else '시스템 정상'}</span></div>
<div class="home-cards">
  <div class="summary-card primary"><b>가상계좌 총자산</b><strong id="home-total-equity">불러오는 중</strong><small>현금 + 보유주식 평가액</small></div>
  <div class="summary-card"><b>계좌 총수익률</b><strong id="home-total-return">-</strong><small id="home-total-profit">입금원금 대비</small></div>
  <div class="summary-card"><b>오늘 추천</b><strong>{len(recommendation_rows)}종목</strong><small>추천 알고리즘 선정</small></div>
  <div class="summary-card"><b>오늘 가상주문</b><strong id="home-orders">-</strong><small>매수·매도 체결</small></div>
</div>
<section class="home-operation"><h2>현재 운영 상태</h2><div class="operation-grid">
  <div><span>자동매매</span><b id="home-auto-status">확인 중</b></div>
  <div><span>시장 모드</span><b id="home-market-mode">확인 중</b></div>
  <div><span>오늘 계좌 손익</span><b id="home-daily-profit">-</b></div>
  <div><span>최근 가격 갱신</span><b id="home-price-updated">확인 중</b></div>
</div></section>
{user_table("Today recommendations", recommendation_rows, ["name", "close", "virtual_target_pct", "virtual_order_status"], "오늘 신규 추천 신호가 없습니다.")}
{user_table("Today sell alerts", sell_rows, ["name", "return_pct", "summary"], "오늘 매도 조건을 충족한 보유종목이 없습니다.")}
{details("추천 성과 추적 보기", table("Positions", position_rows, ["name", "entry_price", "close", "return_pct", "decision"]))}
"""
    tracking_cards = "".join(
        f"<div class='tracking-card'><span>{e(label)}</span><b>{e(value)}</b></div>"
        for label, value in recommendation_tracking_summary(tracking_rows)
    )
    tracking_tab = f"""
<div class="home-heading"><div><h2>추천종목 추적</h2><p class="muted">가상매수 여부와 관계없이 추천 이후의 성과와 매도 알림을 관리합니다.</p></div><span class="system-pill">총 {len(tracking_rows)}건</span></div>
<div class="tracking-summary">{tracking_cards}</div>
{user_table("추천 추적 내역", tracking_rows, ["name", "pick_date", "entry_price", "current_price", "return_pct", "tracking_status", "sell_alert_date", "sell_alert_price", "sell_alert_return_pct", "sell_reason", "virtual_bought"], "아직 추적할 추천종목이 없습니다.")}
"""
    trader_tab = """
<div class="trader-account-grid">
  <div class="trader-balance primary"><span>총자산</span><strong id="trader-total-equity">0원</strong></div>
  <div class="trader-balance"><span>주문 가능 현금</span><strong id="trader-cash">0원</strong></div>
  <div class="trader-balance"><span>주식 평가액</span><strong id="trader-holdings-value">0원</strong></div>
  <div class="trader-balance"><span>보유종목 총수익률</span><strong id="trader-holdings-return">0.00%</strong></div>
</div>
<div class="trader-breakdown"><span>계좌 총수익률 <b id="trader-total-return">0.00%</b></span><span>총손익 <b id="trader-total-profit">0원</b></span></div>
<div class="trader-chart-grid">
  <section class="donut-card" aria-labelledby="asset-chart-title"><h2 id="asset-chart-title">가상계좌 자산 구성</h2><div class="donut-layout"><div class="donut-ring" id="asset-donut" role="img" aria-label="자산 구성 데이터 대기"><div class="donut-hole"><span>총자산</span><b id="asset-donut-total">0원</b></div></div><div class="donut-legend" id="asset-donut-legend"></div></div></section>
  <section class="donut-card" aria-labelledby="sector-chart-title"><h2 id="sector-chart-title">보유종목 업종 비중</h2><div class="donut-layout"><div class="donut-ring" id="sector-donut" role="img" aria-label="업종 비중 데이터 대기"><div class="donut-hole"><span>보유 업종</span><b id="sector-donut-count">0개</b></div></div><div class="donut-legend" id="sector-donut-legend"></div></div></section>
</div>
<section class="order-status"><h2>현재 주문 상태</h2>
<div class="trader-risk-grid">
  <div><span>자동매매</span><b id="trader-auto-status">확인 중</b></div>
  <div><span>시장·신규투자 한도</span><b><span id="trader-market-mode">확인 중</span> · <span id="trader-market-limit">-</span></b></div>
  <div><span>현재 보유 비중</span><b id="trader-exposure">0.00%</b></div>
  <div><span>위험관리</span><b id="trader-risk">초기화 전</b></div>
</div>
<div class="trader-status" aria-live="polite"><span id="trader-price-status">가격 기준시각 확인 중</span><span>적용 전략 <b id="trader-strategy">기본 전략</b> · 일간 <b id="trader-daily-return">0.00%</b> · 주간 <b id="trader-weekly-return">0.00%</b> · 최대낙폭 <b id="trader-drawdown">0.00%</b></span></div></section>
<details class="account-actions"><summary>입금 및 수동 주문</summary><div class="details-body"><section class="trader-controls">
  <div class="trader-form"><label for="deposit-amount">입금금액(원)</label><input id="deposit-amount" type="number" min="1" step="1" inputmode="numeric" placeholder="예: 10000000"><button id="deposit-button" type="button">현금 입금</button><button id="buy-button" type="button" title="자동매매 외에 지금 즉시 주문을 다시 계산합니다.">수동 주문 실행</button></div>
  <p class="muted">수동 주문은 자동매매와 별개로 지금 즉시 계산됩니다. 종목당 현재 목표 비중은 총자산의 10%이며 정수 수량만 주문합니다.</p>
  <p class="muted" id="trader-message" aria-live="polite">계좌 정보를 불러오는 중입니다.</p>
</section></div></details>
<section class="trader-controls" id="remote-connection" hidden>
  <h2>로컬 DB 실시간 연결</h2>
  <div class="trader-form"><label for="remote-api-url">HTTPS API 주소</label><input id="remote-api-url" type="url" placeholder="https://stock-api.example.com"><label for="remote-api-token">접속 토큰</label><input id="remote-api-token" type="password" autocomplete="current-password"><button id="remote-connect-button" type="button">읽기 전용 연결</button></div>
  <p class="muted">주소는 이 브라우저에, 토큰은 현재 탭에만 저장됩니다. 원격에서는 입금과 수동주문을 실행할 수 없습니다.</p>
</section>
<section><h2>가상계좌 보유종목</h2><table><thead><tr><th>종목명</th><th>매도 감시상태</th><th class="num">투자비중</th><th class="num">보유일수</th><th class="num">진입가</th><th class="num">현재가</th><th class="num">평가손익</th><th class="num">수익률</th><th>다음 매도 기준</th></tr></thead><tbody id="trader-holdings"></tbody></table></section>
<section class="sales-history"><h2>매도 내역</h2><p class="muted">부분매도와 전량매도를 포함한 가상계좌 실현 결과입니다.</p>
  <div class="sale-summary-grid">
    <div><span>누적 실현손익</span><b id="sale-realized-profit">0원</b></div>
    <div><span>매도 건수</span><b id="sale-count">0건</b></div>
    <div><span>매도 승률</span><b id="sale-win-rate">0.00%</b></div>
    <div><span>평균 실현수익률</span><b id="sale-return">0.00%</b></div>
  </div>
  <div class="table-scroll"><table><thead><tr><th>종목명</th><th>매수일</th><th>매도일</th><th>구분</th><th class="num">평균 매수가</th><th class="num">매도가</th><th class="num">수량</th><th class="num">실현손익</th><th class="num">실현수익률</th><th>매도 사유</th></tr></thead><tbody id="trader-sales"></tbody></table></div>
  <div class="pager" id="trader-sales-pager" data-page-size="15"></div>
</section>
"""
    system_tab = f"""
<div class="home-heading"><div><h2>시스템 관리</h2><p class="muted">문제가 있을 때만 확인하면 되는 운영 정보입니다.</p></div><span class="system-pill {'bad' if issue_count else 'ok'}">{'경고 ' + str(issue_count) + '건' if issue_count else '모든 작업 정상'}</span></div>
<section class="learning-status"><h2>데이터 학습 준비</h2><div class="progress-heading"><b>20일 성과 표본 {progress['current']} / {progress['target']}</b><span>{progress['percent']}%</span></div><div class="progress-track"><span style="width:{progress['percent']}%"></span></div><p class="muted">최소 300개가 쌓이면 강화된 검증 절차를 통해 가중치 승격 여부를 판단합니다.</p></section>
{table("데이터 축적 현황", data_accumulation_rows(), ["metric", "value", "status"])}
{table("Issues", issue_rows(), ["source", "item", "status"])}
{user_table("Today run details", user_run_rows(), ["step", "status"], "오늘 사용자 확인이 필요한 자동 작업은 없습니다.")}
{details("데이터 품질과 발송 상태", table("Price quality", recent_price_quality(30), ["created_at", "ticker", "status", "reason"]) + table("Recent deliveries", tail_csv("logs/deliveries.csv", 10), ["created_at", "channel", "status", "error"]))}
{details("알고리즘 검증 결과", table("전략별 성과", benchmark_summary_rows(), ["strategy", "total_return_pct", "mdd_pct", "sharpe"]) + table("매도 사유별 결과", sell_quality_rows(), ["sell_reason_group", "count", "avg_realized_return_pct", "rebound_5d_rate_pct", "assessment"]))}
{details("고급 운영 정보", table("Candidate rejection summary", rejection_summary(), ["reason", "count"]) + table("Recent position checks", recent_position_checks(), ["checked_at", "name", "return_pct", "decision", "reasons"]) + table("Current settings", settings_rows(), ["setting", "value"]) + table("Recommendation shape", recommendation_shape_rows(), ["type", "when", "action"]) + f'<section><h2>{e(display_label("Daily check"))}</h2><ul>{checks}</ul></section><section><h2>{e(display_label("Recent task log"))}</h2><ul>{task_log}</ul></section><section><h2>{e(display_label("Recent task errors"))}</h2><ul>{task_error_items}</ul></section>')}
"""
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{e(display_label("stockAlarm Dashboard"))}</title>
<style>
*{{box-sizing:border-box}} html{{overflow-x:hidden}} body{{font-family:Segoe UI,Malgun Gothic,sans-serif;margin:24px;background:#f6f7f9;color:#111;line-height:1.5;overflow-x:hidden}} .dashboard-header,.tabs{{max-width:1600px;margin-left:auto;margin-right:auto}}
.dashboard-header h1{{margin:0}} .dashboard-meta{{margin-top:8px;color:#666}} h2{{line-height:1.3}} .muted{{color:#666;overflow-wrap:anywhere}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin:22px 0}}
.card{{min-width:0;background:white;border-radius:12px;padding:16px;box-shadow:0 1px 4px #ddd}} .card span{{display:block;font-size:24px;margin-top:8px;overflow-wrap:anywhere}}
.home-heading{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin:22px 0 10px}} .home-heading h2{{margin:0 0 4px;font-size:24px}} .home-heading p{{margin:0}} .system-pill{{padding:8px 12px;border-radius:999px;background:white;border:1px solid #d0d5dd;white-space:nowrap}} .system-pill.ok{{background:#ecfdf3;border-color:#abefc6}} .system-pill.bad{{background:#fef3f2;border-color:#fecdca}}
.home-cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:20px 0 24px}} .summary-card{{min-width:0;background:white;border:1px solid #e5e7eb;border-radius:14px;padding:20px;box-shadow:0 1px 4px #ddd}} .summary-card.primary{{background:#111827;color:white}} .summary-card b,.summary-card strong,.summary-card small{{display:block}} .summary-card b{{color:#64748b}} .summary-card.primary b,.summary-card.primary small{{color:#cbd5e1}} .summary-card strong{{font-size:clamp(22px,2vw,28px);margin:10px 0;overflow-wrap:anywhere}} .summary-card small{{color:#64748b}}
.operation-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:#e2e8f0;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden}} .operation-grid>div{{background:#fff;padding:16px 18px;min-width:0}} .operation-grid span,.operation-grid b{{display:block}} .operation-grid span{{font-size:13px;color:#64748b}} .operation-grid b{{margin-top:6px;font-size:17px;overflow-wrap:anywhere}} .empty-state{{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:22px;border:1px dashed #cbd5e1;border-radius:10px;background:#f8fafc}} .empty-state b{{color:#334155}} .empty-state span{{color:#64748b}} .progress-heading{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}} .progress-track{{height:12px;background:#e2e8f0;border-radius:999px;overflow:hidden}} .progress-track span{{display:block;height:100%;background:#2563eb;border-radius:inherit}} .learning-status p{{margin-bottom:0}}
.tracking-summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:20px 0}} .tracking-card{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:17px 18px;box-shadow:0 1px 4px #ddd}} .tracking-card span,.tracking-card b{{display:block}} .tracking-card span{{color:#64748b;font-size:13px}} .tracking-card b{{font-size:22px;margin-top:7px}}
.highlight-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:16px 0}}
.highlight{{background:white;color:#111827;border-radius:14px;padding:16px;box-shadow:0 1px 4px #ddd;border:1px solid #e5e7eb}} .highlight b{{display:block;color:#475569}} .highlight span{{display:block;color:#111827;font-size:24px;font-weight:800;margin-top:8px}}
.tabs{{margin-top:20px}} .tab-input{{display:none}} .tab-labels{{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:18px}} .tab-label{{display:inline-flex;align-items:center;background:#e9edf3;border-radius:999px;padding:10px 16px;cursor:pointer;font-weight:600}}
.tab-panel{{display:none}} #tab-stocks:checked~.tab-labels label[for="tab-stocks"],#tab-tracking:checked~.tab-labels label[for="tab-tracking"],#tab-trader:checked~.tab-labels label[for="tab-trader"],#tab-system:checked~.tab-labels label[for="tab-system"]{{background:#111;color:white}}
#tab-stocks:checked~#stocks-panel,#tab-tracking:checked~#tracking-panel,#tab-trader:checked~#trader-panel,#tab-system:checked~#system-panel{{display:block}}
.legacy-sections,.legacy-order{{display:none}}
section{{min-width:0;background:white;border-radius:12px;padding:20px;margin:20px 0;box-shadow:0 1px 4px #ddd;overflow-x:auto;overflow-y:hidden}} section h2{{margin:0 0 16px}} 
details{{min-width:0;background:#eef2f6;border-radius:12px;margin:20px 0}} details summary{{cursor:pointer;padding:16px 18px;font-weight:700}} .details-body{{padding:0 18px 2px}} .details-body section{{box-shadow:none;border:1px solid #e5e7eb}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{border-bottom:1px solid #eee;text-align:left;padding:10px 12px;white-space:nowrap}} th{{background:#fafafa;position:sticky;top:0}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
.ok{{color:#147a2e;font-weight:600}} .warn{{color:#9a6700;font-weight:600}} .bad{{color:#b42318;font-weight:600}} .pos{{color:#047857;font-weight:700}} .neg{{color:#dc2626;font-weight:700}} .zero{{color:#64748b;font-weight:600}}
.pager{{display:flex;gap:6px;align-items:center;justify-content:center;margin-top:10px}} .pager button{{border:1px solid #d0d5dd;background:white;border-radius:8px;padding:6px 10px;cursor:pointer}} .pager button.active{{background:#111;color:white;border-color:#111}}
.trader-account-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:20px 0}} .trader-balance{{min-width:0;background:white;color:#111827;border:1px solid #e5e7eb;border-radius:14px;padding:20px;box-shadow:0 1px 4px #ddd}} .trader-balance.primary{{background:#111827;color:white}} .trader-balance span{{display:block;color:#64748b}} .trader-balance.primary span{{color:#cbd5e1}} .trader-balance strong{{display:block;font-size:clamp(21px,2vw,28px);margin-top:8px;overflow-wrap:anywhere}} .trader-status{{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;background:#eef6ff;border:1px solid #bfdbfe;border-radius:10px;padding:14px 16px;margin:18px 0}} .trader-form{{display:flex;gap:10px 12px;align-items:center;flex-wrap:wrap}} .trader-form label{{font-weight:600}} .trader-form input{{min-width:0;width:min(100%,320px);padding:10px;border:1px solid #d0d5dd;border-radius:8px}} .trader-form button{{padding:10px 14px;border:0;border-radius:8px;background:#111;color:white;cursor:pointer}} .trader-form button:disabled{{opacity:.4;cursor:not-allowed}}
.trader-breakdown{{display:flex;gap:12px 24px;justify-content:flex-end;flex-wrap:wrap;margin:0 2px 18px;color:#475569}}
.trader-risk-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 16px}} .trader-risk-grid>div{{min-width:0;background:#fff;border:1px solid #dbe3ec;border-radius:12px;padding:14px 16px}} .trader-risk-grid span,.trader-risk-grid b{{display:block}} .trader-risk-grid b>span{{display:inline;color:inherit;font-size:inherit}} .trader-risk-grid span{{color:#64748b;font-size:13px}} .trader-risk-grid b{{margin-top:5px;overflow-wrap:anywhere}} .order-status{{overflow:visible}} .order-status>.trader-status{{margin:0;background:#f8fafc;border-color:#e2e8f0}} .account-actions{{background:#fff;border:1px solid #e2e8f0;box-shadow:0 1px 4px #ddd}} .account-actions summary{{font-size:18px}} .account-actions .trader-controls{{margin-top:0}}
.sale-summary-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0 20px}} .sale-summary-grid>div{{min-width:0;background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:14px 16px}} .sale-summary-grid span,.sale-summary-grid b{{display:block}} .sale-summary-grid span{{color:#64748b;font-size:13px}} .sale-summary-grid b{{font-size:20px;margin-top:6px;overflow-wrap:anywhere}} .table-scroll{{overflow-x:auto}}
.trader-chart-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:20px 0}} .trader-chart-grid .donut-card{{margin:0;overflow:visible}} .donut-layout{{display:grid;grid-template-columns:minmax(190px,240px) minmax(0,1fr);align-items:center;gap:24px}} .donut-ring{{width:220px;aspect-ratio:1;border-radius:50%;display:grid;place-items:center;background:#e2e8f0;margin:auto;transition:background .2s ease}} .donut-hole{{width:58%;aspect-ratio:1;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;background:#fff;box-shadow:0 0 0 1px rgba(226,232,240,.75)}} .donut-hole span{{font-size:13px;color:#64748b}} .donut-hole b{{font-size:18px;margin-top:5px;max-width:110px;overflow-wrap:anywhere}} .donut-legend{{display:grid;gap:10px;min-width:0}} .donut-legend-row{{display:grid;grid-template-columns:12px minmax(0,1fr) auto;align-items:center;gap:9px;font-size:14px}} .donut-swatch{{width:12px;height:12px;border-radius:4px}} .donut-label{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}} .donut-value{{font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}} .donut-empty{{color:#64748b}}
button:focus-visible,input:focus-visible,.tab-label:focus-visible{{outline:3px solid #2563eb;outline-offset:2px}}
li{{margin:4px 0}}
@media(max-width:1100px){{.donut-layout{{grid-template-columns:1fr}}}}
@media(max-width:1000px){{.home-cards,.trader-account-grid,.trader-risk-grid,.sale-summary-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media(max-width:800px){{body{{margin:14px}} .tab-label{{padding:9px 12px}} .home-heading{{align-items:flex-start}} section{{padding:16px}} th,td{{padding:9px 10px}}}}
@media(max-width:480px){{.home-cards,.trader-account-grid,.trader-risk-grid,.sale-summary-grid{{grid-template-columns:1fr}} .home-heading{{display:block}} .system-pill{{display:inline-block;margin-top:10px}} .summary-card strong{{font-size:24px}} .trader-breakdown{{justify-content:flex-start;flex-direction:column;gap:6px}} .trader-form>*{{width:100%}}}}
</style>
</head>
<body>
<header class="dashboard-header">
<h1>{e(display_label("stockAlarm Dashboard"))}</h1>
<div class="dashboard-meta">{e(display_label("generated"))} {e(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</div>
</header>
<div class="legacy-order">{e(display_label("Issues"))} {e(display_label("Today run details"))} {e(display_label("Today recommendations"))} {e(display_label("Recommendation shape"))} {e(display_label("Score breakdown"))} {e(display_label("Why recommended"))} {e(display_label("Sell alert summary"))} {e(display_label("Recent sell alerts"))} {e(display_label("Recommendation stats"))}</div>
<div class="tabs">
<input class="tab-input" id="tab-stocks" name="tabs" type="radio" checked>
<input class="tab-input" id="tab-tracking" name="tabs" type="radio">
<input class="tab-input" id="tab-trader" name="tabs" type="radio">
<input class="tab-input" id="tab-system" name="tabs" type="radio">
<div class="tab-labels" role="tablist" aria-label="대시보드 화면">
<label class="tab-label" for="tab-stocks" role="tab" tabindex="0">홈</label>
<label class="tab-label" for="tab-tracking" role="tab" tabindex="0">추천 추적</label>
<label class="tab-label" for="tab-trader" role="tab" tabindex="0">가상 트레이더</label>
<label class="tab-label" for="tab-system" role="tab" tabindex="0">시스템 관리</label>
</div>
<div class="tab-panel" id="stocks-panel" role="tabpanel">{stock_tab}</div>
<div class="tab-panel" id="tracking-panel" role="tabpanel">{tracking_tab}</div>
<div class="tab-panel" id="trader-panel" role="tabpanel">{trader_tab}</div>
<div class="tab-panel" id="system-panel" role="tabpanel">{system_tab}</div>
</div>
<script>
const traderCandidates = {trader_candidates};
const traderKey = "stockAlarm.virtualTrader.v1";
const remoteMode = !(["file:","http:"].includes(location.protocol) && ["","127.0.0.1","localhost"].includes(location.hostname));
const requestedRemoteApi = remoteMode ? (new URLSearchParams(location.search).get("api") || "") : "";
if(requestedRemoteApi.startsWith("https://")) localStorage.setItem("stockAlarm.remoteApiUrl", requestedRemoteApi.endsWith("/") ? requestedRemoteApi.slice(0,-1) : requestedRemoteApi);
let traderApiBase = remoteMode ? (localStorage.getItem("stockAlarm.remoteApiUrl") || "") : (location.protocol === "file:" ? "http://127.0.0.1:8765" : "");
let remoteToken = remoteMode ? (sessionStorage.getItem("stockAlarm.remoteToken") || "") : "";
let trader = {{cash:0, holdings:[]}};
const won = value => `${{Math.round(value).toLocaleString("ko-KR")}}원`;
const tabLabels=[...document.querySelectorAll(".tab-label")];
function syncTabs() {{ tabLabels.forEach(label=>label.setAttribute("aria-selected",document.getElementById(label.htmlFor).checked?"true":"false")); }}
tabLabels.forEach((label,index)=>{{label.addEventListener("keydown",event=>{{if(event.key==="Enter"||event.key===" "){{event.preventDefault();label.click();}}if(event.key==="ArrowRight"||event.key==="ArrowLeft"){{event.preventDefault();const next=(index+(event.key==="ArrowRight"?1:-1)+tabLabels.length)%tabLabels.length;tabLabels[next].focus();tabLabels[next].click();}}}});label.addEventListener("click",()=>setTimeout(syncTabs));}}); syncTabs();
async function traderRequest(path, options={{}}) {{
  if(remoteMode && !traderApiBase) throw new Error("원격 HTTPS API 주소를 입력해 주세요.");
  const headers={{"Content-Type":"application/json", ...(options.headers||{{}})}};
  if(remoteMode && remoteToken) headers.Authorization=`Bearer ${{remoteToken}}`;
  const response = await fetch(`${{traderApiBase}}${{path}}`, {{...options, headers}});
  const body = await response.json();
  if(!response.ok) throw new Error(body.error || "요청을 처리하지 못했습니다.");
  return body;
}}
function renderSales() {{
  const summary=trader.sale_summary || {{}};
  const profit=document.getElementById("sale-realized-profit"); profit.textContent=won(summary.realized_profit_loss||0); profit.className=summary.realized_profit_loss>0?"pos":summary.realized_profit_loss<0?"neg":"zero";
  document.getElementById("sale-count").textContent=`${{summary.count||0}}건`;
  const winRate=document.getElementById("sale-win-rate"); winRate.textContent=`${{Number(summary.win_rate_pct||0).toFixed(2)}}%`;
  const saleReturn=document.getElementById("sale-return"); saleReturn.textContent=`${{Number(summary.return_pct||0).toFixed(2)}}%`; saleReturn.className=summary.return_pct>0?"pos":summary.return_pct<0?"neg":"zero";
  const body=document.getElementById("trader-sales"), pager=document.getElementById("trader-sales-pager"), sales=trader.sales||[], pageSize=15;
  let page=0;
  const draw=()=>{{
    body.textContent="";
    sales.slice(page*pageSize,(page+1)*pageSize).forEach(item=>{{
      const row=document.createElement("tr"), average=item.quantity?item.cost_basis/item.quantity:0;
      const values=[item.name,item.entry_at?item.entry_at.slice(0,10):"-",item.created_at?item.created_at.slice(0,10):"-",item.sale_label,won(average),won(item.price),Number(item.quantity||0).toLocaleString("ko-KR"),won(item.realized_profit_loss),`${{Number(item.return_pct||0).toFixed(2)}}%`,item.reason||"-"];
      values.forEach((value,index)=>{{const cell=document.createElement("td");cell.textContent=value;if(index>=4&&index<=8)cell.className="num";if(index===7||index===8)cell.className+=Number(item.realized_profit_loss)>0?" pos":Number(item.realized_profit_loss)<0?" neg":" zero";row.appendChild(cell);}}); body.appendChild(row);
    }});
    if(!sales.length) body.innerHTML='<tr><td colspan="10" class="muted">아직 매도 내역이 없습니다.</td></tr>';
    pager.textContent=""; const pages=Math.ceil(sales.length/pageSize); if(pages<=1)return;
    const add=(label,target,active=false)=>{{const button=document.createElement("button");button.type="button";button.textContent=label;button.className=active?"active":"";button.onclick=()=>{{page=Math.max(0,Math.min(target,pages-1));draw();}};pager.appendChild(button);}};
    add("‹",page-1); const block=Math.floor(page/5)*5; for(let index=block;index<Math.min(block+5,pages);index++)add(String(index+1),index,index===page); add("›",page+1);
  }};
  draw();
}}
const donutColors=["#2563eb","#0f766e","#d97706","#7c3aed","#dc2626","#64748b"];
function renderDonut(ringId,legendId,items,emptyText) {{
  const ring=document.getElementById(ringId), legend=document.getElementById(legendId);
  const clean=items.filter(item=>Number(item.value)>0), total=clean.reduce((sum,item)=>sum+Number(item.value),0);
  legend.textContent="";
  if(!(total>0)) {{ ring.style.background="#e2e8f0"; ring.setAttribute("aria-label",emptyText); const empty=document.createElement("div");empty.className="donut-empty";empty.textContent=emptyText;legend.appendChild(empty);return; }}
  let cursor=0; const stops=[]; const labels=[];
  clean.forEach((item,index)=>{{
    const color=item.color||donutColors[index%donutColors.length], pct=Number(item.value)/total*100, end=cursor+pct;
    stops.push(`${{color}} ${{cursor.toFixed(3)}}% ${{end.toFixed(3)}}%`); cursor=end;
    const row=document.createElement("div");row.className="donut-legend-row";
    const swatch=document.createElement("span");swatch.className="donut-swatch";swatch.style.background=color;
    const label=document.createElement("span");label.className="donut-label";label.textContent=item.label;
    const value=document.createElement("span");value.className="donut-value";value.textContent=`${{won(item.value)}} · ${{pct.toFixed(1)}}%`;
    row.append(swatch,label,value);legend.appendChild(row);labels.push(`${{item.label}} ${{pct.toFixed(1)}}%`);
  }});
  ring.style.background=`conic-gradient(${{stops.join(",")}})`; ring.setAttribute("aria-label",labels.join(", "));
}}
function renderPortfolioCharts() {{
  const cash=Number(trader.cash||0), holdingsValue=Number(trader.holdings_value||0);
  document.getElementById("asset-donut-total").textContent=won(cash+holdingsValue);
  renderDonut("asset-donut","asset-donut-legend",[
    {{label:"주문 가능 현금",value:cash,color:"#2563eb"}},
    {{label:"보유주식 평가액",value:holdingsValue,color:"#0f766e"}},
  ],"입금 또는 보유자산이 없습니다.");
  const grouped={{}};
  (trader.holdings||[]).forEach(item=>{{const sector=item.sector||"미분류";grouped[sector]=(grouped[sector]||0)+Number(item.valuation||0);}});
  const sectors=Object.entries(grouped).map(([label,value])=>({{label,value}})).sort((a,b)=>b.value-a.value);
  const shown=sectors.slice(0,5); if(sectors.length>5)shown.push({{label:"기타",value:sectors.slice(5).reduce((sum,item)=>sum+item.value,0)}});
  document.getElementById("sector-donut-count").textContent=`${{sectors.length}}개`;
  renderDonut("sector-donut","sector-donut-legend",shown,"보유종목이 없습니다.");
}}
function renderTrader(message="") {{
  document.getElementById("trader-total-equity").textContent = won(trader.total_equity || trader.cash || 0);
  document.getElementById("trader-cash").textContent = won(trader.cash || 0);
  document.getElementById("trader-holdings-value").textContent = won(trader.holdings_value || 0);
  const holdingsReturn=document.getElementById("trader-holdings-return"); holdingsReturn.textContent=`${{Number(trader.holdings_return_pct||0).toFixed(2)}}%`; holdingsReturn.className=trader.holdings_return_pct>0?"pos":trader.holdings_return_pct<0?"neg":"zero";
  const totalReturn=document.getElementById("trader-total-return"); totalReturn.textContent=`${{Number(trader.total_return_pct||0).toFixed(2)}}%`; totalReturn.className=trader.total_return_pct>0?"pos":trader.total_return_pct<0?"neg":"zero";
  const totalProfit=document.getElementById("trader-total-profit"); totalProfit.textContent=won(trader.total_profit_loss || 0); totalProfit.className=(trader.total_profit_loss>0?"pos":trader.total_profit_loss<0?"neg":"zero");
  document.getElementById("home-total-equity").textContent=won(trader.total_equity||trader.cash||0);
  const homeReturn=document.getElementById("home-total-return"); homeReturn.textContent=`${{Number(trader.total_return_pct||0).toFixed(2)}}%`; homeReturn.className=trader.total_return_pct>0?"pos":trader.total_return_pct<0?"neg":"zero";
  document.getElementById("home-total-profit").textContent=`총손익 ${{won(trader.total_profit_loss||0)}}`;
  renderPortfolioCharts();
  document.getElementById("home-orders").textContent=`매수 ${{trader.today_buys||0}} · 매도 ${{trader.today_sells||0}}`;
  document.getElementById("trader-auto-status").textContent = trader.auto_trading ? `자동매매 켜짐 · ${{trader.schedule || ""}}` : "자동매매 꺼짐";
  document.getElementById("home-auto-status").textContent = trader.auto_trading ? "정상 작동" : "중단";
  document.getElementById("trader-strategy").textContent = trader.strategy_version || "기본 전략";
  const risk=trader.risk || {{}};
  const riskLabels={{active:"정상 운영",reduced:"축소 운영",halted:"신규매수 중단"}};
  document.getElementById("trader-risk").textContent = risk.status === "halted" ? `신규매수 중단 · ${{risk.reason || "위험 한도"}}` : (riskLabels[risk.status] || risk.status || "초기화 전");
  document.getElementById("trader-market-mode").textContent = trader.market_mode ? `${{trader.market_mode}}${{trader.market_up_ratio_pct == null ? "" : ` · 상승 ${{Number(trader.market_up_ratio_pct).toFixed(1)}}%`}}` : "데이터 대기";
  document.getElementById("home-market-mode").textContent = trader.market_mode ? `${{trader.market_mode}}${{trader.market_exposure_limit_pct == null ? "" : ` · 한도 ${{Number(trader.market_exposure_limit_pct).toFixed(0)}}%`}}` : "데이터 대기";
  document.getElementById("trader-market-limit").textContent = trader.market_exposure_limit_pct == null ? "데이터 대기" : `${{Number(trader.market_exposure_limit_pct).toFixed(0)}}%`;
  document.getElementById("trader-exposure").textContent = `${{Number(risk.exposure_pct || 0).toFixed(2)}}%`;
  document.getElementById("trader-daily-return").textContent = `${{Number(risk.daily_return_pct || 0).toFixed(2)}}%`;
  document.getElementById("trader-weekly-return").textContent = `${{Number(risk.weekly_return_pct || 0).toFixed(2)}}%`;
  document.getElementById("trader-drawdown").textContent = `${{Number(risk.drawdown_pct || 0).toFixed(2)}}%`;
  const dailyProfit=Number(risk.equity||trader.total_equity||0)-Number(risk.daily_start_equity||risk.equity||trader.total_equity||0); const homeDaily=document.getElementById("home-daily-profit"); homeDaily.textContent=`${{dailyProfit>=0?"수익 ":"손실 "}}${{won(Math.abs(dailyProfit))}} (${{Number(risk.daily_return_pct||0).toFixed(2)}}%)`; homeDaily.className=dailyProfit>0?"pos":dailyProfit<0?"neg":"zero";
  document.getElementById("home-price-updated").textContent=(trader.price_updated_at||"확인 중").replace("T"," ");
  const unavailable=trader.price_unavailable_tickers || [];
  document.getElementById("trader-price-status").textContent = unavailable.length ? `가격 확인 불가: ${{unavailable.join(", ")}}` : `${{trader.price_source || ""}} · ${{trader.price_updated_at || ""}}`;
  const body = document.getElementById("trader-holdings"); body.textContent = "";
  (trader.holdings || []).forEach(item => {{
    const row = document.createElement("tr");
    const sellReference=`손절 ${{won(item.stop_price||0)}}${{item.ma20?` · 20일선 ${{won(item.ma20)}}`:""}}`;
    const values=[item.name,item.watch_state||'데이터 대기',`${{Number(item.allocation_pct||0).toFixed(2)}}%`,item.holding_days==null?'확인 중':`${{item.holding_days}}일`,won(item.average_price),won(item.current_price),won(item.profit_loss),`${{Number(item.return_pct).toFixed(2)}}%`,sellReference];
    values.forEach((value,index)=>{{const cell=document.createElement("td");cell.textContent=value;if(index>=2&&index<=7)cell.className="num";if(index===6||index===7)cell.className+=Number(item.profit_loss)>0?" pos":Number(item.profit_loss)<0?" neg":" zero";row.appendChild(cell);}}); body.appendChild(row);
  }});
  if (!body.children.length) body.innerHTML='<tr><td colspan="9" class="muted">가상계좌 보유종목이 없습니다.</td></tr>';
  renderSales();
  document.getElementById("buy-button").disabled = remoteMode || !(trader.cash > 0 && traderCandidates.length) || risk.status === "halted";
  if(message) document.getElementById("trader-message").textContent=message;
}}
document.getElementById("deposit-button").addEventListener("click", async () => {{ const input=document.getElementById("deposit-amount"), amount=Math.floor(Number(input.value)); if(!(amount>0)) return renderTrader("1원 이상의 입금금액을 입력해 주세요."); try {{ trader=await traderRequest("/api/trader/deposit",{{method:"POST",body:JSON.stringify({{amount}})}}); input.value=""; renderTrader(`${{won(amount)}}을 DB 계좌에 입금했습니다.`); }} catch(error) {{ renderTrader(error.message); }} }});
document.getElementById("buy-button").addEventListener("click", async () => {{
  try {{ trader=await traderRequest("/api/trader/buy",{{method:"POST",body:"{{}}"}}); renderTrader(`${{trader.bought}}개 종목을 ${{won(trader.spent)}}에 가상 매수했습니다.`); }} catch(error) {{ renderTrader(error.message); }}
}});
const remoteConnection=document.getElementById("remote-connection");
if(remoteMode) {{
  remoteConnection.hidden=false;
  document.getElementById("deposit-button").disabled=true;
  document.getElementById("buy-button").disabled=true;
  document.getElementById("remote-api-url").value=traderApiBase;
  document.getElementById("remote-api-token").value=remoteToken;
  document.getElementById("remote-connect-button").addEventListener("click", async () => {{
    const enteredUrl=document.getElementById("remote-api-url").value.trim();
    const url=enteredUrl.endsWith("/") ? enteredUrl.slice(0,-1) : enteredUrl;
    const token=document.getElementById("remote-api-token").value.trim();
    if(!url.startsWith("https://") || !token) return renderTrader("HTTPS API 주소와 접속 토큰을 입력해 주세요.");
    traderApiBase=url; remoteToken=token;
    localStorage.setItem("stockAlarm.remoteApiUrl",url); sessionStorage.setItem("stockAlarm.remoteToken",token);
    try {{ trader=await traderRequest("/api/trader"); renderTrader("로컬 DB에 읽기 전용으로 연결했습니다."); }} catch(error) {{ renderTrader(error.message); }}
  }});
}}
const legacyTrader = localStorage.getItem(traderKey);
(remoteMode ? traderRequest("/api/trader") : (legacyTrader ? traderRequest("/api/trader/import",{{method:"POST",body:legacyTrader}}) : traderRequest("/api/trader")))
  .then(state => {{trader=state; if(state.imported) localStorage.removeItem(traderKey); renderTrader(state.imported?"기존 브라우저 가상 계좌를 DB로 이전했습니다.":"계좌와 최신 평가 정보를 불러왔습니다.");}})
  .catch(error => renderTrader(remoteMode ? (error.message || "원격 API 연결 정보를 입력해 주세요.") : "open_dashboard.bat으로 열어야 DB 가상 계좌를 사용할 수 있습니다."));
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
