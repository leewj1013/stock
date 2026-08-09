from __future__ import annotations

import csv
import json
import os
import ast
import traceback
import urllib.parse
import urllib.request
import time as time_module
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from io import StringIO
from statistics import mean


@dataclass(frozen=True)
class Pick:
    ticker: str
    name: str
    close: int
    volume_ratio: float
    trading_value: int
    score: float
    volume_score: float = 0
    trading_value_score: float = 0
    trend_score: float = 0
    news_score: float = 0
    disclosure_score: float = 0
    performance_penalty: float = 0

    @property
    def reason(self) -> str:
        return (
            f"거래량 {self.volume_ratio:.1f}배, "
            f"20일선 상회, 거래대금 {self.trading_value / 100_000_000:.0f}억 원"
        )


@dataclass(frozen=True)
class CandidateEvaluation:
    ticker: str
    name: str
    values: dict
    pick: Pick | None = None


DEFAULT_STOCKS = {
    "005930": "Samsung Electronics",
    "000660": "SK hynix",
    "035420": "NAVER",
    "035720": "Kakao",
    "005380": "Hyundai Motor",
    "051910": "LG Chem",
    "006400": "Samsung SDI",
    "068270": "Celltrion",
    "105560": "KB Financial",
    "055550": "Shinhan Financial",
}

WATCHLIST_PATH = "data/watchlist.csv"
POSITIONS_PATH = "data/positions.csv"
SELL_ALERTS_PATH = "logs/sell_alerts.csv"


def load_env(path: str = ".env") -> None:
    os.makedirs(".cache/matplotlib", exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", os.path.abspath(".cache/matplotlib"))
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as file:
        for raw in file:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def save_env_value(key: str, value: str, path: str = ".env") -> None:
    lines = []
    found = False
    if os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            lines = file.readlines()
    for index, raw in enumerate(lines):
        if raw.strip().startswith(f"{key}="):
            lines[index] = f"{key}={value}\n"
            found = True
    if not found:
        lines.append(f"{key}={value}\n")
    with open(path, "w", encoding="utf-8") as file:
        file.writelines(lines)


def yyyymmdd(day: date) -> str:
    return day.strftime("%Y%m%d")


def env_date(name: str, default: date) -> date:
    value = os.environ.get(name, "")
    return datetime.strptime(value, "%Y-%m-%d").date() if value else default


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "")
    return float(value) if value else default


def latest_trading_day() -> date:
    from pykrx import stock

    day = env_date("AS_OF_DATE", date.today())
    for _ in range(900):
        try:
            frame = stock.get_market_ohlcv_by_ticker(yyyymmdd(day), market="KOSPI")
            if not frame.empty:
                return day
        except Exception:
            pass
        day -= timedelta(days=1)
    raise RuntimeError("Could not find a recent trading day.")


def make_pick(ticker: str, end_day: date, min_trading_value: int, volume_multiplier: float) -> Pick | None:
    from pykrx import stock

    start_day = end_day - timedelta(days=60)
    frame = stock.get_market_ohlcv_by_date(yyyymmdd(start_day), yyyymmdd(end_day), ticker)
    if len(frame) < 21 or len(frame.columns) < 6:
        return None

    closes = [int(value) for value in frame.iloc[:, 3].tail(20)]
    highs = [int(value) for value in frame.iloc[:, 1].tail(10)]
    lows = [int(value) for value in frame.iloc[:, 2].tail(10)]
    volumes = [int(value) for value in frame.iloc[:, 4].tail(21)]
    today_volume = volumes[-1]
    avg_volume = mean(volumes[:-1])
    close = closes[-1]
    ma20 = mean(closes)
    trading_value = int(frame.iloc[-1, 5])

    if avg_volume <= 0:
        return None
    volume_ratio = today_volume / avg_volume
    previous_close = int(frame.iloc[-2, 3])
    if not passes_risk_filters(previous_close, close, highs, lows, closes[-10:]):
        return None
    if volume_ratio < volume_multiplier or close <= ma20 or trading_value < min_trading_value:
        return None

    name = stock.get_market_ticker_name(ticker)
    volume_score, trading_value_score, trend_score = calculate_score_parts(close, ma20, volume_ratio, trading_value)
    score = volume_score + trading_value_score + trend_score + news_bonus(name) + dart_bonus(ticker) - performance_penalty(ticker)
    return Pick(ticker, name, close, volume_ratio, trading_value, round(score, 2), volume_score, trading_value_score, trend_score)


