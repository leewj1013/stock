from __future__ import annotations

import os

from .app import configured_stocks, latest_naver_trading_day, load_env, write_error_log
from .daily_check import task_error_status


def yes(value: bool) -> str:
    return "ok" if value else "missing"


def enabled(name: str, default: str = "0") -> str:
    return "on" if os.environ.get(name, default) == "1" else "off"


def lines() -> list[str]:
    load_env()
    data_source = os.environ.get("DATA_SOURCE", "naver")
    stocks = configured_stocks()
    result = [
        f"DATA_SOURCE={data_source}",
        f"NOTIFIER={os.environ.get('NOTIFIER', 'telegram')}",
        f"watchlist_count={len(stocks)}",
        f"TELEGRAM_BOT_TOKEN={yes(bool(os.environ.get('TELEGRAM_BOT_TOKEN')))}",
        f"TELEGRAM_CHAT_ID={yes(bool(os.environ.get('TELEGRAM_CHAT_ID')))}",
        f"TELEGRAM_SEND_READY={yes(bool(os.environ.get('TELEGRAM_BOT_TOKEN') and os.environ.get('TELEGRAM_CHAT_ID')))}",
        f"KAKAO_REDIRECT_URI={os.environ.get('KAKAO_REDIRECT_URI', '') or 'missing'}",
        f"KAKAO_REST_API_KEY={yes(bool(os.environ.get('KAKAO_REST_API_KEY')))}",
        f"KAKAO_ACCESS_TOKEN={yes(bool(os.environ.get('KAKAO_ACCESS_TOKEN')))}",
        f"KAKAO_REFRESH_TOKEN={yes(bool(os.environ.get('KAKAO_REFRESH_TOKEN')))}",
        f"KAKAO_SEND_READY={yes(bool(os.environ.get('KAKAO_ACCESS_TOKEN')))}",
        f"AUTO_TRACK_PICKS={enabled('AUTO_TRACK_PICKS', '1')}",
        f"SEND_EMPTY_SELL_ALERT={enabled('SEND_EMPTY_SELL_ALERT')}",
        f"SELL_LOSS_PCT={os.environ.get('SELL_LOSS_PCT', '5')}",
        f"SELL_DROP_PCT={os.environ.get('SELL_DROP_PCT', '3')}",
        f"SELL_PROTECT_PROFIT_PCT={os.environ.get('SELL_PROTECT_PROFIT_PCT', '5')}",
        f"SELL_GIVEBACK_PCT={os.environ.get('SELL_GIVEBACK_PCT', '4')}",
        f"MIN_MARKET_UP_RATIO={os.environ.get('MIN_MARKET_UP_RATIO', '0')}",
        f"NEWS_LOOKUP={enabled('NEWS_LOOKUP')}",
        f"NEWS_SCORE_WEIGHT={os.environ.get('NEWS_SCORE_WEIGHT', '0')}",
        f"MIN_RECOMMEND_SCORE={os.environ.get('MIN_RECOMMEND_SCORE', '0')}",
        f"DART_LOOKUP={enabled('DART_LOOKUP')}",
        f"DART_API_KEY={yes(bool(os.environ.get('DART_API_KEY')))}",
        task_error_status(),
        f"recommendations_log={yes(os.path.exists('logs/recommendations.csv'))}",
        f"errors_log={yes(os.path.exists('logs/errors.log'))}",
    ]
    if data_source.lower() == "naver":
        result.append(f"latest_naver_trading_day={latest_naver_trading_day().isoformat()}")
    return result


def main() -> None:
    try:
        print("\n".join(lines()))
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
