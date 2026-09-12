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
    latest_portfolio_risk, latest_profile_selections, recent_position_checks, recent_price_quality, recent_runs,
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
    "completed",
    "total",
    "percent",
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
    "average_price",
    "selected",
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
    "rank",
    "profile_score",
    "profitability_score",
    "growth_score",
    "stability_score",
    "dividend_score",
    "momentum_score",
    "news_category_score",
    "order_quantity",
    "filled_price",
    "filled_amount",
    "commission_amount",
}
RETURN_COLUMNS = {"return_pct", "avg_1d_return_pct", "return_1d_pct", "sell_return_pct", "return_3d_pct", "return_5d_pct", "return_10d_pct", "return_20d_pct", "avg_realized_return_pct", "total_return_pct", "mdd_pct", "sell_alert_return_pct"}
TIMESTAMP_COLUMNS = {"created_at", "started_at", "finished_at", "evaluated_at", "checked_at", "alert_created_at", "ordered_at"}
BOOLEAN_COLUMNS = {"passed", "selected", "legacy_passed", "time_stop_triggered"}
# Enum-valued columns whose Korean label depends on which table they're in --
# e.g. "SELL" means "매도 검토" for a candidate decision but plain "매도" for
# an order side, so these can't live in the global DISPLAY_VALUES dict.
COLUMN_VALUE_LABELS = {
    "order_side": {"BUY": "매수", "SELL": "매도"},
    "order_type": {"LIMIT": "지정가", "MARKET": "시장가"},
    "order_status": {"OPEN": "미체결", "FILLED": "체결완료", "PARTIALLY_FILLED": "부분체결", "CANCELED": "취소", "REJECTED": "거부", "EXPIRED": "만료"},
}
STATUS_PILL_CLASSES = {"추적 중": "pill-accent", "매도 알림": "pill-danger", "성과 완료": "pill-neutral", "성과 수집 중": "pill-neutral"}
# The raw tracking_status still drives filtering/summary counts elsewhere
# (data_accumulation_rows) -- this only simplifies what the badge itself
# says, keeping the detailed status one hover away via the title attribute.
TRACKING_STATUS_DISPLAY = {"추적 중": "진행 중", "성과 수집 중": "진행 중", "매도 알림": "완료 (매도)", "성과 완료": "완료 (기간만료)"}
# watch_state carries a ticker-specific suffix (e.g. "종목 경고: LIQUIDATION_TRADING"),
# so it can't be matched as a whole string like STATUS_PILL_CLASSES -- match by prefix instead.
WATCH_STATE_PILL_PREFIXES = [
    ("종목 경고", "pill-danger"), ("매도조건 충족", "pill-danger"),
    ("종목 주의", "pill-warn"), ("손절선 근접", "pill-warn"), ("20일선 주의", "pill-warn"),
    ("1차 익절 완료", "pill-accent"), ("정상 보유", "pill-neutral"),
]


def watch_state_pill_class(value: str) -> str:
    for prefix, klass in WATCH_STATE_PILL_PREFIXES:
        if value.startswith(prefix):
            return klass
    return ""
LABELS = {
    "stockAlarm Dashboard": "국내주식 알림 대시보드",
    "실제 계좌": "실제 계좌",
    "generated": "생성 시각",
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
    "watch_state": "관찰 상태",
    "metric": "지표",
    "value": "값",
    "horizon": "경과 기간",
    "completed": "완료 표본",
    "total": "전체 표본",
    "percent": "진행률(%)",
    "setting": "설정",
    "channel": "채널",
    "message_id": "메시지 ID",
    "chat_id_suffix": "채팅 식별자 끝자리",
    "positions_report": "보유 종목 보고",
    "error": "전송 오류",
    "picks": "추천수",
    "avg_1d_return_pct": "1일 평균 수익률",
    "win_rate_1d_pct": "1일 승률",
    "entry_close": "진입 종가",
    "return_1d_pct": "1일 수익률",
    "sell_return_pct": "매도검토 수익률",
    "sell_reason": "매도 알림 사유",
    "pick_date": "추천일",
    "return_3d_pct": "3일 수익률",
    "return_5d_pct": "5일 수익률",
    "entry_price": "진입가",
    "average_price": "평균매수가",
    "account_type": "계좌 유형",
    "ordered_at": "주문 시각",
    "order_side": "매매구분",
    "order_type": "주문유형",
    "order_status": "주문상태",
    "order_quantity": "주문수량",
    "filled_price": "체결가",
    "filled_amount": "체결금액",
    "commission_amount": "수수료",
    "commission_rate": "수수료율",
    "start_date": "적용 시작일",
    "end_date": "적용 종료일",
    "Candidate rejection summary": "후보 탈락 사유 요약",
    "Recent position checks": "최근 전체 보유종목 판단",
    "started_at": "시작시각",
    "finished_at": "완료시각",
    "evaluated_at": "평가시각",
    "checked_at": "점검시각",
    "passed": "기술조건 통과",
    "selected": "최종선정",
    "rank": "순위",
    "profile": "성향",
    "profile_score": "성향점수",
    "profitability_score": "수익성",
    "growth_score": "성장성",
    "stability_score": "안정성",
    "dividend_score": "배당성",
    "momentum_score": "모멘텀",
    "news_category_score": "뉴스",
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
    "alert_created_at": "매도 판단 시각",
    "return_10d_pct": "10일 수익률",
    "raw_volume_ratio": "원시 거래량 배율",
    "atr20_pct": "20일 변동성",
    "relative_strength_score": "시장대비 점수",
    "legacy_score": "구전략 점수",
    "legacy_passed": "구전략 통과",
    "final_score": "최종 점수",
    "time_stop_triggered": "기간 청산 조건",
    "allocation_pct": "추천 비중(%)",
    "virtual_target_pct": "가상주문 목표 비중(%)",
    "allocation_rule": "비중 규칙",
    "quantity": "보유수량",
    "valuation": "평가금액",
    "profit_loss": "평가손익",
    "notification_status": "알림 상태",
    "aggressive_order_status": "적극투자형",
    "neutral_order_status": "위험중립형",
    "aggressive_status": "적극투자형",
    "neutral_status": "위험중립형",
    "Price quality": "가격 데이터 품질",
    "sell_reason_group": "매도 사유",
    "avg_realized_return_pct": "평균 매도수익률",
    "rebound_5d_rate_pct": "5일 내 반등 비율",
    "assessment": "판정",
    "strategy": "전략",
    "total_return_pct": "총수익률",
    "mdd_pct": "최대낙폭",
    "sharpe": "샤프지수",
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
    "stale": "지연됨",
    "quarantined": "격리됨",
    "missing_price": "가격 없음",
    "invalid_ohlc": "OHLC 이상값",
    "unparseable_price": "가격 형식 오류",
    "close_source_mismatch": "종가 불일치",
    "LIQUIDATION_TRADING": "정리매매",
    "OVERHEATED": "단기과열",
    "INVESTMENT_WARNING": "투자경고",
    "INVESTMENT_RISK": "투자위험",
    "VI_STATIC": "정적VI",
    "VI_DYNAMIC": "동적VI",
    "VI_STATIC_AND_DYNAMIC": "정적+동적VI",
    "STOCK_WARRANTS": "신주인수권",
    "reference_unavailable": "비교가격 확인 불가",
    "notifier returned false": "알림 전송 실패",
    "stock_alarm": "stockAlarm",
    "BROKERAGE": "종합매매",
    "OVERSEAS_DERIVATIVES": "해외파생",
    "PENSION_SAVINGS": "연금저축",
    "RESHORING_INVESTMENT": "RIA",
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
    if text.startswith("price_date="):
        return f"기준일={text.removeprefix('price_date=')}"
    if text.startswith("stock_warning:"):
        return f"종목 경고:{display_value(text.removeprefix('stock_warning:'))}"
    for status in ("valid", "invalid", "stale", "quarantined"):
        prefix = f"price_{status}:"
        if text.startswith(prefix):
            return f"가격 {DISPLAY_VALUES[status]}:{display_value(text.removeprefix(prefix))}"
    if text.endswith(")") and "(경고:" in text:
        base, _, warning_part = text.partition("(경고:")
        return f"{display_value(base)}(경고:{display_value(warning_part[:-1])})"
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


NAV_ICONS = {
    "home": '<path d="M3 11.5 12 4l9 7.5"/><path d="M5 10v9a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1v-9"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><path d="m4 6 1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"/>',
    "chart": '<path d="M6 20V14M12 20V6M18 20v-8"/>',
    "settings": '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M1 14h6M9 8h6M17 16h6"/>',
    "wallet": '<rect x="3" y="6" width="18" height="13" rx="2"/><path d="M3 10h18"/><circle cx="16" cy="14" r="1"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"/>',
}


def nav_icon(name: str) -> str:
    return f'<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{NAV_ICONS[name]}</svg>'


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
    from .trading_profiles import PROFILES
    bought_by_profile = {
        name: {
            row.get("ticker", "") for row in recent_virtual_trades(1000, path=profile["db_path"])
            if str(row.get("created_at", "")).startswith(today)
        }
        for name, profile in PROFILES.items()
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
            "aggressive_order_status": "체결" if ticker in bought_by_profile["aggressive"] else "미체결",
            "neutral_order_status": "체결" if ticker in bought_by_profile["neutral"] else "미체결",
            "reason": reason_summary(row, performance.get(ticker, {}), performance_penalty(ticker)),
        })
    return rows[:limit] if limit is not None else rows


def today_sell_alert_rows(limit: int | None = None) -> list[dict[str, str]]:
    from .trading_profiles import PROFILES
    today = datetime.now().date().isoformat()
    deliveries = tail_csv("logs/deliveries.csv", 10000)
    by_ticker: dict[str, dict[str, str]] = {}
    for name, profile in PROFILES.items():
        raw = tail_csv(profile["sell_alerts_log"], 10000)
        # Telegram delivery reconciliation only makes sense for a profile that
        # actually sends notifications -- a silent (notify=False) profile's
        # own alerts never get a delivery receipt, so reconciling would drop
        # every one of its rows.
        profile_rows = reconciled_daily_alert_rows(raw, deliveries, today, "sell") if profile["notify"] else daily_ticker_rows(raw, today)
        for row in profile_rows:
            ticker = row.get("ticker", "")
            entry = by_ticker.setdefault(ticker, {"name": row.get("name", ticker), "created_at": ""})
            entry[f"{name}_status"] = row.get("summary") or row.get("reason") or "매도 조건 충족"
            entry["created_at"] = max(entry["created_at"], row.get("created_at", ""))
    rows = []
    for entry in by_ticker.values():
        for name in PROFILES:
            entry.setdefault(f"{name}_status", "-")
        rows.append(entry)
    rows.sort(key=lambda row: row.get("created_at", ""), reverse=True)
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

    from .trading_profiles import PROFILES
    labels = {"aggressive": "적극투자형", "neutral": "위험중립형"}
    for name, profile in PROFILES.items():
        risk = latest_portfolio_risk(profile["db_path"])
        if risk.get("status") == "halted":
            rows.append({"source": "가상매매", "item": f"{labels.get(name, name)} 신규매수 중단", "status": display_value(risk.get("reason") or "위험 한도 도달")})
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


def market_calendar_state() -> dict:
    """Today's official KR market hours (Toss market-calendar), display-only
    -- not wired into is_trading_day()/is_market_alert_time(), which keep
    using their existing Naver-based check for now."""
    try:
        from .toss_client import TossClient
        today = TossClient().market_calendar_kr().get("today", {})
        integrated = today.get("integrated")
        regular = (integrated or {}).get("regularMarket")
        return {
            "connected": True, "date": today.get("date", ""), "open": bool(integrated),
            "start_time": regular.get("startTime", "")[11:16] if regular else "",
            "end_time": regular.get("endTime", "")[11:16] if regular else "",
        }
    except Exception as error:
        return {"connected": False, "reason": str(error)}


def real_account_state() -> dict:
    """Live read-only snapshot from the connected Toss Securities account.

    Mockup-stage: rendered synchronously into the static HTML like the rest
    of this file, not client-fetched like the virtual trader tab. Any
    failure (missing keys, network, empty account) degrades to a
    "not connected" state rather than breaking the whole dashboard.
    """
    try:
        from .toss_client import TossClient
        client = TossClient()
        accounts = client.accounts()
        if not accounts:
            return {"connected": False, "reason": "연결된 계좌 없음"}
        account_seq = accounts[0]["accountSeq"]
        holdings = client.holdings(account_seq)
        buying_power = client.buying_power(account_seq)
        cash = int(float(buying_power.get("cashBuyingPower") or 0))
        # Mockup scope: 국내 주식만 (해외주식 제외). Sum from the KR items
        # directly rather than the overview's krw total -- same number today,
        # but this way it can't silently include a KR-denominated instrument
        # that isn't a plain domestic stock.
        domestic_items = [item for item in holdings.get("items", []) if item.get("marketCountry") == "KR"]
        market_value = int(sum(float((item.get("marketValue") or {}).get("amount") or 0) for item in domestic_items))
        profit_loss = int(sum(float((item.get("profitLoss") or {}).get("amount") or 0) for item in domestic_items))
        from .toss_client import all_warnings_for, BLOCKING_STOCK_WARNINGS

        def watch_state(symbol: str) -> str:
            active = all_warnings_for(symbol)
            blocking = active & BLOCKING_STOCK_WARNINGS
            if blocking:
                return f"종목 경고: {','.join(sorted(blocking))}"
            if active:
                return f"종목 주의: {','.join(sorted(active))}"
            return "정상 보유"

        rows = [
            {
                "name": item.get("name", ""),
                "ticker": item.get("symbol", ""),
                "quantity": item.get("quantity", ""),
                "average_price": item.get("averagePurchasePrice", ""),
                "current_price": item.get("lastPrice", ""),
                "valuation": (item.get("marketValue") or {}).get("amount", ""),
                "profit_loss": (item.get("profitLoss") or {}).get("amount", ""),
                "return_pct": str(round(float((item.get("profitLoss") or {}).get("rate") or 0) * 100, 2)),
                "watch_state": watch_state(str(item.get("symbol") or "")),
            }
            for item in domestic_items
        ]
        # Order history/commissions are a nice-to-have next to the holdings
        # table -- a failure here (e.g. Toss outage) shouldn't blank out the
        # whole tab when holdings/buying-power already came back fine.
        try:
            orders = client.order_history(account_seq, "CLOSED", limit=10).get("orders", [])
        except Exception:
            orders = []
        order_rows = [
            {
                "ordered_at": order.get("orderedAt", ""),
                "ticker": order.get("symbol", ""),
                "order_side": order.get("side", ""),
                "order_type": order.get("orderType", ""),
                "order_status": order.get("status", ""),
                "order_quantity": order.get("quantity", ""),
                "filled_price": (order.get("execution") or {}).get("averageFilledPrice", ""),
                "filled_amount": (order.get("execution") or {}).get("filledAmount", ""),
                "commission_amount": (order.get("execution") or {}).get("commission", ""),
            }
            for order in orders if order.get("currency", "KRW") == "KRW"
        ]
        try:
            commission_rows = [
                {
                    "commission_rate": f"{float(row.get('commissionRate') or 0) * 100:.4f}%",
                    "start_date": row.get("startDate", ""), "end_date": row.get("endDate", ""),
                }
                for row in client.commissions(account_seq) if row.get("marketCountry") == "KR"
            ]
        except Exception:
            commission_rows = []
        return {
            "connected": True, "account_type": accounts[0].get("accountType", ""),
            "cash": cash, "market_value": market_value, "profit_loss": profit_loss,
            "total_equity": cash + market_value, "holdings": rows,
            "orders": order_rows, "commissions": commission_rows,
        }
    except Exception as error:
        return {"connected": False, "reason": str(error)}


def profile_selection_rows() -> list[dict[str, str]]:
    labels = {"aggressive": "적극투자형", "neutral": "위험중립형"}
    return [{**row, "profile": labels.get(row.get("profile"), row.get("profile"))} for row in latest_profile_selections()]


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
    elif column in COLUMN_VALUE_LABELS and str(display) in COLUMN_VALUE_LABELS[column]:
        shown = COLUMN_VALUE_LABELS[column][str(display)]
    else:
        shown = format_number(display) if column in NUMERIC_COLUMNS else display_value(display)
        if column in TIMESTAMP_COLUMNS and isinstance(shown, str) and "T" in shown:
            shown = shown.replace("T", " ")
    if column == "tracking_status" and shown in STATUS_PILL_CLASSES:
        return f"<td{attr}><span class='status-pill {STATUS_PILL_CLASSES[shown]}' title='{e(shown)}'>{e(TRACKING_STATUS_DISPLAY.get(shown, shown))}</span></td>"
    if column == "watch_state" and watch_state_pill_class(str(shown)):
        return f"<td{attr}><span class='status-pill {watch_state_pill_class(str(shown))}'>{e(shown)}</span></td>"
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
    # Same env var and floor strategy_learning.learn() gates on, so the dashboard
    # can never advertise a different threshold than the one that actually promotes.
    minimum = max(300, int(os.environ.get("LEARNING_MIN_SAMPLES", "300")))
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


