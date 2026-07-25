from __future__ import annotations

import csv
import os
from statistics import mean

from .app import load_env, write_error_log
from .backtest import backtest_rows


def floats(name: str, default: str) -> list[float]:
    return [float(value.strip()) for value in os.environ.get(name, default).split(",") if value.strip()]


def ints(name: str, default: str) -> list[int]:
    return [int(value.strip()) for value in os.environ.get(name, default).split(",") if value.strip()]


def summarize(rows: list[list]) -> tuple[int, str, str]:
    returns = [float(row[-1]) for row in rows]
    if not returns:
        return 0, "", ""
    return len(returns), f"{mean(returns):.2f}", f"{sum(value > 0 for value in returns) / len(returns) * 100:.1f}"


def run() -> None:
    load_env()
    markets = [item.strip() for item in os.environ.get("MARKETS", "KOSPI,KOSDAQ").split(",") if item.strip()]
    top_n = int(os.environ.get("TOP_N", "5"))
    min_trading_value = int(os.environ.get("MIN_TRADING_VALUE", "5000000000"))
    test_days = int(os.environ.get("BACKTEST_DAYS", "20"))
    volume_multipliers = floats("TUNE_VOLUME_MULTIPLIERS", "1.5,2.0,2.5")
    hold_days_list = ints("TUNE_HOLD_DAYS", "1,3,5")

    os.makedirs("logs", exist_ok=True)
    path = "logs/tuning.csv"
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["volume_multiplier", "hold_days", "picks", "avg_return_pct", "win_rate_pct"])
        for volume_multiplier in volume_multipliers:
            for hold_days in hold_days_list:
                rows = backtest_rows(markets, top_n, min_trading_value, volume_multiplier, test_days, hold_days)
                picks, avg_return, win_rate = summarize(rows)
                writer.writerow([volume_multiplier, hold_days, picks, avg_return, win_rate])
    os.replace(tmp_path, path)
    print(f"wrote {path}")


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
