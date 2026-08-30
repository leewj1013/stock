from __future__ import annotations

import sys
from datetime import date, datetime, time

from .app import is_trading_day, load_env


# KRX 정기/임시 휴장일 (평일에 해당하는 날만). is_trading_day()는 "오늘자 캔들이
# 이미 발행됐는지"로 판단해서 08:30 장 시작 전에는 정상 거래일도 휴장으로 오판하므로,
# open 모드처럼 장 시작 전에 도는 배치는 이 정적 캘린더로만 휴장 여부를 판단해야 한다.
# 매년 갱신 필요: 한국거래소 공시채널(kind.krx.co.kr)의 연간 휴장일 공지 참고.
KR_MARKET_HOLIDAYS = {
    date(2026, 1, 1),   # 신정
    date(2026, 2, 16),  # 설 연휴
    date(2026, 2, 17),  # 설날
    date(2026, 2, 18),  # 설 연휴
    date(2026, 3, 2),   # 삼일절 대체공휴일
    date(2026, 5, 1),   # 근로자의 날
    date(2026, 5, 5),   # 어린이날
    date(2026, 5, 25),  # 부처님오신날 대체공휴일
    date(2026, 6, 3),   # 전국동시지방선거일
    date(2026, 7, 17),  # 제헌절(2026년부터 공휴일 재지정)
    date(2026, 8, 17),  # 광복절 대체공휴일
    date(2026, 9, 24),  # 추석 연휴
    date(2026, 9, 25),  # 추석
    date(2026, 10, 5),  # 개천절 대체공휴일
    date(2026, 10, 9),  # 한글날
    date(2026, 12, 25), # 성탄절
    date(2026, 12, 31), # 연말 폐장일
}


def should_run(mode: str, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if mode in {"intraday", "sell"}:
        if now.weekday() >= 5:
            return False
        if not time(8, 50) <= now.time() <= time(15, 40):
            return False
        # The 08:50 preparation run occurs before Naver publishes today's first row.
        return True if now.time() < time(9, 0) else is_trading_day(now.date())
    if mode == "open":
        if now.weekday() >= 5:
            return False
        # Pre-market: is_trading_day() can't tell today's candle from "not published
        # yet", so only the static holiday calendar can rule out a holiday here.
        return now.date() not in KR_MARKET_HOLIDAYS
    if mode in {"daily", "issue_alert"}:
        if now.weekday() >= 5:
            return False
        return is_trading_day(now.date())
    return True


def main() -> int:
    load_env()
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if should_run(mode):
        print(f"run {mode}")
        return 0
    print(f"skip {mode}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
