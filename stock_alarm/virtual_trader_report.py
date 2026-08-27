from __future__ import annotations

from datetime import date, datetime, timedelta

from .app import load_env, naver_rows, write_error_log
from .data_quality import checked_prices
from .data_store import record_virtual_valuation, virtual_trader_state
from .portfolio_risk import snapshot as risk_snapshot


def current_prices() -> dict[str, int]:
    today = date.today()
    tickers = [holding["ticker"] for holding in virtual_trader_state()["holdings"]]
    def reference_close(ticker: str) -> int | None:
        if datetime.now().time() < datetime.strptime("15:40", "%H:%M").time():
            return None
        try:
            from pykrx import stock
            frame = stock.get_market_ohlcv_by_date(today.strftime("%Y%m%d"), today.strftime("%Y%m%d"), ticker)
            return int(frame.iloc[-1, 3]) if not frame.empty else None
        except Exception:
            return None
    prices, _checks = checked_prices(
        tickers,
        lambda ticker: naver_rows(ticker, today - timedelta(days=10), today, max_cache_age_seconds=60),
        today,
        reference_provider=reference_close,
        reference_name="pykrx",
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
            f"사유: {risk.get('reason')}\n매도 점검과 평가는 계속 진행합니다.",
            event_type="portfolio_risk_halt",
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
