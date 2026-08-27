from __future__ import annotations

import csv
import os
from datetime import date, datetime, timedelta
from statistics import mean

from .app import env_date, latest_naver_trading_day, load_env, naver_rows, recommend_for_day, recommend_naver, write_error_log, yyyymmdd


def close_after(ticker: str, start_day: date, hold_days: int) -> int | None:
    from pykrx import stock

    frame = stock.get_market_ohlcv_by_date(yyyymmdd(start_day), yyyymmdd(start_day + timedelta(days=hold_days * 3)), ticker)
    if len(frame) <= hold_days or len(frame.columns) < 4:
        return None
    return int(frame.iloc[hold_days, 3])


def naver_close_after(ticker: str, start_day: date, hold_days: int) -> int | None:
    rows = naver_rows(ticker, start_day, start_day + timedelta(days=hold_days * 3))
    if len(rows) <= hold_days:
        return None
    return int(rows[hold_days][4])


def naver_next_open(ticker: str, signal_day: date) -> tuple[date, int] | None:
    rows = naver_rows(ticker, signal_day, signal_day + timedelta(days=10))
    for row in rows:
        trading_day = datetime.strptime(str(row[0]), "%Y%m%d").date()
        if trading_day > signal_day and int(row[1]) > 0:
            return trading_day, int(row[1])
    return None


def trading_days(days: int) -> list[date]:
    from pykrx import stock

    found: list[date] = []
    day = env_date("AS_OF_DATE", date.today()) - timedelta(days=1)
    attempts = 0
    while len(found) < days and attempts < 900:
        try:
            if not stock.get_market_ohlcv_by_ticker(yyyymmdd(day), market="KOSPI").empty:
                found.append(day)
        except Exception:
            pass
        day -= timedelta(days=1)
        attempts += 1
    if len(found) < days:
        raise RuntimeError(f"Only found {len(found)} trading days in 900 calendar days.")
    return list(reversed(found))


def naver_trading_days(days: int) -> list[date]:
    day = latest_naver_trading_day() - timedelta(days=1)
    rows = naver_rows("005930", day - timedelta(days=days * 3 + 30), day)
    return [datetime.strptime(str(row[0]), "%Y%m%d").date() for row in rows[-days:]]


def backtest_rows(
    markets: list[str], top_n: int, min_trading_value: int, volume_multiplier: float, test_days: int, hold_days: int
) -> list[list]:
    rows = []
    use_naver = os.environ.get("DATA_SOURCE", "naver").lower() == "naver"
    days = naver_trading_days(test_days) if use_naver else trading_days(test_days)

    for day in days:
        picks = (
            recommend_naver(day, top_n, min_trading_value, volume_multiplier)
            if use_naver
            else recommend_for_day(day, markets, top_n, min_trading_value, volume_multiplier)
        )
        for pick in picks:
            execution = naver_next_open(pick.ticker, day) if use_naver else None
            entry_day, entry_price = execution if execution else (day, pick.close)
            exit_close = naver_close_after(pick.ticker, entry_day, hold_days) if use_naver else close_after(pick.ticker, entry_day, hold_days)
            if exit_close and entry_price:
                cost_bps = int(os.environ.get("EXECUTION_COST_BPS", "30")) + int(os.environ.get("BACKTEST_SLIPPAGE_BPS", "10"))
                returns = (exit_close - entry_price) / entry_price * 100 - cost_bps / 100
                rows.append([day.isoformat(), pick.ticker, pick.name, entry_price, exit_close, f"{returns:.2f}"])
    return rows


def write_summary(rows: list[list], hold_days: int, path: str = "logs/backtest_summary.csv") -> None:
    returns = [float(row[-1]) for row in rows]
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["metric", "value"])
        writer.writerow(["hold_days", hold_days])
        writer.writerow(["picks", len(returns)])
        if not returns:
            return
        best = max(rows, key=lambda row: float(row[-1]))
        worst = min(rows, key=lambda row: float(row[-1]))
        writer.writerow(["avg_return_pct", f"{mean(returns):.2f}"])
        writer.writerow(["win_rate_pct", f"{sum(value > 0 for value in returns) / len(returns) * 100:.1f}"])
        writer.writerow(["best", f"{best[2]}({best[1]}) {best[-1]}%"])
        writer.writerow(["worst", f"{worst[2]}({worst[1]}) {worst[-1]}%"])


def run() -> None:
    load_env()
    markets = [item.strip() for item in os.environ.get("MARKETS", "KOSPI,KOSDAQ").split(",") if item.strip()]
    top_n = int(os.environ.get("TOP_N", "5"))
    min_trading_value = int(os.environ.get("MIN_TRADING_VALUE", "5000000000"))
    volume_multiplier = float(os.environ.get("VOLUME_MULTIPLIER", "1.5"))
    test_days = int(os.environ.get("BACKTEST_DAYS", "20"))
    hold_days = int(os.environ.get("BACKTEST_HOLD_DAYS", "1"))
    rows = backtest_rows(markets, top_n, min_trading_value, volume_multiplier, test_days, hold_days)

    os.makedirs("logs", exist_ok=True)
    path = "logs/backtest.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["pick_date", "ticker", "name", "entry_close", "exit_close", f"return_{hold_days}d_pct"])
        writer.writerows(rows)
    write_summary(rows, hold_days)

    returns = [float(row[-1]) for row in rows]
    print(f"wrote {path}")
    print("wrote logs/backtest_summary.csv")
    if returns:
        print(f"picks={len(returns)} avg_return={mean(returns):.2f}% win_rate={sum(value > 0 for value in returns) / len(returns) * 100:.1f}%")


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
