from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from .app import load_env, naver_rows, write_error_log
from .data_quality import checked_prices
from .data_store import record_virtual_valuation, virtual_trader_state
from .portfolio_risk import snapshot as risk_snapshot


RISK_REASON_LABELS = {
    "daily_loss_limit": "당일 계좌 손실 한도 도달",
    "weekly_loss_limit": "주간 계좌 손실 한도 도달",
    "drawdown_limit": "계좌 최고점 대비 최대낙폭 한도 도달",
    "exposure_limit": "보유종목 투자비중 한도 초과",
    "portfolio_risk_limit": "포트폴리오 위험 한도 도달",
}


def risk_reason_lines(risk: dict) -> list[str]:
    """Translate internal risk codes into actionable Korean Telegram text."""
    values = {
        "daily_loss_limit": ("daily_return_pct", "RISK_DAILY_LOSS_PCT", 2.0, "당일 손익률"),
        "weekly_loss_limit": ("weekly_return_pct", "RISK_WEEKLY_LOSS_PCT", 5.0, "주간 손익률"),
        "drawdown_limit": ("drawdown_pct", "RISK_MAX_DRAWDOWN_PCT", 10.0, "최고점 대비 낙폭"),
        "exposure_limit": ("exposure_pct", "RISK_MAX_EXPOSURE_PCT", 70.0, "현재 보유비중"),
    }
    codes = [code.strip() for code in str(risk.get("reason") or "").split(",") if code.strip()]
    lines = []
    for code in codes:
        label = RISK_REASON_LABELS.get(code, "기타 포트폴리오 위험 조건")
        if code not in values:
            lines.append(f"- {label}")
            continue
        value_key, env_key, default, value_label = values[code]
        try:
            current = float(risk.get(value_key))
            limit = abs(float(os.environ.get(env_key, str(default))))
        except (TypeError, ValueError):
            lines.append(f"- {label}")
            continue
        comparison = "초과 기준" if code == "exposure_limit" else "중단 기준"
        if code == "exposure_limit":
            lines.append(f"- {label}: {value_label} {current:.2f}% ({comparison} {limit:.2f}%)")
        else:
            lines.append(f"- {label}: {value_label} {current:+.2f}% ({comparison} {-limit:.2f}%)")
    return lines or ["- 포트폴리오 위험 한도 도달"]


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
        reasons = "\n".join(risk_reason_lines(risk))
        send_notification(
            "[가상트레이더 위험중단]\n신규매수를 중단합니다.\n"
            f"중단 사유\n{reasons}\n"
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