def naver_rows(
    ticker: str,
    start_day: date,
    end_day: date,
    max_cache_age_seconds: int | None = None,
) -> list[list]:
    cache_path = naver_cache_path(ticker, start_day, end_day)
    cache_exists = os.path.exists(cache_path)
    cache_fresh = cache_exists and (
        max_cache_age_seconds is None
        or time_module.time() - os.path.getmtime(cache_path) <= max_cache_age_seconds
    )
    if os.environ.get("NO_CACHE", "0") != "1" and cache_fresh:
        with open(cache_path, encoding="utf-8") as file:
            return json.load(file)

    url = "https://api.finance.naver.com/siseJson.naver?" + urllib.parse.urlencode(
        {
            "symbol": ticker,
            "requestType": 1,
            "startTime": yyyymmdd(start_day),
            "endTime": yyyymmdd(end_day),
            "timeframe": "day",
        }
    )
    with urllib.request.urlopen(url, timeout=10) as response:
        body = response.read()
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("cp949")
    rows = ast.literal_eval(text.strip())
    data = [row for row in rows[1:] if row]
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False)
    return data


def naver_cache_path(ticker: str, start_day: date, end_day: date) -> str:
    return os.path.join(".cache", "naver", f"{ticker}-{yyyymmdd(start_day)}-{yyyymmdd(end_day)}.json")


def latest_naver_trading_day() -> date:
    end_day = env_date("AS_OF_DATE", date.today())
    cache_age = 300 if end_day == date.today() else None
    rows = naver_rows("005930", end_day - timedelta(days=30), end_day, max_cache_age_seconds=cache_age)
    if not rows:
        raise RuntimeError("Could not find a recent Naver trading day.")
    return datetime.strptime(str(rows[-1][0]), "%Y%m%d").date()


def is_trading_day(today: date | None = None) -> bool:
    today = today or env_date("AS_OF_DATE", date.today())
    return latest_naver_trading_day() == today


