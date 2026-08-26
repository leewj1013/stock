from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import date, timedelta
from statistics import mean

from .app import configured_stocks, latest_naver_trading_day, load_env, naver_rows, stock_name, write_error_log
from .notifier import send_notification


US_MARKET_SYMBOLS = {"SPY": "S&P 500", "QQQ": "나스닥 100", "SOXX": "반도체", "IWM": "미국 중소형주"}
US_CACHE_PATH = "data/us_market_summary.json"


def alpha_vantage_daily(symbol: str, api_key: str) -> dict[str, str]:
    query = urllib.parse.urlencode({"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": "compact", "apikey": api_key})
    request = urllib.request.Request(f"https://www.alphavantage.co/query?{query}", headers={"User-Agent": "stockAlarm/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))
    series = body.get("Time Series (Daily)") or {}
    days = sorted(series, reverse=True)
    if len(days) < 2:
        raise RuntimeError(body.get("Note") or body.get("Information") or body.get("Error Message") or f"{symbol} daily data unavailable")
    latest, previous = series[days[0]], series[days[1]]
    close, previous_close = float(latest["4. close"]), float(previous["4. close"])
    return {"symbol": symbol, "name": US_MARKET_SYMBOLS[symbol], "market_date": days[0], "close": f"{close:.2f}", "change_pct": f"{(close - previous_close) / previous_close * 100:.2f}"}


def _read_us_cache(path: str = US_CACHE_PATH) -> list[dict[str, str]]:
    try:
        with open(path, encoding="utf-8") as file:
            return list(json.load(file).get("rows") or [])
    except (OSError, ValueError, AttributeError):
        return []


def us_market_rows(api_key: str | None = None, cache_path: str = US_CACHE_PATH) -> list[dict[str, str]]:
    api_key = api_key or os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not api_key:
        return _read_us_cache(cache_path)
    cached = {row.get("symbol"): row for row in _read_us_cache(cache_path) if row.get("symbol")}
    rows = []
    for symbol in US_MARKET_SYMBOLS:
        try:
            rows.append(alpha_vantage_daily(symbol, api_key))
        except Exception:
            continue
    if rows:
        merged = {**cached, **{row["symbol"]: row for row in rows}}
        rows = [merged[symbol] for symbol in US_MARKET_SYMBOLS if symbol in merged]
        directory = os.path.dirname(cache_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as file:
            json.dump({"updated_at": date.today().isoformat(), "rows": rows}, file, ensure_ascii=False, indent=2)
        return rows
    return list(cached.values())


def market_rows(end_day: date | None = None) -> list[dict[str, str]]:
    day = end_day or latest_naver_trading_day()
    rows: list[dict[str, str]] = []
    for ticker, fallback in configured_stocks().items():
        prices = naver_rows(ticker, day - timedelta(days=10), day)
        if len(prices) < 2:
            continue
        previous, close, volume = int(prices[-2][4]), int(prices[-1][4]), int(prices[-1][5])
        change = (close - previous) / previous * 100 if previous else 0
        rows.append({"ticker": ticker, "name": stock_name(ticker, fallback), "change_pct": f"{change:.2f}", "trading_value": str(close * volume)})
    return rows


def summary(rows: list[dict[str, str]]) -> dict[str, str]:
    changes = [float(row["change_pct"]) for row in rows]
    up_count = sum(value > 0 for value in changes)
    down_count = sum(value < 0 for value in changes)
    return {"count": str(len(rows)), "up_count": str(up_count), "down_count": str(down_count), "up_ratio_pct": f"{up_count / len(rows) * 100:.1f}" if rows else "0.0", "avg_change_pct": f"{mean(changes):.2f}" if changes else "0.00"}


def market_regime(domestic: dict[str, str], us_rows: list[dict[str, str]]) -> dict[str, str]:
    domestic_average = float(domestic["avg_change_pct"])
    domestic_breadth = float(domestic["up_ratio_pct"])
    us_average = mean(float(row["change_pct"]) for row in us_rows) if us_rows else 0
    score = 50 + domestic_average * 8 + (domestic_breadth - 50) * 0.3 + us_average * 10
    score = max(0, min(100, score))
    if score >= 70:
        return {"label": "🟢 공격", "buy_limit": "70%", "guidance": "추세 확인 종목은 분할 매수 가능", "score": f"{score:.0f}"}
    if score >= 45:
        return {"label": "🟡 중립", "buy_limit": "40%", "guidance": "초반 추격을 피하고 거래량 확인 후 선별 매수", "score": f"{score:.0f}"}
    return {"label": "🔴 방어", "buy_limit": "10%", "guidance": "신규 매수를 최소화하고 손절선과 현금 비중을 우선", "score": f"{score:.0f}"}


def message(rows: list[dict[str, str]] | None = None, us_rows: list[dict[str, str]] | None = None) -> str:
    rows = rows if rows is not None else market_rows()
    us_rows = us_rows if us_rows is not None else us_market_rows()
    info = summary(rows)
    regime = market_regime(info, us_rows)
    leaders = sorted(rows, key=lambda row: int(row.get("trading_value") or 0), reverse=True)[:3]
    lines = [
        "[08:30 오늘의 매매 브리핑]",
        "",
        "■ 시장 판단",
        f"상태: {regime['label']} (시장점수 {regime['score']})",
        f"권장 신규 매수 한도: 보유 현금의 {regime['buy_limit']}",
        f"대응: {regime['guidance']}",
        "",
        "■ 미국 증시 마감",
    ]
    if us_rows:
        lines.extend(f"- {row['name']}({row['symbol']}): {float(row['change_pct']):+.2f}%" for row in us_rows)
    else:
        lines.append("- 미국 증시 데이터 수집 대기")
    lines.extend(["", "■ 국내 관심종목 흐름", f"상승/하락: {info['up_count']}개 / {info['down_count']}개", f"상승 비율: {info['up_ratio_pct']}%", f"평균 등락률: {float(info['avg_change_pct']):+.2f}%"])
    if leaders:
        lines.extend(["", "■ 거래대금 주도 종목"])
        lines.extend(f"- {row['name']}({row['ticker']}): {float(row['change_pct']):+.2f}%" for row in leaders)
    lines.extend(["", "■ 한 줄 결론", regime["guidance"], "미국장은 최근 마감가, 국내는 직전 거래일 종가 기준입니다."])
    return "\n".join(lines)


def run() -> str:
    load_env()
    return send_notification(message())


def main() -> None:
    try:
        print(run())
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