def sample_progress_rows() -> list[dict[str, str]]:
    """Break "20일 학습 표본" progress down by every horizon (1/3/5/10/20일)
    so it's clear how many picks are still mid-flight at each stage, not
    just the final 20-day count that data_accumulation_rows shows."""
    rows = tail_csv("logs/recommendation_performance.csv", 100000)
    unique = {(row.get("pick_date", ""), row.get("ticker", "")): row for row in rows}
    samples = list(unique.values())
    total = len(samples)
    result = []
    for horizon in (1, 3, 5, 10, 20):
        completed = sum(bool(row.get(f"return_{horizon}d_pct")) for row in samples)
        result.append({
            "horizon": f"{horizon}일",
            "completed": str(completed),
            "total": str(total),
            "percent": f"{completed / total * 100:.1f}" if total else "0.0",
        })
    return result


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
        # `current` holds only the ticker's single active position (already
        # deduped by latest_position_rows), which belongs to whichever pick
        # most recently entered it -- not necessarily this row's pick_date.
        # A ticker recommended more than once must not borrow a different
        # pick's entry price, current price, or "currently held" status.
        position = current.get(ticker, {})
        if position.get("entry_date") != pick_date:
            position = {}
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
        # recommendation_performance.csv keeps tracking a pick's return for 20
        # trading days regardless of whether/when it sold, so once a sell alert
        # has fired this is what should drive "현재가"/"수익률" onward -- otherwise
        # they just freeze at the sell alert's own price/return forever and
        # duplicate the sell_alert_* columns instead of showing what happened
        # to the stock after the sale.
        performance_return = next(
            (row.get(column) for column in ("return_20d_pct", "return_10d_pct", "return_5d_pct", "return_3d_pct", "return_1d_pct") if row.get(column)), ""
        )
        performance_price = ""
        if performance_return and row.get("entry_close"):
            try:
                performance_price = str(round(float(row["entry_close"]) * (1 + float(performance_return) / 100)))
            except ValueError:
                performance_price = ""
        return_value = position.get("return_pct") or performance_return or alert.get("return_pct")
        result.append({
            "name": str(row.get("name") or position.get("name") or alert.get("name") or ticker),
            "pick_date": pick_date,
            "score": str(row.get("score") or ""),
            # Prefer the signal-day close (positions.csv/sell_alerts.csv basis) over
            # recommendation_performance's entry_close (next-day-open execution
            # price) -- sell_alert_price/sell_alert_return_pct below are always
            # computed against the signal-day close, so pairing them with the
            # other basis makes the displayed return look wrong even though both
            # numbers are individually correct.
            "entry_price": str(position.get("entry_price") or row.get("close") or row.get("entry_close") or ""),
            "current_price": str(position.get("close") or performance_price or alert.get("close") or ""),
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
<div class="home-heading"><div><h2>오늘의 투자 현황</h2><p class="muted">추천과 가상 주문 결과를 한눈에 확인하세요.</p></div></div>
<section class="profile-compare"><h2>가상계좌 성향 비교</h2><div class="profile-compare-grid">
  <div class="profile-compare-card"><span class="profile-compare-label">적극투자형</span>
    <div class="profile-compare-heading"><strong id="compare-aggressive-equity">불러오는 중</strong><svg class="sparkline" id="compare-aggressive-sparkline" width="90" height="30" viewBox="0 0 90 30"></svg></div>
    <div class="profile-compare-stats">
      <span>수익률 <b id="compare-aggressive-return">-</b></span>
      <span>오늘 손익 <b id="compare-aggressive-daily">-</b></span>
      <span>위험관리 <b id="compare-aggressive-risk">-</b></span>
      <span>오늘 매수·매도 <b id="compare-aggressive-orders">-</b></span>
    </div>
  </div>
  <div class="profile-compare-card"><span class="profile-compare-label">위험중립형</span>
    <div class="profile-compare-heading"><strong id="compare-neutral-equity">불러오는 중</strong><svg class="sparkline" id="compare-neutral-sparkline" width="90" height="30" viewBox="0 0 90 30"></svg></div>
    <div class="profile-compare-stats">
      <span>수익률 <b id="compare-neutral-return">-</b></span>
      <span>오늘 손익 <b id="compare-neutral-daily">-</b></span>
      <span>위험관리 <b id="compare-neutral-risk">-</b></span>
      <span>오늘 매수·매도 <b id="compare-neutral-orders">-</b></span>
    </div>
  </div>
</div></section>
<section class="home-operation"><h2>현재 운영 상태</h2><div class="operation-grid">
  <div><span>자동매매</span><b id="home-auto-status">확인 중</b></div>
  <div><span>시장 모드</span><b id="home-market-mode">확인 중</b></div>
  <div><span>오늘 추천</span><b>{len(recommendation_rows)}종목</b></div>
  <div><span>최근 가격 갱신</span><b id="home-price-updated">확인 중</b></div>
</div></section>
{user_table("Today recommendations", recommendation_rows, ["name", "close", "virtual_target_pct", "aggressive_order_status", "neutral_order_status"], "오늘 신규 추천 신호가 없습니다.")}
{user_table("Today sell alerts", sell_rows, ["name", "aggressive_status", "neutral_status"], "오늘 매도 조건을 충족한 보유종목이 없습니다.")}
{details("추천 성과 추적 보기", table("Positions", position_rows, ["name", "entry_price", "close", "return_pct", "decision"]))}
"""
    tracking_cards = "".join(
        f"<div class='tracking-card'><span>{e(label)}</span><b>{e(value)}</b></div>"
        for label, value in recommendation_tracking_summary(tracking_rows)
    )
    tracking_tab = f"""
<div class="home-heading"><div><h2>추천종목 추적</h2><p class="muted">가상매수 여부와 관계없이 추천 이후의 성과와 매도 알림을 관리합니다.</p></div><span class="system-pill">총 {len(tracking_rows)}건</span></div>
<div class="tracking-summary">{tracking_cards}</div>
{user_table("추천 추적 내역", tracking_rows, ["name", "pick_date", "score", "entry_price", "current_price", "return_pct", "tracking_status", "sell_alert_date", "sell_alert_price", "sell_alert_return_pct", "sell_reason", "virtual_bought"], "아직 추적할 추천종목이 없습니다.")}
"""
    trader_tab = """
<div class="trader-profile-toggle" role="tablist" aria-label="가상 트레이더 성향 선택">
  <button type="button" class="profile-button" id="profile-aggressive" data-profile="aggressive" aria-pressed="true">적극투자형</button>
  <button type="button" class="profile-button" id="profile-neutral" data-profile="neutral" aria-pressed="false">위험중립형</button>
</div>
<div class="trader-account-grid">
  <div class="trader-balance primary"><span>총자산 · <b id="trader-profile-label">적극투자형</b></span><strong id="trader-total-equity">0원</strong></div>
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
<section><h2>가상계좌 보유종목</h2><table><thead><tr><th>종목명</th><th class="num">보유수량</th><th class="num">투자비중</th><th class="num">보유일수</th><th class="num">진입가</th><th class="num">현재가</th><th class="num">평가손익</th><th class="num">수익률</th><th>매도 감시상태</th><th>다음 매도 기준</th></tr></thead><tbody id="trader-holdings"></tbody></table></section>
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
    real_account = real_account_state()
    real_account_warning_count = sum(1 for row in real_account.get("holdings", []) if str(row.get("watch_state", "")).startswith("종목"))
    real_account_tab = f"""
<div class="home-heading"><div><h2>실제 계좌</h2><p class="muted">토스증권 API로 연결된 실제 증권 계좌입니다 (조회 전용, 자동 매매 없음, 국내주식만 표시).</p></div><span class="system-pill {'ok' if real_account['connected'] else 'bad'}">{'연결됨 · ' + display_value(real_account.get('account_type', '')) if real_account['connected'] else '연결 안 됨'}</span></div>
{f'''<div class="trader-account-grid">
  <div class="trader-balance primary"><span>총자산</span><strong>{real_account['total_equity']:,}원</strong></div>
  <div class="trader-balance"><span>주문 가능 현금</span><strong>{real_account['cash']:,}원</strong></div>
  <div class="trader-balance"><span>주식 평가액</span><strong>{real_account['market_value']:,}원</strong></div>
  <div class="trader-balance"><span>평가손익</span><strong>{real_account['profit_loss']:+,}원</strong></div>
</div>
{user_table("보유종목", real_account["holdings"], ["name", "ticker", "quantity", "average_price", "current_price", "valuation", "profit_loss", "return_pct", "watch_state"], "보유 중인 종목이 없습니다.")}
{user_table("최근 주문 내역", real_account["orders"], ["ordered_at", "ticker", "order_side", "order_type", "order_status", "order_quantity", "filled_price", "filled_amount", "commission_amount"], "최근 체결/취소된 주문이 없습니다.")}
{user_table("수수료율", real_account["commissions"], ["commission_rate", "start_date", "end_date"], "수수료율 정보를 확인할 수 없습니다.")}''' if real_account['connected'] else f'''<section class="empty-section"><h2>{e(display_label("실제 계좌"))}</h2><div class="empty-state"><b>계좌에 연결할 수 없습니다</b><span>{e(real_account.get("reason", ""))}</span></div></section>'''}
"""
    market_calendar = market_calendar_state()
    market_calendar_card = (
        f'<section class="home-operation"><h2>오늘 장 운영 정보</h2><div class="operation-grid">'
        f'<div><span>날짜</span><b>{e(market_calendar["date"])}</b></div>'
        f'<div><span>개장 여부</span><b>{"개장" if market_calendar["open"] else "휴장"}</b></div>'
        f'<div><span>정규장 시작</span><b>{e(market_calendar["start_time"] or "-")}</b></div>'
        f'<div><span>정규장 종료</span><b>{e(market_calendar["end_time"] or "-")}</b></div>'
        f'</div></section>'
        if market_calendar["connected"] else ""
    )
    system_tab = f"""
<div class="home-heading"><div><h2>시스템 관리</h2><p class="muted">문제가 있을 때만 확인하면 되는 운영 정보입니다.</p></div><span class="system-pill {'bad' if issue_count else 'ok'}">{'경고 ' + str(issue_count) + '건' if issue_count else '모든 작업 정상'}</span></div>
{market_calendar_card}
<section class="learning-status"><h2>데이터 학습 준비</h2><div class="progress-heading"><b>20일 성과 표본 {progress['current']} / {progress['target']}</b><span>{progress['percent']}%</span></div><div class="progress-track"><span style="width:{progress['percent']}%"></span></div><p class="muted">최소 300개가 쌓이면 강화된 검증 절차를 통해 가중치 승격 여부를 판단합니다.</p></section>
{table("데이터 축적 현황", data_accumulation_rows(), ["metric", "value", "status"])}
{table("표본 진행 현황", sample_progress_rows(), ["horizon", "completed", "total", "percent"])}
{table("Issues", issue_rows(), ["source", "item", "status"])}
{user_table("Today run details", user_run_rows(), ["step", "status"], "오늘 사용자 확인이 필요한 자동 작업은 없습니다.")}
{details("데이터 품질과 발송 상태", table("Price quality", recent_price_quality(30), ["created_at", "ticker", "status", "reason"]) + table("Recent deliveries", tail_csv("logs/deliveries.csv", 10), ["created_at", "channel", "status", "error"]))}
{details("알고리즘 검증 결과", table("전략별 성과", benchmark_summary_rows(), ["strategy", "total_return_pct", "mdd_pct", "sharpe"]) + table("매도 사유별 결과", sell_quality_rows(), ["sell_reason_group", "count", "avg_realized_return_pct", "rebound_5d_rate_pct", "assessment"]))}
{details("프로필별 후보 평가", table("성향별 카테고리 점수", profile_selection_rows(), ["profile", "ticker", "name", "rank", "selected", "profile_score", "profitability_score", "growth_score", "stability_score", "dividend_score", "momentum_score", "news_category_score"]))}
{details("고급 운영 정보", table("Candidate rejection summary", rejection_summary(), ["reason", "count"]) + table("Recent position checks", recent_position_checks(), ["checked_at", "name", "return_pct", "decision", "reasons"]) + table("Current settings", settings_rows(), ["setting", "value"]) + table("Recommendation shape", recommendation_shape_rows(), ["type", "when", "action"]) + f'<section><h2>{e(display_label("Daily check"))}</h2><ul>{checks}</ul></section><section><h2>{e(display_label("Recent task log"))}</h2><ul>{task_log}</ul></section><section><h2>{e(display_label("Recent task errors"))}</h2><ul>{task_error_items}</ul></section>')}
"""
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{e(display_label("stockAlarm Dashboard"))}</title>
<style>
:root{{color-scheme:light dark;
--bg-page:#f6f7f9;--bg-surface:#ffffff;--bg-surface-alt:#f8fafc;--bg-accent-card:#ffffff;--bg-section:#ffffff;--section-border:transparent;
--text-primary:#111;--text-secondary:#64748b;--text-muted:#666;--text-strong:#334155;--text-on-accent:#111;--text-on-accent-muted:#64748b;
--border:#e5e7eb;--border-strong:#d0d5dd;--hover-overlay:rgba(0,0,0,.05);
--accent:#2563eb;--accent-bg:#eef6ff;--accent-text:#1d4ed8;--accent-border:#bfdbfe;
--danger-bg:#fef3f2;--danger-border:#fecdca;--danger-text:#b42318;
--success-bg:#ecfdf3;--success-border:#abefc6;--success-text:#147a2e;
--warn-text:#9a6700;--warn-bg:#fff8e6;--warn-border:#fde68a;--pos:#047857;--neg:#dc2626;--zero:#64748b;
--shadow-color:#ddd;--table-border:#eee;--table-header-bg:#fafafa;--details-bg:#eef2f6;--track-bg:#e2e8f0;--pill-neutral-bg:#f1f5f9;--pager-active-bg:#111}}
@media(prefers-color-scheme:dark){{:root:not([data-theme="light"]){{
--bg-page:#0b0f17;--bg-surface:#171b26;--bg-surface-alt:#1c2130;--bg-accent-card:#1e293b;--bg-section:#0b0f17;--section-border:transparent;
--text-primary:#e5e7eb;--text-secondary:#94a3b8;--text-muted:#94a3b8;--text-strong:#cbd5e1;--text-on-accent:#fff;--text-on-accent-muted:#cbd5e1;
--border:#2d3444;--border-strong:#3a4254;--hover-overlay:rgba(255,255,255,.08);
--accent:#60a5fa;--accent-bg:#1e3a5f;--accent-text:#93c5fd;--accent-border:#2d5b8a;
--danger-bg:#3f1d1d;--danger-border:#7f1d1d;--danger-text:#fca5a5;
--success-bg:#14291d;--success-border:#14532d;--success-text:#86efac;
--warn-text:#fbbf24;--warn-bg:#3a2e0a;--warn-border:#78350f;--pos:#34d399;--neg:#f87171;--zero:#94a3b8;
--shadow-color:rgba(0,0,0,.5);--table-border:#2d3444;--table-header-bg:#1c2130;--details-bg:var(--bg-page);--track-bg:#2d3444;--pill-neutral-bg:#232a3b;--pager-active-bg:#3a4254}}}}
:root[data-theme="light"]{{color-scheme:light}}
:root[data-theme="dark"]{{color-scheme:dark;
--bg-page:#0b0f17;--bg-surface:#171b26;--bg-surface-alt:#1c2130;--bg-accent-card:#1e293b;--bg-section:#0b0f17;--section-border:transparent;
--text-primary:#e5e7eb;--text-secondary:#94a3b8;--text-muted:#94a3b8;--text-strong:#cbd5e1;--text-on-accent:#fff;--text-on-accent-muted:#cbd5e1;
--border:#2d3444;--border-strong:#3a4254;--hover-overlay:rgba(255,255,255,.08);
--accent:#60a5fa;--accent-bg:#1e3a5f;--accent-text:#93c5fd;--accent-border:#2d5b8a;
--danger-bg:#3f1d1d;--danger-border:#7f1d1d;--danger-text:#fca5a5;
--success-bg:#14291d;--success-border:#14532d;--success-text:#86efac;
--warn-text:#fbbf24;--warn-bg:#3a2e0a;--warn-border:#78350f;--pos:#34d399;--neg:#f87171;--zero:#94a3b8;
--shadow-color:rgba(0,0,0,.5);--table-border:#2d3444;--table-header-bg:#1c2130;--details-bg:var(--bg-page);--track-bg:#2d3444;--pill-neutral-bg:#232a3b;--pager-active-bg:#3a4254}}
*{{box-sizing:border-box}} html{{overflow-x:hidden;overflow-y:scroll;scrollbar-gutter:stable}} body{{font-family:Segoe UI,Malgun Gothic,sans-serif;margin:24px;padding-top:110px;background:var(--bg-page);color:var(--text-primary);line-height:1.5}} .dashboard-header,.tabs{{max-width:1600px;margin-left:auto;margin-right:auto}}
.dashboard-header{{position:fixed;top:0;left:0;right:0;z-index:30;background:var(--bg-page);padding:12px 24px;border-bottom:1px solid var(--section-border);will-change:transform;backface-visibility:hidden;display:flex;align-items:flex-start;justify-content:space-between;gap:16px}}
.theme-toggle{{display:flex;background:var(--bg-surface-alt);border-radius:999px;padding:3px;gap:2px;flex-shrink:0}} .theme-toggle-btn{{display:flex;align-items:center;justify-content:center;width:30px;height:30px;border:0;border-radius:999px;background:transparent;color:var(--text-secondary);cursor:pointer}} .theme-toggle-btn[aria-pressed="true"]{{background:var(--bg-surface);color:var(--text-primary);box-shadow:0 1px 2px var(--shadow-color)}}
.dashboard-header h1{{margin:0}} .dashboard-meta{{margin-top:8px;color:var(--text-muted)}} h2{{line-height:1.3}} .muted{{color:var(--text-muted);overflow-wrap:anywhere}} .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin:22px 0}}
.card{{min-width:0;background:var(--bg-surface);border-radius:12px;padding:16px;box-shadow:0 1px 4px var(--shadow-color)}} .card span{{display:block;font-size:24px;margin-top:8px;overflow-wrap:anywhere}}
.home-heading{{display:flex;justify-content:space-between;align-items:center;gap:16px;margin:22px 0 10px}} .home-heading h2{{margin:0 0 4px;font-size:24px}} .home-heading p{{margin:0}} .system-pill{{padding:8px 12px;border-radius:999px;background:var(--bg-surface);border:1px solid var(--border-strong);white-space:nowrap}} .system-pill.ok{{background:var(--success-bg);border-color:var(--success-border)}} .system-pill.bad{{background:var(--danger-bg);border-color:var(--danger-border)}}
.profile-compare-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:20px 0 24px}} .profile-compare-card{{min-width:0;background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:14px;padding:20px;box-shadow:0 1px 4px var(--shadow-color)}} .profile-compare-label{{display:block;color:var(--text-on-accent-muted);font-size:13px}} .profile-compare-heading{{display:flex;align-items:flex-end;justify-content:space-between;gap:12px}} .profile-compare-card strong{{display:block;font-size:clamp(22px,2vw,28px);margin:10px 0;overflow-wrap:anywhere}} .sparkline{{flex-shrink:0;overflow:visible}} .profile-compare-stats{{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;font-size:13px;color:var(--text-on-accent-muted)}} .profile-compare-stats b{{font-weight:700}}
.operation-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}} .operation-grid>div{{background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:12px;padding:16px 18px;min-width:0}} .operation-grid span,.operation-grid b{{display:block}} .operation-grid span{{font-size:13px;color:var(--text-on-accent-muted)}} .operation-grid b{{margin-top:6px;font-size:17px;overflow-wrap:anywhere}} .empty-state{{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:22px;border:1px dashed var(--border-strong);border-radius:10px;background:var(--bg-surface-alt)}} .empty-state b{{color:var(--text-strong)}} .empty-state span{{color:var(--text-secondary)}} .progress-heading{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}} .progress-track{{height:12px;background:var(--track-bg);border-radius:999px;overflow:hidden}} .progress-track span{{display:block;height:100%;background:var(--accent);border-radius:inherit}} .learning-status p{{margin-bottom:0}}
.tracking-summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:20px 0}} .tracking-card{{background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:12px;padding:17px 18px;box-shadow:0 1px 4px var(--shadow-color)}} .tracking-card span,.tracking-card b{{display:block}} .tracking-card span{{color:var(--text-on-accent-muted);font-size:13px}} .tracking-card b{{font-size:22px;margin-top:7px}}
.highlight-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:16px 0}}
.highlight{{background:var(--bg-surface);color:var(--text-primary);border-radius:14px;padding:16px;box-shadow:0 1px 4px var(--shadow-color);border:1px solid var(--border)}} .highlight b{{display:block;color:var(--text-strong)}} .highlight span{{display:block;color:var(--text-primary);font-size:24px;font-weight:800;margin-top:8px}}
.tabs{{margin-top:20px}} .tab-input{{display:none}} .tab-labels{{display:flex;flex-direction:column;gap:4px;width:200px;position:fixed;top:118px;left:max(24px,calc((100vw - 1648px)/2 + 24px));z-index:20;background:var(--bg-accent-card);border-radius:12px;padding:10px;box-shadow:0 1px 4px var(--shadow-color)}} .tab-label{{display:flex;align-items:center;gap:10px;border-radius:8px;padding:11px 14px;cursor:pointer;font-weight:600;color:var(--text-on-accent-muted)}} .tab-label:hover{{background:var(--hover-overlay)}} .tab-label svg{{flex-shrink:0}}
.tab-panel{{display:none;margin-left:228px;min-width:0}} #tab-stocks:checked~.tab-labels label[for="tab-stocks"],#tab-tracking:checked~.tab-labels label[for="tab-tracking"],#tab-trader:checked~.tab-labels label[for="tab-trader"],#tab-real-account:checked~.tab-labels label[for="tab-real-account"],#tab-system:checked~.tab-labels label[for="tab-system"]{{background:var(--accent-bg);color:var(--accent-text)}}
#tab-stocks:checked~#stocks-panel,#tab-tracking:checked~#tracking-panel,#tab-trader:checked~#trader-panel,#tab-real-account:checked~#real-account-panel,#tab-system:checked~#system-panel{{display:block}}
section{{min-width:0;background:var(--bg-section);border:1px solid var(--section-border);border-radius:12px;padding:20px;margin:20px 0;box-shadow:0 1px 4px var(--shadow-color);overflow-x:auto;overflow-y:hidden}} section h2{{margin:0 0 16px}}
details{{min-width:0;background:var(--details-bg);border-radius:12px;margin:20px 0}} details summary{{cursor:pointer;padding:16px 18px;font-weight:700}} .details-body{{padding:0 18px 2px}} .details-body section{{box-shadow:none;border:1px solid var(--section-border)}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{border-bottom:1px solid var(--table-border);text-align:left;padding:10px 12px;white-space:nowrap}} th{{background:var(--table-header-bg);position:sticky;top:0}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
.ok{{color:var(--success-text);font-weight:600}} .warn{{color:var(--warn-text);font-weight:600}} .bad{{color:var(--danger-text);font-weight:600}} .pos{{color:var(--pos);font-weight:700}} .neg{{color:var(--neg);font-weight:700}} .zero{{color:var(--zero);font-weight:600}}
.status-pill{{display:inline-block;font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;white-space:nowrap}} .status-pill.pill-accent{{background:var(--accent-bg);color:var(--accent-text)}} .status-pill.pill-danger{{background:var(--danger-bg);color:var(--danger-text)}} .status-pill.pill-neutral{{background:var(--pill-neutral-bg);color:var(--text-secondary)}} .status-pill.pill-warn{{background:var(--warn-bg);color:var(--warn-text)}}
.nav-badge{{display:inline-flex;align-items:center;justify-content:center;min-width:18px;height:18px;padding:0 5px;margin-left:auto;border-radius:999px;background:var(--danger-bg);color:var(--danger-text);font-size:11px;font-weight:700}} .nav-badge[hidden]{{display:none}}
.pager{{display:flex;gap:6px;align-items:center;justify-content:center;margin-top:10px}} .pager button{{border:1px solid var(--border-strong);background:var(--bg-surface);color:var(--text-primary);border-radius:8px;padding:6px 10px;cursor:pointer}} .pager button.active{{background:var(--pager-active-bg);color:#fff;border-color:var(--pager-active-bg)}}
.trader-profile-toggle{{display:flex;gap:8px;margin:0 0 18px}} .profile-button{{flex:1;padding:10px;border-radius:8px;border:1px solid var(--border-strong);background:var(--bg-surface);color:var(--text-secondary);font-weight:600;cursor:pointer}} .profile-button[aria-pressed="true"]{{background:var(--accent-bg);border-color:var(--accent);color:var(--accent-text)}}
.trader-account-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin:20px 0}} .trader-balance{{min-width:0;background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:14px;padding:20px;box-shadow:0 1px 4px var(--shadow-color)}} .trader-balance span{{display:block;color:var(--text-on-accent-muted)}} .trader-balance strong{{display:block;font-size:clamp(21px,2vw,28px);margin-top:8px;overflow-wrap:anywhere}} .trader-status{{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;background:var(--accent-bg);border:1px solid var(--accent-border);border-radius:10px;padding:14px 16px;margin:18px 0}} .trader-form{{display:flex;gap:10px 12px;align-items:center;flex-wrap:wrap}} .trader-form label{{font-weight:600}} .trader-form input{{min-width:0;width:min(100%,320px);padding:10px;border:1px solid var(--border-strong);border-radius:8px;background:var(--bg-surface);color:var(--text-primary)}} .trader-form button{{padding:10px 14px;border:0;border-radius:8px;background:var(--bg-accent-card);color:var(--text-on-accent);cursor:pointer}} .trader-form button:disabled{{opacity:.4;cursor:not-allowed}}
.trader-breakdown{{display:flex;gap:12px 24px;justify-content:flex-end;flex-wrap:wrap;margin:0 2px 18px;color:var(--text-strong)}}
.trader-risk-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 16px}} .trader-risk-grid>div{{min-width:0;background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:12px;padding:14px 16px}} .trader-risk-grid span,.trader-risk-grid b{{display:block}} .trader-risk-grid b>span{{display:inline;color:inherit;font-size:inherit}} .trader-risk-grid span{{color:var(--text-on-accent-muted);font-size:13px}} .trader-risk-grid b{{margin-top:5px;overflow-wrap:anywhere}} .order-status{{overflow:visible}} .order-status>.trader-status{{margin:0;background:var(--bg-surface-alt);border-color:var(--border)}} .account-actions{{background:var(--bg-surface);border:1px solid var(--border);box-shadow:0 1px 4px var(--shadow-color)}} .account-actions summary{{font-size:18px}} .account-actions .trader-controls{{margin-top:0}}
.sale-summary-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0 20px}} .sale-summary-grid>div{{min-width:0;background:var(--bg-accent-card);color:var(--text-on-accent);border-radius:12px;padding:14px 16px}} .sale-summary-grid span,.sale-summary-grid b{{display:block}} .sale-summary-grid span{{color:var(--text-on-accent-muted);font-size:13px}} .sale-summary-grid b{{font-size:20px;margin-top:6px;overflow-wrap:anywhere}} .table-scroll{{overflow-x:auto}}
.trader-chart-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:20px 0}} .trader-chart-grid .donut-card{{margin:0;overflow:visible}} .donut-layout{{display:grid;grid-template-columns:minmax(190px,240px) minmax(0,1fr);align-items:center;gap:24px}} .donut-ring{{width:220px;aspect-ratio:1;border-radius:50%;display:grid;place-items:center;background:var(--track-bg);margin:auto;transition:background .2s ease}} .donut-hole{{width:58%;aspect-ratio:1;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;background:var(--bg-surface);box-shadow:0 0 0 1px var(--border)}} .donut-hole span{{font-size:13px;color:var(--text-secondary)}} .donut-hole b{{font-size:18px;margin-top:5px;max-width:110px;overflow-wrap:anywhere;color:var(--text-primary)}} .donut-legend{{display:grid;gap:10px;min-width:0}} .donut-legend-row{{display:grid;grid-template-columns:12px minmax(0,1fr) auto;align-items:center;gap:9px;font-size:14px}} .donut-swatch{{width:12px;height:12px;border-radius:4px}} .donut-label{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}} .donut-value{{font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}} .donut-empty{{color:var(--text-secondary)}}
button:focus-visible,input:focus-visible,.tab-label:focus-visible{{outline:3px solid var(--accent);outline-offset:2px}}
li{{margin:4px 0}}
@media(max-width:1100px){{.donut-layout{{grid-template-columns:1fr}}}}
@media(max-width:1000px){{.trader-account-grid,.trader-risk-grid,.sale-summary-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media(max-width:800px){{body{{margin:14px}} .tab-labels{{flex-direction:row;flex-wrap:wrap;position:static;width:auto;box-shadow:none;padding:0;background:transparent}} .tab-panel{{margin-left:0}} .tab-label{{padding:9px 12px;background:var(--bg-accent-card);color:var(--text-on-accent-muted)}} .home-heading{{align-items:flex-start}} section{{padding:16px}} th,td{{padding:9px 10px}}}}
@media(max-width:480px){{.profile-compare-grid,.trader-account-grid,.trader-risk-grid,.sale-summary-grid{{grid-template-columns:1fr}} .home-heading{{display:block}} .system-pill{{display:inline-block;margin-top:10px}} .profile-compare-card strong{{font-size:24px}} .trader-breakdown{{justify-content:flex-start;flex-direction:column;gap:6px}} .trader-form>*{{width:100%}}}}
</style>
</head>
<body>
<header class="dashboard-header">
<div>
<h1>{e(display_label("stockAlarm Dashboard"))}</h1>
<div class="dashboard-meta">{e(display_label("generated"))} {e(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</div>
</div>
<div class="theme-toggle" role="radiogroup" aria-label="테마 선택">
<button type="button" class="theme-toggle-btn" data-theme-choice="light" aria-label="라이트 모드" aria-pressed="false">{nav_icon("sun")}</button>
<button type="button" class="theme-toggle-btn" data-theme-choice="dark" aria-label="다크 모드" aria-pressed="false">{nav_icon("moon")}</button>
</div>
</header>
<div class="tabs">
<input class="tab-input" id="tab-stocks" name="tabs" type="radio" checked>
<input class="tab-input" id="tab-tracking" name="tabs" type="radio">
<input class="tab-input" id="tab-trader" name="tabs" type="radio">
<input class="tab-input" id="tab-real-account" name="tabs" type="radio">
<input class="tab-input" id="tab-system" name="tabs" type="radio">
<div class="tab-labels" role="tablist" aria-label="대시보드 화면">
<label class="tab-label" for="tab-stocks" role="tab" tabindex="0">{nav_icon("home")}홈</label>
<label class="tab-label" for="tab-tracking" role="tab" tabindex="0">{nav_icon("list")}추천 추적</label>
<label class="tab-label" for="tab-trader" role="tab" tabindex="0">{nav_icon("chart")}가상 트레이더<span class="nav-badge" id="nav-badge-trader" hidden></span></label>
<label class="tab-label" for="tab-real-account" role="tab" tabindex="0">{nav_icon("wallet")}실제 계좌{f'<span class="nav-badge">{real_account_warning_count}</span>' if real_account_warning_count else ''}</label>
<label class="tab-label" for="tab-system" role="tab" tabindex="0">{nav_icon("settings")}시스템 관리</label>
</div>
<div class="tab-panel" id="stocks-panel" role="tabpanel">{stock_tab}</div>
<div class="tab-panel" id="tracking-panel" role="tabpanel">{tracking_tab}</div>
<div class="tab-panel" id="trader-panel" role="tabpanel">{trader_tab}</div>
<div class="tab-panel" id="real-account-panel" role="tabpanel">{real_account_tab}</div>
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
let currentProfile = localStorage.getItem("stockAlarm.traderProfile") || "aggressive";
let trader = {{cash:0, holdings:[]}};
const won = value => `${{Math.round(value).toLocaleString("ko-KR")}}원`;
const tabLabels=[...document.querySelectorAll(".tab-label")];
function syncTabs() {{ tabLabels.forEach(label=>label.setAttribute("aria-selected",document.getElementById(label.htmlFor).checked?"true":"false")); }}
tabLabels.forEach((label,index)=>{{label.addEventListener("keydown",event=>{{if(event.key==="Enter"||event.key===" "){{event.preventDefault();label.click();}}if(event.key==="ArrowRight"||event.key==="ArrowLeft"){{event.preventDefault();const next=(index+(event.key==="ArrowRight"?1:-1)+tabLabels.length)%tabLabels.length;tabLabels[next].focus();tabLabels[next].click();}}}});label.addEventListener("click",()=>setTimeout(syncTabs));}}); syncTabs();
const themeKey = "stockAlarm.theme";
const themeButtons = [...document.querySelectorAll(".theme-toggle-btn")];
function applyTheme(choice) {{
  if(choice) document.documentElement.setAttribute("data-theme", choice);
  else document.documentElement.removeAttribute("data-theme");
  const effective = choice || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  themeButtons.forEach(btn=>btn.setAttribute("aria-pressed", btn.dataset.themeChoice === effective ? "true" : "false"));
}}
themeButtons.forEach(btn=>btn.addEventListener("click", () => {{
  localStorage.setItem(themeKey, btn.dataset.themeChoice);
  applyTheme(btn.dataset.themeChoice);
}}));
applyTheme(localStorage.getItem(themeKey));
async function traderRequest(path, options={{}}, profileOverride=null) {{
  if(remoteMode && !traderApiBase) throw new Error("원격 HTTPS API 주소를 입력해 주세요.");
  const profile = profileOverride || currentProfile;
  const headers={{"Content-Type":"application/json", ...(options.headers||{{}})}};
  if(remoteMode && remoteToken) headers.Authorization=`Bearer ${{remoteToken}}`;
  const url = `${{path}}${{path.includes("?") ? "&" : "?"}}profile=${{encodeURIComponent(profile)}}`;
  const requestBody = options.method === "POST" ? JSON.stringify({{...(options.body ? JSON.parse(options.body) : {{}}), profile}}) : options.body;
  const response = await fetch(`${{traderApiBase}}${{url}}`, {{...options, headers, body: requestBody}});
  const body = await response.json();
  if(!response.ok) throw new Error(body.error || "요청을 처리하지 못했습니다.");
  return body;
}}
function renderSparkline(svgId, points) {{
  const svg = document.getElementById(svgId);
  const values = (points || []).filter(value => Number.isFinite(value));
  if(values.length < 2) {{ svg.innerHTML = ""; return; }}
  const width=90, height=30, min=Math.min(...values), max=Math.max(...values), span=max-min || 1;
  const coords = values.map((value,index) => `${{(index/(values.length-1)*width).toFixed(1)}},${{(height-(value-min)/span*height).toFixed(1)}}`);
  const color = values[values.length-1] >= values[0] ? "#047857" : "#dc2626";
  svg.innerHTML = `<polyline points="${{coords.join(" ")}}" fill="none" stroke="${{color}}" stroke-width="2"/>`;
}}
function renderCompare(name, state) {{
  const prefix = `compare-${{name}}`;
  if(!state) {{ document.getElementById(`${{prefix}}-equity`).textContent = "확인 불가"; return; }}
  document.getElementById(`${{prefix}}-equity`).textContent = won(state.total_equity || state.cash || 0);
  renderSparkline(`${{prefix}}-sparkline`, state.equity_trend);
  const returnEl=document.getElementById(`${{prefix}}-return`); returnEl.textContent=`${{Number(state.total_return_pct||0).toFixed(2)}}%`; returnEl.className=state.total_return_pct>0?"pos":state.total_return_pct<0?"neg":"zero";
  const risk=state.risk || {{}};
  const riskLabels={{active:"정상",reduced:"축소",halted:"중단"}};
  document.getElementById(`${{prefix}}-risk`).textContent = riskLabels[risk.status] || risk.status || "확인 중";
  const dailyProfit=Number(risk.equity||state.total_equity||0)-Number(risk.daily_start_equity||risk.equity||state.total_equity||0);
  const dailyEl=document.getElementById(`${{prefix}}-daily`); dailyEl.textContent=`${{dailyProfit>=0?"+":""}}${{won(dailyProfit)}}`; dailyEl.className=dailyProfit>0?"pos":dailyProfit<0?"neg":"zero";
  document.getElementById(`${{prefix}}-orders`).textContent=`매수 ${{state.today_buys||0}} · 매도 ${{state.today_sells||0}}`;
}}
async function loadComparison() {{
  for(const name of Object.keys(profileLabels)) {{
    try {{ renderCompare(name, await traderRequest("/api/trader", {{}}, name)); }} catch(error) {{ renderCompare(name, null); }}
  }}
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
    add("«",0); add("‹",page-1); const block=Math.floor(page/5)*5; for(let index=block;index<Math.min(block+5,pages);index++)add(String(index+1),index,index===page); add("›",page+1); add("»",pages-1);
  }};
  draw();
}}
const donutColors=["#4c6ef5","#12b886","#f08c00","#e64980","#7048e8","#868e96"];
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
    {{label:"주문 가능 현금",value:cash,color:"#4c6ef5"}},
    {{label:"보유주식 평가액",value:holdingsValue,color:"#12b886"}},
  ],"입금 또는 보유자산이 없습니다.");
  const grouped={{}};
  (trader.holdings||[]).forEach(item=>{{const sector=item.sector||"미분류";grouped[sector]=(grouped[sector]||0)+Number(item.valuation||0);}});
  const sectors=Object.entries(grouped).map(([label,value])=>({{label,value}})).sort((a,b)=>b.value-a.value);
  const shown=sectors.slice(0,5); if(sectors.length>5)shown.push({{label:"기타",value:sectors.slice(5).reduce((sum,item)=>sum+item.value,0)}});
  document.getElementById("sector-donut-count").textContent=`${{sectors.length}}개`;
  renderDonut("sector-donut","sector-donut-legend",shown,"보유종목이 없습니다.");
}}
const WATCH_STATE_PILL_PREFIXES=[["종목 경고","pill-danger"],["매도조건 충족","pill-danger"],["종목 주의","pill-warn"],["손절선 근접","pill-warn"],["20일선 주의","pill-warn"],["1차 익절 완료","pill-accent"],["정상 보유","pill-neutral"]];
function watchStatePillClass(value) {{
  const hit = WATCH_STATE_PILL_PREFIXES.find(([prefix]) => value.startsWith(prefix));
  return hit ? hit[1] : "";
}}
function renderTrader(message="") {{
  document.getElementById("trader-total-equity").textContent = won(trader.total_equity || trader.cash || 0);
  document.getElementById("trader-cash").textContent = won(trader.cash || 0);
  document.getElementById("trader-holdings-value").textContent = won(trader.holdings_value || 0);
  const holdingsReturn=document.getElementById("trader-holdings-return"); holdingsReturn.textContent=`${{Number(trader.holdings_return_pct||0).toFixed(2)}}%`; holdingsReturn.className=trader.holdings_return_pct>0?"pos":trader.holdings_return_pct<0?"neg":"zero";
  const totalReturn=document.getElementById("trader-total-return"); totalReturn.textContent=`${{Number(trader.total_return_pct||0).toFixed(2)}}%`; totalReturn.className=trader.total_return_pct>0?"pos":trader.total_return_pct<0?"neg":"zero";
  const totalProfit=document.getElementById("trader-total-profit"); totalProfit.textContent=won(trader.total_profit_loss || 0); totalProfit.className=(trader.total_profit_loss>0?"pos":trader.total_profit_loss<0?"neg":"zero");
  renderPortfolioCharts();
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
  document.getElementById("home-price-updated").textContent=(trader.price_updated_at||"확인 중").replace("T"," ");
  const unavailable=trader.price_unavailable_tickers || [];
  document.getElementById("trader-price-status").textContent = unavailable.length ? `가격 확인 불가: ${{unavailable.join(", ")}}` : `${{trader.price_source || ""}} · ${{trader.price_updated_at || ""}}`;
  const body = document.getElementById("trader-holdings"); body.textContent = "";
  (trader.holdings || []).forEach(item => {{
    const row = document.createElement("tr");
    const sellReference=`손절 ${{won(item.stop_price||0)}}${{item.ma20?` · 20일선 ${{won(item.ma20)}}`:""}}`;
    const watchState=item.watch_state||'데이터 대기';
    const values=[item.name,Number(item.quantity||0).toLocaleString("ko-KR"),`${{Number(item.allocation_pct||0).toFixed(2)}}%`,item.holding_days==null?'확인 중':`${{item.holding_days}}일`,won(item.average_price),won(item.current_price),won(item.profit_loss),`${{Number(item.return_pct).toFixed(2)}}%`,watchState,sellReference];
    values.forEach((value,index)=>{{
      const cell=document.createElement("td");
      if(index===8 && watchStatePillClass(watchState)) {{const pill=document.createElement("span"); pill.className=`status-pill ${{watchStatePillClass(watchState)}}`; pill.textContent=value; cell.appendChild(pill);}}
      else cell.textContent=value;
      if(index>=1&&index<=7)cell.className="num";if(index===6||index===7)cell.className+=Number(item.profit_loss)>0?" pos":Number(item.profit_loss)<0?" neg":" zero";
      row.appendChild(cell);
    }}); body.appendChild(row);
  }});
  if (!body.children.length) body.innerHTML='<tr><td colspan="10" class="muted">가상계좌 보유종목이 없습니다.</td></tr>';
  const warningCount=(trader.holdings||[]).filter(item=>(item.watch_state||"").startsWith("종목")).length;
  const navBadge=document.getElementById("nav-badge-trader");
  navBadge.textContent=warningCount; navBadge.hidden=!warningCount;
  renderSales();
  document.getElementById("buy-button").disabled = remoteMode || !(trader.cash > 0 && traderCandidates.length) || risk.status === "halted";
  if(message) document.getElementById("trader-message").textContent=message;
}}
document.getElementById("deposit-button").addEventListener("click", async () => {{ const input=document.getElementById("deposit-amount"), amount=Math.floor(Number(input.value)); if(!(amount>0)) return renderTrader("1원 이상의 입금금액을 입력해 주세요."); try {{ trader=await traderRequest("/api/trader/deposit",{{method:"POST",body:JSON.stringify({{amount}})}}); input.value=""; renderTrader(`${{won(amount)}}을 DB 계좌에 입금했습니다.`); loadComparison(); }} catch(error) {{ renderTrader(error.message); }} }});
document.getElementById("buy-button").addEventListener("click", async () => {{
  try {{ trader=await traderRequest("/api/trader/buy",{{method:"POST",body:"{{}}"}}); renderTrader(`${{trader.bought}}개 종목을 ${{won(trader.spent)}}에 가상 매수했습니다.`); loadComparison(); }} catch(error) {{ renderTrader(error.message); }}
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
const profileButtons=[...document.querySelectorAll(".profile-button")];
const profileLabels={{aggressive:"적극투자형",neutral:"위험중립형"}};
function syncProfileButtons() {{
  profileButtons.forEach(button=>button.setAttribute("aria-pressed", String(button.dataset.profile===currentProfile)));
  document.getElementById("trader-profile-label").textContent = profileLabels[currentProfile] || currentProfile;
}}
function loadTrader() {{
  const legacyTrader = currentProfile==="aggressive" ? localStorage.getItem(traderKey) : null;
  return (remoteMode ? traderRequest("/api/trader") : (legacyTrader ? traderRequest("/api/trader/import",{{method:"POST",body:legacyTrader}}) : traderRequest("/api/trader")))
    .then(state => {{trader=state; if(state.imported) localStorage.removeItem(traderKey); renderTrader(state.imported?"기존 브라우저 가상 계좌를 DB로 이전했습니다.":"계좌와 최신 평가 정보를 불러왔습니다.");}})
    .catch(error => renderTrader(remoteMode ? (error.message || "원격 API 연결 정보를 입력해 주세요.") : "open_dashboard.bat으로 열어야 DB 가상 계좌를 사용할 수 있습니다."));
}}
profileButtons.forEach(button=>button.addEventListener("click", () => {{
  if(button.dataset.profile===currentProfile) return;
  currentProfile=button.dataset.profile; localStorage.setItem("stockAlarm.traderProfile", currentProfile);
  syncProfileButtons(); loadTrader(); loadComparison();
}}));
syncProfileButtons();
loadTrader();
loadComparison();
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
  const first=document.createElement("button"); first.type="button"; first.textContent="«"; first.addEventListener("click",()=>show(0)); pager.appendChild(first);
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
  const last=document.createElement("button"); last.type="button"; last.textContent="»"; last.addEventListener("click",()=>show(pageCount-1)); pager.appendChild(last);
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
