from __future__ import annotations

from datetime import date, timedelta

from .app import load_env, naver_rows, write_error_log
from .data_store import record_virtual_valuation, virtual_trader_state


def current_prices() -> dict[str, int]:
    today = date.today()
    result = {}
    for holding in virtual_trader_state()["holdings"]:
        ticker = holding["ticker"]
        rows = naver_rows(ticker, today - timedelta(days=10), today, max_cache_age_seconds=60)
        if rows:
            result[ticker] = int(rows[-1][4])
    return result


def run() -> dict:
    load_env()
    result = record_virtual_valuation(current_prices())
    print(
        f"virtual_trader equity={result['equity']:,} return={result['return_pct']:.2f}% "
        f"change={result['return_change_pct']:+.2f}%p"
    )
    return result


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
