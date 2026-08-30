from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from .app import load_env, naver_rows, write_error_log
from .data_quality import checked_prices
from .data_store import record_virtual_valuation, virtual_trader_state
from .portfolio_risk import snapshot as risk_snapshot


def toss_reference_price(ticker: str) -> int | None:
    """Live last-traded price from Toss, used as an intraday cross-check for
    Naver's close. Unlike the pykrx fallback below, this works all session
    long instead of only after the 15:40 daily close settles."""
    if not (os.environ.get("TOSS_CLIENT_ID") and os.environ.get("TOSS_CLIENT_SECRET")):
        return None
    try:
        from .toss_client import TossClient
        prices = TossClient().prices([ticker])
        return int(float(prices[0]["lastPrice"])) if prices else None
    except Exception:
        return None


def current_prices() -> dict[str, int]:
    today = date.today()
    tickers = [holding["ticker"] for holding in virtual_trader_state()["holdings"]]
    def pykrx_reference_close(ticker: str) -> int | None:
        if datetime.now().time() < datetime.strptime("15:40", "%H:%M").time():
            return None
        try:
            from pykrx import stock
            frame = stock.get_market_ohlcv_by_date(today.strftime("%Y%m%d"), today.strftime("%Y%m%d"), ticker)
            return int(frame.iloc[-1, 3]) if not frame.empty else None
        except Exception:
            return None
    def reference_close(ticker: str) -> int | None:
        return toss_reference_price(ticker) or pykrx_reference_close(ticker)
    prices, _checks = checked_prices(
        tickers,
        lambda ticker: naver_rows(ticker, today - timedelta(days=10), today, max_cache_age_seconds=60),
        today,
        reference_provider=reference_close,
        reference_name="toss",
    )
    return prices


def run() -> dict:
    load_env()
    prices = current_prices()
    required = {holding["ticker"] for holding in virtual_trader_state()["holdings"]}
    missing = sorted(required - set(prices))
    if missing:
        result = {"status": "price_unavailable", "missing": missing}
        print(f"virtual_trader skipped missing_prices={','.join(missing)}")
        return result
    result = record_virtual_valuation(prices)
    risk = risk_snapshot(virtual_trader_state(prices))
    if risk.get("transition") == "halted":
        from .notifier import send_notification
        send_notification(
            "[가상트레이더 위험중단]\n신규매수를 중단합니다.\n"
            f"사유: {risk.get('reason')}\n"
            "보유종목은 유지되며 분할익절과 개별 매도조건은 계속 감시 중입니다.",
            event_type="portfolio_risk_halt",
        )
    elif risk.get("transition") == "resumed":
        from .notifier import send_notification
        send_notification(
            "[가상트레이더 위험중단 해제]\n신규매수를 다시 허용합니다.\n"
            "보유종목은 유지되며 분할익절과 개별 매도조건은 계속 감시 중입니다.",
            event_type="portfolio_risk_resume",
        )
    print(
        f"virtual_trader equity={result['equity']:,} return={result['return_pct']:.2f}% "
        f"change={result['return_change_pct']:+.2f}%p"
    )
    return {**result, "risk": risk}


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