def is_market_alert_time(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return time(9, 0) <= now.time() <= time(15, 30) and is_trading_day(now.date())


def make_naver_pick(
    ticker: str, name: str, end_day: date, min_trading_value: int, volume_multiplier: float
) -> Pick | None:
    return evaluate_naver_candidate(ticker, name, end_day, min_trading_value, volume_multiplier).pick


def evaluate_naver_candidate(
    ticker: str, name: str, end_day: date, min_trading_value: int, volume_multiplier: float
) -> CandidateEvaluation:
    evaluated_at = datetime.now().isoformat(timespec="seconds")
    base = {"ticker": ticker, "name": name, "evaluated_at": evaluated_at, "passed": 0, "selected": 0}
    rows = naver_rows(ticker, end_day - timedelta(days=90), end_day)
    if len(rows) < 21:
        return CandidateEvaluation(ticker, name, {**base, "rejection_reasons": "insufficient_history"})

    closes = [int(row[4]) for row in rows[-20:]]
    highs = [int(row[2]) for row in rows[-10:]]
    lows = [int(row[3]) for row in rows[-10:]]
    volumes = [int(row[5]) for row in rows[-21:]]
    today_volume = volumes[-1]
    avg_volume = mean(volumes[:-1])
    close = closes[-1]
    ma20 = mean(closes)
    trading_value = close * today_volume

    if avg_volume <= 0:
        return CandidateEvaluation(ticker, name, {**base, "close": close, "rejection_reasons": "invalid_average_volume"})
    volume_ratio = today_volume / avg_volume
    previous_close = int(rows[-2][4])
    avg_range = average_intraday_range_pct(highs, lows, closes[-10:])
    rejections = []
    if abs(day_change_pct(previous_close, close)) > env_float("MAX_DAY_CHANGE_PCT", 8):
        rejections.append("day_change")
    if avg_range > env_float("MAX_AVG_RANGE_PCT", 12):
        rejections.append("average_range")
    if volume_ratio < volume_multiplier:
        rejections.append("volume_ratio")
    if close <= ma20:
        rejections.append("below_ma20")
    if trading_value < min_trading_value:
        rejections.append("trading_value")
    values = {
        **base,
        "close": close,
        "previous_close": previous_close,
        "day_return_pct": day_change_pct(previous_close, close),
        "volume": today_volume,
        "avg_volume": avg_volume,
        "volume_ratio": volume_ratio,
        "trading_value": trading_value,
        "ma20": ma20,
        "distance_ma20_pct": (close / ma20 - 1) * 100 if ma20 else None,
        "avg_range_pct": avg_range,
        "rejection_reasons": ",".join(rejections),
    }
    if rejections:
        return CandidateEvaluation(ticker, name, values)
    name = stock_name(ticker, name)
    volume_score, trading_value_score, trend_score = calculate_score_parts(close, ma20, volume_ratio, trading_value)
    news_score = news_bonus(name)
    disclosure_score = dart_bonus(ticker)
    penalty = performance_penalty(ticker)
    score = volume_score + trading_value_score + trend_score + news_score + disclosure_score - penalty
    pick = Pick(ticker, name, close, volume_ratio, trading_value, round(score, 2), volume_score, trading_value_score, trend_score, news_score, disclosure_score, penalty)
    values.update(
        name=name,
        volume_score=volume_score,
        trading_value_score=trading_value_score,
        trend_score=trend_score,
        news_score=news_score,
        disclosure_score=disclosure_score,
        performance_penalty=penalty,
        final_score=round(score, 2),
        passed=1,
    )
    return CandidateEvaluation(ticker, name, values, pick)


def calculate_score(close: int, ma20: float, volume_ratio: float, trading_value: int) -> float:
    return round(sum(calculate_score_parts(close, ma20, volume_ratio, trading_value)), 2)


def calculate_score_parts(close: int, ma20: float, volume_ratio: float, trading_value: int) -> tuple[float, float, float]:
    volume_score = min(volume_ratio / 3, 1) * 45
    trading_value_score = min(trading_value / 300_000_000_000, 1) * 35
    trend_score = min(max(close / ma20 - 1, 0) / 0.10, 1) * 20
    return round(volume_score, 2), round(trading_value_score, 2), round(trend_score, 2)


def news_bonus(name: str) -> float:
    weight = env_float("NEWS_SCORE_WEIGHT", 0)
    if not weight:
        return 0
    try:
        from .news_reference import reference

        score, _notes = reference(name)
        return float(score) * weight
    except Exception:
        return 0


def dart_bonus(ticker: str) -> float:
    weight = env_float("DART_SCORE_WEIGHT", 0)
    if not weight:
        return 0
    try:
        from .dart_reference import reference

        score, _notes = reference(ticker)
        return float(score) * weight
    except Exception:
        return 0


def performance_penalty(ticker: str, path: str = "logs/recommendation_performance.csv") -> float:
    if not os.path.exists(path):
        return 0
    values = []
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            if row.get("ticker") == ticker and row.get("return_1d_pct"):
                values.append(float(row["return_1d_pct"]))
    if len(values) < 3:
        return 0
    avg = mean(values)
    return min(abs(avg), 10) if avg < 0 else 0


@lru_cache(maxsize=512)
def stock_name(ticker: str, fallback: str) -> str:
    if os.environ.get("KOREAN_STOCK_NAMES", "1") != "1":
        return fallback
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            from pykrx import stock

            return stock.get_market_ticker_name(ticker) or fallback
    except Exception:
        return fallback


def day_change_pct(previous_close: int, close: int) -> float:
    return (close - previous_close) / previous_close * 100 if previous_close else 0


def average_intraday_range_pct(highs: list[int], lows: list[int], closes: list[int]) -> float:
    ranges = [(high - low) / close * 100 for high, low, close in zip(highs, lows, closes) if close]
    return mean(ranges) if ranges else 0


def passes_risk_filters(previous_close: int, close: int, highs: list[int], lows: list[int], closes: list[int]) -> bool:
    max_day_change = env_float("MAX_DAY_CHANGE_PCT", 8)
    max_range = env_float("MAX_AVG_RANGE_PCT", 12)
    if abs(day_change_pct(previous_close, close)) > max_day_change:
        return False
    return average_intraday_range_pct(highs, lows, closes) <= max_range


def market_up_ratio(moves: list[bool]) -> float:
    return sum(moves) / len(moves) if moves else 0


def naver_market_up_ratio(end_day: date) -> float:
    moves = []
    for ticker in configured_stocks():
        rows = naver_rows(ticker, end_day - timedelta(days=10), end_day)
        if len(rows) >= 2:
            moves.append(int(rows[-1][4]) > int(rows[-2][4]))
    return market_up_ratio(moves)


def passes_market_filter(end_day: date) -> bool:
    minimum = env_float("MIN_MARKET_UP_RATIO", 0)
    return naver_market_up_ratio(end_day) >= minimum


def configured_stocks() -> dict[str, str]:
    raw = os.environ.get("STOCKS", "")
    stocks: dict[str, str] = {}
    if raw:
        for item in raw.split(","):
            ticker, _, name = item.partition(":")
            ticker = ticker.strip()
            if ticker:
                stocks[ticker] = name.strip() or ticker
        return stocks
    if os.path.exists(WATCHLIST_PATH):
        with open(WATCHLIST_PATH, newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                ticker = row.get("ticker", "").strip()
                name = row.get("name", "").strip()
                if ticker:
                    stocks[ticker] = name or ticker
        return stocks
    return DEFAULT_STOCKS


def recommend_naver(end_day: date, top_n: int, min_trading_value: int, volume_multiplier: float, run_id: str | None = None) -> list[Pick]:
    if not passes_market_filter(end_day):
        if run_id:
            from .data_store import write_candidates
            write_candidates(run_id, ({"ticker": ticker, "name": name, "evaluated_at": datetime.now().isoformat(timespec="seconds"), "passed": 0, "selected": 0, "rejection_reasons": "market_filter"} for ticker, name in configured_stocks().items()))
        return []
    picks = []
    evaluations = []
    for ticker, name in configured_stocks().items():
        if run_id:
            evaluation = evaluate_naver_candidate(ticker, name, end_day, min_trading_value, volume_multiplier)
            evaluations.append(evaluation)
            pick = evaluation.pick
        else:
            pick = make_naver_pick(ticker, name, end_day, min_trading_value, volume_multiplier)
        if pick:
            picks.append(pick)
    selected = top_picks(picks, top_n)
    if run_id:
        from .data_store import write_candidates
        ranks = {pick.ticker: index for index, pick in enumerate(sorted(picks, key=lambda item: item.score, reverse=True), 1)}
        selected_tickers = {pick.ticker for pick in selected}
        rows = []
        for evaluation in evaluations:
            values = dict(evaluation.values)
            values["rank"] = ranks.get(evaluation.ticker)
            values["selected"] = int(evaluation.ticker in selected_tickers)
            if evaluation.pick and evaluation.ticker not in selected_tickers and not values.get("rejection_reasons"):
                values["rejection_reasons"] = "score_or_portfolio_filter"
            rows.append(values)
        write_candidates(run_id, rows)
    return selected


def recommend_for_day(
    end_day: date,
    markets: list[str],
    top_n: int,
    min_trading_value: int,
    volume_multiplier: float,
    ticker_provider=None,
) -> list[Pick]:
    if ticker_provider is None:
        from pykrx import stock

        ticker_provider = stock.get_market_ticker_list
    picks: list[Pick] = []
    for market in markets:
        for ticker in ticker_provider(yyyymmdd(end_day), market=market):
            pick = make_pick(ticker, end_day, min_trading_value, volume_multiplier)
            if pick:
                picks.append(pick)
    return top_picks(picks, top_n)


def top_picks(picks: list[Pick], top_n: int) -> list[Pick]:
    minimum = env_float("MIN_RECOMMEND_SCORE", 50)
    blocked = open_recommended_tickers()
    result = []
    seen = set()
    for pick in sorted(picks, key=lambda item: item.score, reverse=True):
        if pick.score < minimum or pick.ticker in blocked or pick.ticker in seen:
            continue
        seen.add(pick.ticker)
        result.append(pick)
        if len(result) >= top_n:
            break
    return result


def open_recommended_tickers(
    positions_path: str = POSITIONS_PATH,
    sell_alerts_path: str = SELL_ALERTS_PATH,
    recommendations_path: str = "logs/recommendations.csv",
) -> set[str]:
    sell_alerts = latest_sell_alert_times(sell_alerts_path)
    result = set()
    cooldown_until = datetime.now() - timedelta(days=int(env_float("SELL_RECOMMEND_COOLDOWN_DAYS", 3)))
    for ticker, sell_time in sell_alerts.items():
        if sell_time >= cooldown_until:
            result.add(ticker)
    for ticker, entry_time in latest_position_times(positions_path).items():
        sell_time = sell_alerts.get(ticker)
        if not sell_time or entry_time > sell_time:
            result.add(ticker)
    for ticker, recommend_time in latest_recommendation_times(recommendations_path).items():
        sell_time = sell_alerts.get(ticker)
        if not sell_time or recommend_time > sell_time:
            result.add(ticker)
    return result


def latest_position_times(path: str) -> dict[str, datetime]:
    result: dict[str, datetime] = {}
    if not os.path.exists(path):
        return result
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            ticker = row.get("ticker", "").strip()
            entry_time = parse_time(row.get("entry_date", ""))
            if ticker and entry_time:
                result[ticker] = max(result.get(ticker, entry_time), entry_time)
    return result


def latest_sell_alert_times(path: str) -> dict[str, datetime]:
    return latest_event_times(path, "created_at")


def latest_recommendation_times(path: str) -> dict[str, datetime]:
    return latest_event_times(path, "created_at")


def latest_event_times(path: str, column: str) -> dict[str, datetime]:
    result: dict[str, datetime] = {}
    if not os.path.exists(path):
        return result
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            ticker = row.get("ticker", "").strip()
            event_time = parse_time(row.get(column, ""))
            if ticker and event_time:
                result[ticker] = max(result.get(ticker, event_time), event_time)
    return result


def parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.fromisoformat(value[:10])
        except ValueError:
            return None


def read_tickers(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8-sig") as file:
        return {row.get("ticker", "").strip() for row in csv.DictReader(file) if row.get("ticker")}


def recommend(markets: list[str], top_n: int, min_trading_value: int, volume_multiplier: float, run_id: str | None = None) -> list[Pick]:
    if os.environ.get("DATA_SOURCE", "naver").lower() == "naver":
        return recommend_naver(latest_naver_trading_day(), top_n, min_trading_value, volume_multiplier, run_id)
    return recommend_for_day(latest_trading_day(), markets, top_n, min_trading_value, volume_multiplier)


def _legacy_format_message_1(picks: list[Pick]) -> str:
    if not picks:
        return "오늘 조건에 맞는 관심 종목이 없습니다."
    lines = ["[오늘의 국내주식 관심 종목]"]
    for index, pick in enumerate(picks, 1):
        news = news_bonus(pick.name)
        penalty = performance_penalty(pick.ticker)
        disclosure = dart_bonus(pick.ticker)
        lines.append(f"{index}. {pick.name}({pick.ticker})")
        lines.append(f"- 종가: {pick.close:,}원")
        lines.append(f"- 점수: {pick.score:.1f}")
        if news:
            lines.append(f"- 뉴스 보너스: +{news:.1f}점")
        if disclosure:
            lines.append(f"- 공시 보너스: +{disclosure:.1f}점")
        if penalty:
            lines.append(f"- 성과 감점: -{penalty:.1f}점")
        lines.append(f"- 사유 요약: {reason_summary(pick.volume_ratio, news, disclosure, penalty)}")
        lines.append(f"- 사유: {pick.reason}")
    lines.append("※ 조건 기반 관심 종목 알림이며 투자 자문이 아닙니다.")
    return "\n".join(lines)


def _legacy_reason_summary_1(volume_ratio: float, news: float, disclosure: float, penalty: float) -> str:
    parts = []
    if volume_ratio >= 2:
        parts.append("거래량 급증")
    if news > 0:
        parts.append("뉴스 보너스")
    if disclosure > 0:
        parts.append("공시 보너스")
    if penalty:
        parts.append("성과 감점")
    return " + ".join(parts) or "기본 조건 충족"


def write_log(picks: list[Pick], path: str = "logs/recommendations.csv") -> None:
    from .csv_schema import ensure_header, migrate_recommendation_row
    header = ["created_at", "ticker", "name", "close", "volume_ratio", "trading_value", "score", "volume_score", "trading_value_score", "trend_score", "news_score", "disclosure_score", "performance_penalty"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ensure_header(path, header, migrate_recommendation_row)
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        if not exists:
            writer.writerow(header)
        for pick in picks:
            writer.writerow(
                [
                    datetime.now().isoformat(timespec="seconds"),
                    pick.ticker,
                    pick.name,
                    pick.close,
                    f"{pick.volume_ratio:.2f}",
                    pick.trading_value,
                    f"{pick.score:.2f}",
                    f"{pick.volume_score:.2f}",
                    f"{pick.trading_value_score:.2f}",
                    f"{pick.trend_score:.2f}",
                    f"{pick.news_score:.2f}",
                    f"{pick.disclosure_score:.2f}",
                    f"{pick.performance_penalty:.2f}",
                ]
            )


def track_positions(picks: list[Pick], path: str = POSITIONS_PATH, sell_alerts_path: str = SELL_ALERTS_PATH) -> int:
    if os.environ.get("AUTO_TRACK_PICKS", "1") != "1":
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = active_position_tickers(path, sell_alerts_path)
    exists = os.path.exists(path)
    added = 0
    with open(path, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        if not exists:
            writer.writerow(["ticker", "name", "entry_price", "entry_date"])
        for pick in picks:
            if pick.ticker in existing:
                continue
            writer.writerow([pick.ticker, pick.name, pick.close, date.today().isoformat()])
            added += 1
    return added


def active_position_tickers(path: str = POSITIONS_PATH, sell_alerts_path: str = SELL_ALERTS_PATH) -> set[str]:
    sell_alerts = latest_sell_alert_times(sell_alerts_path)
    result = set()
    for ticker, entry_time in latest_position_times(path).items():
        sell_time = sell_alerts.get(ticker)
        if not sell_time or entry_time > sell_time:
            result.add(ticker)
    return result


def write_error_log(error: BaseException, path: str = "logs/errors.log") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as file:
        file.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] {type(error).__name__}: {error}\n")
        file.write("".join(traceback.format_exception(error)))


def refresh_kakao_token() -> bool:
    import json

    rest_api_key = os.environ.get("KAKAO_REST_API_KEY", "")
    refresh_token = os.environ.get("KAKAO_REFRESH_TOKEN", "")
    if not rest_api_key or not refresh_token:
        return False

    data = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": rest_api_key,
            "refresh_token": refresh_token,
        }
    ).encode()
    request = urllib.request.Request("https://kauth.kakao.com/oauth/token", data=data, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode())

    os.environ["KAKAO_ACCESS_TOKEN"] = body["access_token"]
    save_env_value("KAKAO_ACCESS_TOKEN", body["access_token"])
    if "refresh_token" in body:
        os.environ["KAKAO_REFRESH_TOKEN"] = body["refresh_token"]
        save_env_value("KAKAO_REFRESH_TOKEN", body["refresh_token"])
    return True


def _legacy_pick_reason(pick: Pick) -> str:
    return f"거래량 {pick.volume_ratio:.1f}배, 20일선 상회, 거래대금 {pick.trading_value / 100_000_000:.0f}억원"


def _legacy_reason_summary_2(volume_ratio: float, news: float, disclosure: float, penalty: float) -> str:
    parts = []
    if volume_ratio >= 2:
        parts.append("거래량 급증")
    if news > 0:
        parts.append("뉴스 보너스")
    if disclosure > 0:
        parts.append("공시 보너스")
    if penalty:
        parts.append("성과 감점")
    return " + ".join(parts) or "기본 조건 충족"


def _legacy_format_message_2(picks: list[Pick]) -> str:
    if not picks:
        return "오늘 조건에 맞는 관심 종목이 없습니다."
    lines = ["[오늘의 국내주식 관심 종목]"]
    for index, pick in enumerate(picks, 1):
        news = news_bonus(pick.name)
        penalty = performance_penalty(pick.ticker)
        disclosure = dart_bonus(pick.ticker)
        lines.extend([f"{index}. {pick.name}({pick.ticker})", f"- 종가: {pick.close:,}원", f"- 점수: {pick.score:.1f}"])
        if news:
            lines.append(f"- 뉴스 보너스: +{news:.1f}점")
        if disclosure:
            lines.append(f"- 공시 보너스: +{disclosure:.1f}점")
        if penalty:
            lines.append(f"- 성과 감점: -{penalty:.1f}점")
        lines.append(f"- 사유 요약: {reason_summary(pick.volume_ratio, news, disclosure, penalty)}")
        lines.append(f"- 사유: {pick_reason(pick)}")
    lines.append("조건 기반 관심 종목 알림이며 투자 자문이 아닙니다.")
    return "\n".join(lines)


def pick_reason(pick: Pick) -> str:
    return f"거래량 {pick.volume_ratio:.1f}배, 20일선 상회, 거래대금 {pick.trading_value / 100_000_000:.0f}억원"


def reason_summary(volume_ratio: float, news: float, disclosure: float, penalty: float) -> str:
    parts: list[str] = []
    if volume_ratio >= 2:
        parts.append("거래량 급증")
    if news > 0:
        parts.append("뉴스 보너스")
    if disclosure > 0:
        parts.append("공시 보너스")
    if penalty:
        parts.append("성과 감점")
    return " + ".join(parts) or "기본 조건 충족"


def format_message(picks: list[Pick]) -> str:
    if not picks:
        return "오늘 조건에 맞는 관심 종목이 없습니다."
    lines = ["[오늘의 국내주식 관심 종목]"]
    for index, pick in enumerate(picks, 1):
        lines.extend([f"{index}. {pick.name}({pick.ticker})", f"- 종가: {pick.close:,}원", f"- 점수: {pick.score:.1f}"])
        if pick.news_score:
            lines.append(f"- 뉴스 보너스: +{pick.news_score:.1f}점")
        if pick.disclosure_score:
            lines.append(f"- 공시 보너스: +{pick.disclosure_score:.1f}점")
        if pick.performance_penalty:
            lines.append(f"- 성과 감점: -{pick.performance_penalty:.1f}점")
        lines.append(f"- 사유 요약: {reason_summary(pick.volume_ratio, pick.news_score, pick.disclosure_score, pick.performance_penalty)}")
        lines.append(f"- 사유: {pick_reason(pick)}")
    lines.append("조건 기반 관심 종목 알림이며 투자 자문이 아닙니다.")
    return "\n".join(lines)


def run() -> None:
    load_env()
    if not is_market_alert_time():
        return
    markets = [item.strip() for item in os.environ.get("MARKETS", "KOSPI,KOSDAQ").split(",") if item.strip()]
    top_n = int(os.environ.get("TOP_N", "5"))
    min_trading_value = int(os.environ.get("MIN_TRADING_VALUE", "5000000000"))
    volume_multiplier = float(os.environ.get("VOLUME_MULTIPLIER", "1.5"))
    from .data_store import finish_run, start_run
    market_date = env_date("AS_OF_DATE", date.today()).isoformat()
    run_id = start_run("recommendation", market_date)
    try:
        picks = recommend(markets, top_n, min_trading_value, volume_multiplier, run_id)
        track_positions(picks)
        write_log(picks)
        finish_run(run_id)
    except Exception:
        finish_run(run_id, "failed")
        raise
    if not picks and os.environ.get("SEND_EMPTY_RECOMMENDATION", "0") != "1":
        return
    from .notifier import send_notification

    send_notification(format_message(picks))


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise
