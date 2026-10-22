from __future__ import annotations

import csv
import json
import os
import urllib.request
from datetime import date, timedelta
from statistics import mean

from .app import configured_stocks, latest_naver_trading_day, load_env, naver_rows, stock_name, write_error_log
from .notifier import send_notification


US_MARKET_SYMBOLS = {".INX": "S&P 500", ".IXIC": "나스닥", ".SOX": "필라델피아 반도체", ".VIX": "VIX"}
US_CACHE_PATH = "data/us_market_summary.json"
# Daily closes kept so the VIX "fear regime" idea can be re-checked later
# (2026-09-20 study: too few episodes to act on yet).
US_HISTORY_PATH = "data/us_market_history.csv"
# 2022-01~2026-09: after a US session that moved 1%+ (S&P 500), KOSPI opened
# on average about that much in the same direction, then did ~0 intraday.
US_GAP_NOTE_PCT = 1.0


def naver_world_index(symbol: str) -> dict[str, str]:
    request = urllib.request.Request(f"https://api.stock.naver.com/index/{symbol}/basic", headers={"User-Agent": "stockAlarm/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        body = json.loads(response.read().decode("utf-8"))
    close = float(str(body["closePrice"]).replace(",", ""))
    return {"symbol": symbol, "name": US_MARKET_SYMBOLS[symbol], "market_date": str(body["localTradedAt"])[:10],
            "close": f"{close:.2f}", "change_pct": f"{float(body['fluctuationsRatio']):.2f}"}


def _read_us_cache(path: str = US_CACHE_PATH) -> list[dict[str, str]]:
    try:
        with open(path, encoding="utf-8") as file:
            return list(json.load(file).get("rows") or [])
    except (OSError, ValueError, AttributeError):
        return []


def append_us_history(rows: list[dict[str, str]], path: str = US_HISTORY_PATH) -> None:
    seen = set()
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as file:
            seen = {(row["market_date"], row["symbol"]) for row in csv.DictReader(file)}
    new = [row for row in rows if (row["market_date"], row["symbol"]) not in seen]
    if not new:
        return
    write_header = not os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["market_date", "symbol", "close", "change_pct"], extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(new)


def us_market_rows(cache_path: str = US_CACHE_PATH, history_path: str = US_HISTORY_PATH) -> list[dict[str, str]]:
    cached = {row.get("symbol"): row for row in _read_us_cache(cache_path) if row.get("symbol") in US_MARKET_SYMBOLS}
    rows = []
    for symbol in US_MARKET_SYMBOLS:
        try:
            rows.append(naver_world_index(symbol))
        except Exception:
            continue
    if not rows:
        return list(cached.values())
    merged = {**cached, **{row["symbol"]: row for row in rows}}
    rows = [merged[symbol] for symbol in US_MARKET_SYMBOLS if symbol in merged]
    directory = os.path.dirname(cache_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as file:
        json.dump({"updated_at": date.today().isoformat(), "rows": rows}, file, ensure_ascii=False, indent=2)
    append_us_history(rows, history_path)
    return rows


def us_row_text(row: dict[str, str]) -> str:
    if row["symbol"] == ".VIX":
        return f"VIX {float(row['close']):.2f} ({float(row['change_pct']):+.2f}%)"
    return f"{row['name']} {float(row['change_pct']):+.2f}%"


def us_gap_note(us_rows: list[dict[str, str]]) -> str:
    spx = next((row for row in us_rows if row.get("symbol") == ".INX"), None)
    if not spx or abs(float(spx["change_pct"])) < US_GAP_NOTE_PCT:
        return ""
    direction = "상승" if float(spx["change_pct"]) > 0 else "하락"
    return (f"S&P 500이 1% 넘게 {direction}한 다음 날, 코스피는 시초가에서 평균 1% 안팎 {direction} 출발했고 "
            "장중 추가 움직임은 거의 없었습니다(2022~2026 기준).")


def session_change(ticker: str, day: date) -> tuple[float, int] | None:
    """(% change, trading value) for `day` from Naver's daily rows."""
    prices = naver_rows(ticker, day - timedelta(days=10), day)
    if len(prices) < 2:
        return None
    previous, close, volume = int(prices[-2][4]), int(prices[-1][4]), int(prices[-1][5])
    return ((close - previous) / previous * 100 if previous else 0.0), close * volume


def market_rows(end_day: date | None = None) -> list[dict[str, str]]:
    day = end_day or latest_naver_trading_day()
    rows: list[dict[str, str]] = []
    for ticker, fallback in configured_stocks().items():
        figures = session_change(ticker, day)
        if figures is None:
            continue
        change, trading_value = figures
        rows.append({"ticker": ticker, "name": stock_name(ticker, fallback), "change_pct": f"{change:.2f}", "trading_value": str(trading_value)})
    return rows


def with_session_changes(leaders: list[dict], day: date) -> list[dict]:
    """Keep KRX's ranking but take each leader's % from the same Naver session.

    On 2026-09-18 KRX's daily rows labelled 09/17 disagreed with both Naver
    and Toss (SK hynix close 1,745,000 vs 1,766,000), so the same stock showed
    +4.13% and -0.80% in adjacent lists. Naver matches Toss, so it is the
    source for every % in the brief; KRX only says who led by trading value.
    """
    out = []
    for row in leaders:
        try:
            figures = session_change(str(row["ticker"]), day)
        except Exception:
            figures = None
        if figures:
            out.append({**row, "change_pct": figures[0]})
    return out


def summary(rows: list[dict[str, str]]) -> dict[str, str]:
    changes = [float(row["change_pct"]) for row in rows]
    up_count = sum(value > 0 for value in changes)
    down_count = sum(value < 0 for value in changes)
    return {"count": str(len(rows)), "up_count": str(up_count), "down_count": str(down_count), "up_ratio_pct": f"{up_count / len(rows) * 100:.1f}" if rows else "0.0", "avg_change_pct": f"{mean(changes):.2f}" if changes else "0.00"}


def index_changes(day: date) -> dict[str, float]:
    changes = {}
    for symbol in ("KOSPI", "KOSDAQ"):
        try:
            rows = naver_rows(symbol, day - timedelta(days=10), day, max_cache_age_seconds=300 if day == date.today() else None)
            if len(rows) >= 2 and str(rows[-1][0]) == day.strftime("%Y%m%d") and float(rows[-2][4]):
                changes[symbol] = (float(rows[-1][4]) / float(rows[-2][4]) - 1) * 100
        except (OSError, ValueError, TypeError, IndexError, KeyError):
            continue
    return changes


def whole_market_summary() -> dict[str, str] | None:
    """KOSPI+KOSDAQ-wide breadth (KRX official API when available, else a
    Naver-scrape fallback) -- distinct from the watchlist-only summary()
    above, so the brief can show whether the user's watchlist is tracking
    or diverging from the broader market."""
    from .market_breadth import cached_whole_market_average_change_pct, cached_whole_market_up_ratio

    up_ratio = cached_whole_market_up_ratio()
    avg_change = cached_whole_market_average_change_pct()
    if up_ratio is None or avg_change is None:
        return None
    return {"up_ratio_pct": f"{up_ratio * 100:.1f}", "avg_change_pct": f"{avg_change:.2f}"}


def market_regime(domestic: dict[str, str], whole_market: dict[str, str] | None = None) -> dict[str, str]:
    # Prefer the whole-market figures when available: the watchlist is
    # curated toward large/mid caps and can read stronger or weaker than the
    # broader KOSPI+KOSDAQ mood.
    basis = whole_market or domestic
    domestic_average = float(basis["avg_change_pct"])
    domestic_breadth = float(basis["up_ratio_pct"])
    # US moves are left out on purpose: the 2026-09-20 study found they are
    # fully priced into the KOSPI open and say nothing about the day after it.
    score = 50 + domestic_average * 8 + (domestic_breadth - 50) * 0.3
    score = max(0, min(100, score))
    if score >= 70:
        return {"label": "🟢 공격", "buy_limit": "70%", "guidance": "추세 확인 종목은 분할 매수 가능", "score": f"{score:.0f}"}
    if score >= 45:
        return {"label": "🟡 중립", "buy_limit": "40%", "guidance": "초반 추격을 피하고 거래량 확인 후 선별 매수", "score": f"{score:.0f}"}
    return {"label": "🔴 방어", "buy_limit": "10%", "guidance": "신규 매수를 최소화하고 손절선과 현금 비중을 우선", "score": f"{score:.0f}"}


def message(
    rows: list[dict[str, str]] | None = None,
    us_rows: list[dict[str, str]] | None = None,
    whole_market: dict[str, str] | None = None,
    whole_market_leaders: list[dict] | None = None,
) -> str:
    basis_day = None
    if rows is None:
        basis_day = latest_naver_trading_day()
        rows = market_rows(basis_day)
    if whole_market_leaders is None:
        whole_market_leaders = krx_top_trading_value_leaders()
        if basis_day:
            whole_market_leaders = with_session_changes(whole_market_leaders, basis_day)
    us_rows = us_rows if us_rows is not None else us_market_rows()
    whole_market = whole_market if whole_market is not None else whole_market_summary()
    info = summary(rows)
    leaders = whole_market_leaders or sorted(rows, key=lambda row: int(row.get("trading_value") or 0), reverse=True)[:3]
    domestic_basis = f"{basis_day:%m/%d} 종가" if basis_day else "직전 거래일 종가"
    us_dates = sorted({row.get("market_date", "") for row in us_rows if row.get("market_date")})
    lines = [
        "[08:30 아침 시황]",
        f"국내 {domestic_basis} · 미국 {us_dates[-1] if us_dates else '최근 마감'} 기준",
        "",
        "■ 핵심 변화",
        (f"국내 전체 상승 {whole_market['up_ratio_pct']}% · 평균 {float(whole_market['avg_change_pct']):+.2f}%"
         if whole_market else f"관심종목 상승 {info['up_ratio_pct']}% · 평균 {float(info['avg_change_pct']):+.2f}% (전체 시장 자료 없음)"),
        "",
        "■ 미국 증시 마감",
    ]
    if us_rows:
        lines.extend(f"- {us_row_text(row)} ({row['market_date']} 마감)" if row.get("market_date") else f"- {us_row_text(row)}" for row in us_rows)
        if note := us_gap_note(us_rows):
            lines.append(note)
    else:
        lines.append("- 미국 증시 데이터 수집 대기")
    indices = index_changes(basis_day) if basis_day else {}
    lines.extend(["", f"■ 국내 주요 지수 ({domestic_basis})"])
    lines.extend(f"{symbol} {change:+.2f}%" for symbol, change in indices.items())
    if not indices:
        lines.append("지수 등락률 확인 대기")
    if leaders:
        lines.extend(["", f"■ 거래대금 주도 종목 ({domestic_basis})"])
        lines.extend(f"- {row['name']}({row['ticker']}): {float(row['change_pct']):+.2f}%" for row in leaders[:3])
    lines.extend(["", "■ 오늘 확인", "개장 후 시장 상승 비율·추천 종목 거래량·추격매수 조건 확인"])
    return "\n".join(lines)


def krx_top_trading_value_leaders(top_n: int = 3) -> list[dict]:
    from .market_breadth import krx_top_trading_value_rows

    try:
        return krx_top_trading_value_rows(top_n)
    except Exception:
        return []


def run() -> str:
    load_env()
    return send_notification(message(), event_type="market_summary")


def main() -> None:
    try:
        print(run())
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
