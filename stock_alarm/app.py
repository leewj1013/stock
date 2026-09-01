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
from dataclasses import dataclass, replace
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
    raw_volume_ratio: float = 0
    expected_volume_fraction: float = 1
    atr20_pct: float = 0
    relative_strength_pct: float = 0
    relative_strength_score: float = 0
    financial_score: float = 0
    raw_trading_value: int = 0

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
# Every scheduled entry point runs "python -m stock_alarm.<module>", which only
# needs the package to be importable -- it does NOT guarantee the process's
# working directory is the repo root. A relative ".env" default silently finds
# nothing (os.path.exists returns False, load_env() no-ops without error) the
# moment something invokes a module from elsewhere, so TELEGRAM_BOT_TOKEN and
# friends end up unset with no exception logged anywhere. Anchor to this file's
# location instead so env loading works regardless of caller's cwd.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ENV_PATH = os.path.join(PROJECT_ROOT, ".env")


def load_env(path: str | None = None) -> None:
    path = path or DEFAULT_ENV_PATH
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


def save_env_value(key: str, value: str, path: str | None = None) -> None:
    path = path or DEFAULT_ENV_PATH
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
    ticker: str,
    name: str,
    end_day: date,
    min_trading_value: int,
    volume_multiplier: float,
    record_quality: bool = False,
    price_rows: list[list] | None = None,
    external_lookup: bool = True,
    score_weights: dict[str, float] | None = None,
) -> CandidateEvaluation:
    evaluated_at = datetime.now().isoformat(timespec="seconds")
    base = {"ticker": ticker, "name": name, "evaluated_at": evaluated_at, "passed": 0, "selected": 0}
    rows = price_rows if price_rows is not None else naver_rows(ticker, end_day - timedelta(days=90), end_day)
    if len(rows) < 21:
        return CandidateEvaluation(ticker, name, {**base, "rejection_reasons": "insufficient_history"})
    from .data_quality import validate_price_rows
    quality = validate_price_rows(ticker, rows, end_day)
    if record_quality:
        from .data_store import record_price_quality
        record_price_quality(quality)
    if record_quality and quality["status"] != "valid":
        return CandidateEvaluation(ticker, name, {**base, "rejection_reasons": f"price_{quality['status']}:{quality['reason']}"})

    closes = [int(row[4]) for row in rows[-20:]]
    highs = [int(row[2]) for row in rows[-10:]]
    lows = [int(row[3]) for row in rows[-10:]]
    volumes = [int(row[5]) for row in rows[-21:]]
    today_volume = volumes[-1]
    avg_volume = mean(volumes[:-1])
    close = closes[-1]
    ma20 = mean(closes)
    raw_trading_value = close * today_volume

    if avg_volume <= 0:
        return CandidateEvaluation(ticker, name, {**base, "close": close, "rejection_reasons": "invalid_average_volume"})
    raw_volume_ratio = today_volume / avg_volume
    volume_ratio, volume_fraction = time_adjusted_volume_ratio(raw_volume_ratio, end_day)
    trading_value = int(raw_trading_value / max(volume_fraction, 0.03))
    previous_close = int(rows[-2][4])
    avg_range = average_intraday_range_pct(highs, lows, closes[-10:])
    atr20_pct = average_true_range_pct(rows)
    rejections = []
    day_return = day_change_pct(previous_close, close)
    distance_ma20_pct = (close / ma20 - 1) * 100 if ma20 else 0
    if abs(day_return) > env_float("MAX_DAY_CHANGE_PCT", 8):
        rejections.append("day_change")
    if avg_range > env_float("MAX_AVG_RANGE_PCT", 12):
        rejections.append("average_range")
    if volume_ratio < volume_multiplier:
        rejections.append("volume_ratio")
    if close <= ma20:
        rejections.append("below_ma20")
    if trading_value < min_trading_value:
        rejections.append("trading_value")
    legacy_passed = int(not rejections)
    if day_return > env_float("MAX_ENTRY_DAY_CHANGE_PCT", 5):
        rejections.append("entry_day_change")
    max_distance = min(
        env_float("MAX_MA20_DISTANCE_PCT", 10),
        max(atr20_pct * env_float("MAX_MA20_DISTANCE_ATR", 1.5), 3),
    )
    if distance_ma20_pct > max_distance:
        rejections.append("extended_above_ma20")
    values = {
        **base,
        "close": close,
        "previous_close": previous_close,
        "day_return_pct": day_return,
        "volume": today_volume,
        "avg_volume": avg_volume,
        "raw_volume_ratio": raw_volume_ratio,
        "expected_volume_fraction": volume_fraction,
        "volume_ratio": volume_ratio,
        "raw_trading_value": raw_trading_value,
        "trading_value": trading_value,
        "ma20": ma20,
        "distance_ma20_pct": distance_ma20_pct,
        "avg_range_pct": avg_range,
        "atr20_pct": atr20_pct,
        "legacy_score": calculate_legacy_score(close, ma20, volume_ratio, trading_value, atr20_pct),
        "legacy_passed": legacy_passed,
        "rejection_reasons": ",".join(rejections),
    }
    if rejections:
        return CandidateEvaluation(ticker, name, values)
    name = stock_name(ticker, name)
    volume_score, trading_value_score, trend_score = calculate_score_parts(close, ma20, volume_ratio, trading_value, atr20_pct)
    news_score = news_bonus(name) if external_lookup else 0.0
    disclosure_score = dart_bonus(ticker) if external_lookup else 0.0
    penalty = performance_penalty(ticker) if external_lookup else 0.0
    if external_lookup:
        from .fundamental_reference import snapshot as fundamental_snapshot
        fundamentals = fundamental_snapshot(ticker, end_day)
    else:
        fundamentals = {"financial_score": 0.0, "financial_notes": "disabled_for_point_in_time_backtest"}
    financial_score = float(fundamentals.get("financial_score") or 0)
    score_parts = {
        "volume_score": volume_score, "trading_value_score": trading_value_score, "trend_score": trend_score,
        "news_score": news_score, "disclosure_score": disclosure_score, "financial_score": financial_score,
        "relative_strength_score": 0, "performance_penalty": penalty,
    }
    if score_weights is None:
        from .strategy_learning import adjusted_score
        score = adjusted_score(score_parts)
    else:
        from .strategy_learning import score_with_weights
        score = score_with_weights(score_parts, score_weights)
    legacy_score = calculate_legacy_score(close, ma20, volume_ratio, trading_value, atr20_pct) + news_score + disclosure_score + financial_score - penalty
    pick = Pick(ticker, name, close, volume_ratio, trading_value, round(score, 2), volume_score, trading_value_score, trend_score, news_score, disclosure_score, penalty, raw_volume_ratio, volume_fraction, atr20_pct, 0, 0, financial_score, raw_trading_value)
    values.update(
        name=name,
        volume_score=volume_score,
        trading_value_score=trading_value_score,
        trend_score=trend_score,
        news_score=news_score,
        disclosure_score=disclosure_score,
        performance_penalty=penalty,
        financial_score=financial_score,
        financial_notes=fundamentals.get("financial_notes", ""),
        per=fundamentals.get("per"),
        pbr=fundamentals.get("pbr"),
        dividend_yield=fundamentals.get("dividend_yield"),
        legacy_score=round(max(0.0, min(100.0, legacy_score)), 2),
        final_score=round(score, 2),
        passed=1,
    )
    return CandidateEvaluation(ticker, name, values, pick)


def calculate_score(close: int, ma20: float, volume_ratio: float, trading_value: int, atr20_pct: float = 0) -> float:
    return round(sum(calculate_score_parts(close, ma20, volume_ratio, trading_value, atr20_pct)), 2)


def calculate_score_parts(close: int, ma20: float, volume_ratio: float, trading_value: int, atr20_pct: float = 0) -> tuple[float, float, float]:
    volume_score = min(volume_ratio / 2, 1) * 40
    trading_value_score = min(trading_value / 300_000_000_000, 1) * 30
    distance_pct = max(close / ma20 - 1, 0) * 100
    trend_scale = max(atr20_pct, 2) if atr20_pct else 2
    normalized_distance = distance_pct / trend_scale
    trend_score = max(0, 1 - abs(normalized_distance - 0.75) / 1.25) * 30
    return round(volume_score, 2), round(trading_value_score, 2), round(trend_score, 2)


def calculate_legacy_score(close: int, ma20: float, volume_ratio: float, trading_value: int, atr20_pct: float = 0) -> float:
    """Return the v3 technical score so v4 can be evaluated in shadow mode."""
    volume_score = min(volume_ratio / 3, 1) * 45
    trading_value_score = min(trading_value / 300_000_000_000, 1) * 35
    distance_pct = max(close / ma20 - 1, 0) * 100
    trend_scale = max(atr20_pct * 3, 10) if atr20_pct else 10
    trend_score = min(distance_pct / trend_scale, 1) * 20
    return round(volume_score + trading_value_score + trend_score, 2)


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
    values: list[float] = []
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            if row.get("ticker") != ticker:
                continue
            weighted = []
            for column, weight in (("return_1d_pct", 0.2), ("return_3d_pct", 0.3), ("return_5d_pct", 0.5)):
                if row.get(column):
                    weighted.append((float(row[column]), weight))
            if weighted:
                values.append(sum(value * weight for value, weight in weighted) / sum(weight for _value, weight in weighted))
    minimum = int(env_float("PERFORMANCE_MIN_SAMPLES", 20))
    if len(values) < minimum:
        return 0
    recent = values[-60:]
    avg = mean(recent)
    shrinkage = len(recent) / (len(recent) + 20)
    return min(abs(avg) * shrinkage, 10) if avg < 0 else 0


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


def expected_volume_fraction(now: datetime | None = None) -> float:
    now = now or datetime.now()
    market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now <= market_open:
        return 0.03
    if now >= market_close:
        return 1.0
    elapsed = (now - market_open).total_seconds() / (market_close - market_open).total_seconds()
    # Korean equities usually trade more heavily near the open and close. This
    # deterministic curve is retained in each snapshot and can later be replaced
    # by an empirically learned profile without changing the raw volume history.
    if elapsed <= 0.15:
        return 0.03 + elapsed / 0.15 * 0.22
    if elapsed <= 0.75:
        return 0.25 + (elapsed - 0.15) / 0.60 * 0.45
    return 0.70 + (elapsed - 0.75) / 0.25 * 0.30


def time_adjusted_volume_ratio(raw_ratio: float, end_day: date, now: datetime | None = None) -> tuple[float, float]:
    now = now or datetime.now()
    fraction = expected_volume_fraction(now) if end_day == now.date() else 1.0
    return raw_ratio / max(fraction, 0.03), fraction


def average_true_range_pct(rows: list[list], periods: int = 20) -> float:
    sample = rows[-(periods + 1):]
    if len(sample) < 2:
        return 0.0
    ranges = []
    for previous, current in zip(sample, sample[1:]):
        previous_close = int(previous[4])
        high, low, close = int(current[2]), int(current[3]), int(current[4])
        if close:
            true_range = abs(close - previous_close) if high <= 0 or low <= 0 else max(high - low, abs(high - previous_close), abs(low - previous_close))
            ranges.append(true_range / close * 100)
    return mean(ranges) if ranges else 0.0


def passes_risk_filters(previous_close: int, close: int, highs: list[int], lows: list[int], closes: list[int]) -> bool:
    max_day_change = env_float("MAX_DAY_CHANGE_PCT", 8)
    max_range = env_float("MAX_AVG_RANGE_PCT", 12)
    if abs(day_change_pct(previous_close, close)) > max_day_change:
        return False
    return average_intraday_range_pct(highs, lows, closes) <= max_range


def market_up_ratio(moves: list[bool]) -> float:
    return sum(moves) / len(moves) if moves else 0


def market_exposure_limit_pct(up_ratio: float) -> float:
    """Return the live 4.3 portfolio exposure cap for a breadth observation."""
    return 70.0 if up_ratio >= 0.60 else 40.0 if up_ratio >= 0.45 else 10.0


def watchlist_market_up_ratio(end_day: date) -> float:
    moves = []
    for ticker in configured_stocks():
        rows = naver_rows(ticker, end_day - timedelta(days=10), end_day)
        if len(rows) >= 2:
            moves.append(int(rows[-1][4]) > int(rows[-2][4]))
    return market_up_ratio(moves)


def naver_market_up_ratio(end_day: date) -> float:
    """Whole KOSPI+KOSDAQ advance/decline ratio, falling back to the watchlist
    approximation if the market-cap page scrape is unavailable."""
    from .market_breadth import cached_whole_market_up_ratio

    ratio = cached_whole_market_up_ratio()
    return ratio if ratio is not None else watchlist_market_up_ratio(end_day)


def passes_market_filter(end_day: date) -> bool:
    minimum = env_float("MIN_MARKET_UP_RATIO", 0.45)
    return naver_market_up_ratio(end_day) >= minimum


def market_benchmark_return(end_day: date) -> tuple[str, float | None]:
    """The relative-strength baseline: a single index ticker only reflects its
    (often large-cap-heavy) constituents, while the whole-market mean %-change
    covers every listed stock. Only fall back to the ticker when no custom
    MARKET_BENCHMARK_TICKER override is set and the whole-market figure is
    unavailable, so an explicit override is still honored as-is.
    """
    symbol = os.environ.get("MARKET_BENCHMARK_TICKER", "KOSPI")
    if symbol == "KOSPI":
        from .market_breadth import cached_whole_market_average_change_pct

        whole_market = cached_whole_market_average_change_pct()
        if whole_market is not None:
            return "WHOLE_MARKET", whole_market
    try:
        rows = naver_rows(symbol, end_day - timedelta(days=10), end_day)
        if len(rows) >= 2 and float(rows[-2][4]):
            return symbol, (float(rows[-1][4]) - float(rows[-2][4])) / float(rows[-2][4]) * 100
    except Exception:
        pass
    return symbol, None


def apply_relative_strength(
    evaluations: list[CandidateEvaluation],
    benchmark: tuple[str, float | None] | None = None,
    score_weights: dict[str, float] | None = None,
) -> list[CandidateEvaluation]:
    returns = [float(item.values["day_return_pct"]) for item in evaluations if item.values.get("day_return_pct") is not None]
    symbol, benchmark_value = benchmark or ("WATCHLIST", None)
    benchmark_value = benchmark_value if benchmark_value is not None else (mean(returns) if returns else 0.0)
    adjusted: list[CandidateEvaluation] = []
    for item in evaluations:
        values = dict(item.values)
        relative = float(values.get("day_return_pct") or 0) - benchmark_value
        # A bounded +/-5 point adjustment prevents one volatile session from
        # overwhelming liquidity and trend quality.
        relative_score = max(-5.0, min(5.0, relative / 2))
        values.update(benchmark_symbol=symbol, market_proxy_return_pct=benchmark_value, relative_strength_pct=relative, relative_strength_score=relative_score)
        pick = item.pick
        if pick:
            parts = {
                "volume_score": pick.volume_score, "trading_value_score": pick.trading_value_score,
                "trend_score": pick.trend_score, "news_score": pick.news_score,
                "disclosure_score": pick.disclosure_score, "financial_score": pick.financial_score,
                "relative_strength_score": relative_score, "performance_penalty": pick.performance_penalty,
            }
            if score_weights is None:
                from .strategy_learning import adjusted_score
                score = adjusted_score(parts)
            else:
                from .strategy_learning import score_with_weights
                score = score_with_weights(parts, score_weights)
            pick = replace(pick, score=round(score, 2), relative_strength_pct=relative, relative_strength_score=relative_score)
            values["final_score"] = round(score, 2)
        if values.get("legacy_score") is not None:
            values["legacy_score"] = round(max(0.0, min(100.0, float(values["legacy_score"]) + relative_score)), 2)
        adjusted.append(CandidateEvaluation(item.ticker, item.name, values, pick))
    return adjusted


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


def recommend_universe(min_trading_value: int) -> dict[str, str]:
    """The watchlist, optionally widened with today's top-trading-value stocks
    market-wide. Opt-in via DYNAMIC_SCREENING_TOP_N (default 0/off) since it
    changes which tickers ever get a chance at a recommendation -- every
    candidate here, static or dynamic, still has to clear the same
    liquidity/technical/quality filters in evaluate_naver_candidate().
    """
    stocks = dict(configured_stocks())
    top_n = int(env_float("DYNAMIC_SCREENING_TOP_N", 0))
    if top_n > 0:
        from .market_breadth import krx_top_trading_value_candidates
        try:
            for ticker, name in krx_top_trading_value_candidates(top_n, min_trading_value).items():
                stocks.setdefault(ticker, name)
        except Exception:
            pass
    return stocks


def recommend_naver(end_day: date, top_n: int, min_trading_value: int, volume_multiplier: float, run_id: str | None = None) -> list[Pick]:
    universe = recommend_universe(min_trading_value)
    if not passes_market_filter(end_day):
        if run_id:
            from .data_store import write_candidates
            write_candidates(run_id, ({"ticker": ticker, "name": name, "evaluated_at": datetime.now().isoformat(timespec="seconds"), "passed": 0, "selected": 0, "rejection_reasons": "market_filter"} for ticker, name in universe.items()))
        return []
    picks = []
    evaluations = []
    for ticker, name in universe.items():
        if run_id:
            evaluation = evaluate_naver_candidate(ticker, name, end_day, min_trading_value, volume_multiplier, record_quality=True)
            evaluations.append(evaluation)
            pick = evaluation.pick
        else:
            pick = make_naver_pick(ticker, name, end_day, min_trading_value, volume_multiplier)
        if pick:
            picks.append(pick)
    if run_id:
        evaluations = apply_relative_strength(evaluations, market_benchmark_return(end_day))
        picks = [evaluation.pick for evaluation in evaluations if evaluation.pick]
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


def sell_cooldown_days(reason: str, stage: str = "") -> float:
    """Recommendation cooldown length after a sell alert, tiered by how strong
    a signal it was: a stop-loss means the setup was wrong and should sit out
    longer, while a weak MA20-only break or a profit-taking exit can be
    reconsidered sooner.
    """
    reason = reason or ""
    if (stage or "").strip().startswith("take_profit"):
        return env_float("SELL_RECOMMEND_COOLDOWN_TAKE_PROFIT_DAYS", 1)
    if "손절 기준" in reason:
        return env_float("SELL_RECOMMEND_COOLDOWN_STOP_LOSS_DAYS", 5)
    if "기대수익 미달" in reason:
        return env_float("SELL_RECOMMEND_COOLDOWN_TIME_STOP_DAYS", 3)
    if "20일선" in reason:
        return env_float("SELL_RECOMMEND_COOLDOWN_MA20_DAYS", 2)
    return env_float("SELL_RECOMMEND_COOLDOWN_DAYS", 3)


def open_recommended_tickers(
    positions_path: str = POSITIONS_PATH,
    sell_alerts_path: str = SELL_ALERTS_PATH,
    recommendations_path: str = "logs/recommendations.csv",
) -> set[str]:
    sell_events = latest_full_sell_events(sell_alerts_path)
    sell_alerts = {ticker: event["time"] for ticker, event in sell_events.items()}
    result = set()
    now = datetime.now()
    for ticker, event in sell_events.items():
        cooldown_days = sell_cooldown_days(event.get("reason", ""), event.get("stage", ""))
        if event["time"] >= now - timedelta(days=cooldown_days):
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


def latest_full_sell_events(path: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    if not os.path.exists(path):
        return result
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            # A first-stage take profit keeps the recommendation episode open.
            if (row.get("sale_type") or "full").strip().lower() == "partial":
                continue
            ticker = row.get("ticker", "").strip()
            event_time = parse_time(row.get("created_at", ""))
            if not ticker or not event_time:
                continue
            existing = result.get(ticker)
            if existing is None or event_time > existing["time"]:
                result[ticker] = {"time": event_time, "reason": row.get("reason", ""), "stage": row.get("stage", "")}
    return result


def latest_sell_alert_times(path: str) -> dict[str, datetime]:
    return {ticker: event["time"] for ticker, event in latest_full_sell_events(path).items()}


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


def recommend(markets: list[str], top_n: int, min_trading_value: int, volume_multiplier: float, run_id: str | None = None) -> list[Pick]:
    if os.environ.get("DATA_SOURCE", "naver").lower() == "naver":
        return recommend_naver(latest_naver_trading_day(), top_n, min_trading_value, volume_multiplier, run_id)
    return recommend_for_day(latest_trading_day(), markets, top_n, min_trading_value, volume_multiplier)


def write_log(picks: list[Pick], path: str = "logs/recommendations.csv") -> None:
    from .csv_schema import ensure_header, migrate_recommendation_row
    header = ["created_at", "ticker", "name", "close", "volume_ratio", "trading_value", "score", "volume_score", "trading_value_score", "trend_score", "news_score", "disclosure_score", "performance_penalty", "raw_volume_ratio", "expected_volume_fraction", "atr20_pct", "relative_strength_pct", "relative_strength_score", "financial_score", "raw_trading_value", "allocation_pct"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ensure_header(path, header, migrate_recommendation_row)
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        if not exists:
            writer.writerow(header)
        allocations = allocation_percentages(picks)
        for pick, allocation in zip(picks, allocations):
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
                    f"{pick.raw_volume_ratio:.2f}",
                    f"{pick.expected_volume_fraction:.4f}",
                    f"{pick.atr20_pct:.2f}",
                    f"{pick.relative_strength_pct:.2f}",
                    f"{pick.relative_strength_score:.2f}",
                    f"{pick.financial_score:.2f}",
                    pick.raw_trading_value,
                    f"{allocation:.2f}",
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


def historical_allocation_factors(path: str = "logs/recommendation_performance.csv") -> dict[str, float]:
    """Return conservative per-ticker multipliers learned from completed outcomes."""
    if not os.path.exists(path):
        return {}
    samples: dict[str, list[float]] = {}
    with open(path, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            returns = []
            for column, weight in (("return_1d_pct", 0.2), ("return_3d_pct", 0.3), ("return_5d_pct", 0.5)):
                try:
                    returns.append((float(row.get(column, "")), weight))
                except ValueError:
                    continue
            if returns and row.get("ticker"):
                samples.setdefault(row["ticker"], []).append(sum(value * weight for value, weight in returns) / sum(weight for _, weight in returns))
    factors = {}
    for ticker, values in samples.items():
        # Bayesian-style shrinkage prevents a handful of wins or losses from dominating.
        learned_return = sum(values) / (len(values) + 5)
        factors[ticker] = max(0.5, min(1.5, 1 + learned_return / 20))
    return factors


def allocation_percentages(picks: list[Pick], performance_path: str = "logs/recommendation_performance.csv") -> list[float]:
    """Return per-position target weights against total virtual-account equity."""
    if not picks:
        return []
    if os.environ.get("VIRTUAL_TRADER_POSITION_SIZING_MODE", "fixed").strip().lower() == "fixed":
        fixed = max(0.0, min(100.0, env_float("VIRTUAL_TRADER_FIXED_POSITION_PCT", 10)))
        return [round(fixed, 2) for _pick in picks]
    learned = historical_allocation_factors(performance_path)
    minimum = max(0.0, min(100.0, env_float("VIRTUAL_TRADER_MIN_POSITION_PCT", 10)))
    maximum = max(minimum, min(100.0, env_float("VIRTUAL_TRADER_MAX_POSITION_PCT", 30)))
    allocations = []
    for pick in picks:
        quality = max(0.25, min(1.0, pick.score / 100))
        volatility = max(0.5, min(1.0, 3 / max(pick.atr20_pct, 3)))
        target = maximum * quality * volatility * learned.get(pick.ticker, 1.0)
        allocations.append(round(max(minimum, min(maximum, target)), 2))
    total = sum(allocations)
    if total > 100:
        allocations = [round(value / total * 100, 2) for value in allocations]
    return allocations


def correlation_limited_allocations(
    picks: list[Pick],
    allocations: list[float],
    end_day: date | None = None,
    price_rows_by_ticker: dict[str, list[list]] | None = None,
    locked_tickers: set[str] | None = None,
    threshold_override: float | None = None,
    group_cap_override: float | None = None,
    minimum_override: float | None = None,
) -> list[float]:
    """Cap connected groups of highly correlated positions at the live limit.

    ``price_rows_by_ticker`` lets isolated replay paths inject point-in-time
    history while live execution keeps using Naver. ``locked_tickers`` are
    existing holdings: their weights are never reduced here, but they consume
    group capacity and can block a new allocation.
    """
    if len(picks) < 2:
        return allocations
    end_day = end_day or date.today()
    lookback = int(env_float("CORRELATION_LOOKBACK_DAYS", 60))
    threshold = env_float("CORRELATION_LIMIT", 0.8) if threshold_override is None else float(threshold_override)
    group_cap = env_float("CORRELATED_GROUP_MAX_PCT", 40) if group_cap_override is None else float(group_cap_override)
    series = {}
    for pick in picks:
        if price_rows_by_ticker is not None:
            rows = price_rows_by_ticker.get(pick.ticker, [])
        else:
            try:
                rows = naver_rows(pick.ticker, end_day - timedelta(days=lookback * 2), end_day)
            except Exception:
                rows = []
        closes = [float(row[4]) for row in rows[-(lookback + 1):] if float(row[4]) > 0]
        series[pick.ticker] = [(current - previous) / previous for previous, current in zip(closes, closes[1:])]
    limited = list(allocations)
    adjacency = {index: set() for index in range(len(picks))}
    for left in range(len(picks)):
        for right in range(left + 1, len(picks)):
            first, second = series.get(picks[left].ticker, []), series.get(picks[right].ticker, [])
            size = min(len(first), len(second))
            if size < 20:
                continue
            xs, ys = first[-size:], second[-size:]
            xbar, ybar = mean(xs), mean(ys)
            numerator = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
            denominator = (sum((x - xbar) ** 2 for x in xs) * sum((y - ybar) ** 2 for y in ys)) ** 0.5
            correlation = numerator / denominator if denominator else 0
            if correlation >= threshold:
                adjacency[left].add(right)
                adjacency[right].add(left)
    locked_tickers = locked_tickers or set()
    visited = set()
    minimum = env_float("VIRTUAL_TRADER_MIN_POSITION_PCT", 10) if minimum_override is None else float(minimum_override)
    for start in range(len(picks)):
        if start in visited or not adjacency[start]:
            continue
        stack, component = [start], []
        while stack:
            index = stack.pop()
            if index in visited:
                continue
            visited.add(index)
            component.append(index)
            stack.extend(adjacency[index] - visited)
        locked = [index for index in component if picks[index].ticker in locked_tickers]
        unlocked = sorted(index for index in component if picks[index].ticker not in locked_tickers)
        remaining = max(0.0, group_cap - sum(limited[index] for index in locked))
        for index in unlocked:
            allowed = min(limited[index], remaining)
            limited[index] = round(allowed, 2) if allowed >= minimum else 0.0
            remaining = max(0.0, remaining - limited[index])
    return limited


def sector_limited_allocations(
    picks: list[Pick], allocations: list[float], end_day: date | None = None,
    sector_by_ticker: dict[str, str] | None = None, locked_tickers: set[str] | None = None,
    group_cap_override: float | None = None, minimum_override: float | None = None,
    cache_path: str | None = None, refresh_mapping: bool = False,
) -> list[float]:
    """Cap each Naver industry without increasing any prior allocation.

    Existing holdings are locked: they consume sector capacity but are never
    sold by this entry constraint. Missing classifications are isolated per
    ticker instead of being incorrectly grouped into one unknown sector.
    """
    if len(picks) != len(allocations):
        raise ValueError("picks and allocations must have the same length")
    group_cap = env_float("SECTOR_GROUP_MAX_PCT", 100) if group_cap_override is None else float(group_cap_override)
    if not picks or group_cap >= 100:
        return list(allocations)
    if sector_by_ticker is None:
        from pathlib import Path
        from .sector_reference import DEFAULT_CACHE, load_sector_mapping
        sector_by_ticker = load_sector_mapping({pick.ticker for pick in picks}, Path(cache_path) if cache_path else DEFAULT_CACHE, refresh_mapping)
    locked_tickers = locked_tickers or set()
    minimum = env_float("VIRTUAL_TRADER_MIN_POSITION_PCT", 10) if minimum_override is None else float(minimum_override)
    groups: dict[str, list[int]] = {}
    for index, pick in enumerate(picks):
        sector = sector_by_ticker.get(pick.ticker) or f"__unclassified__:{pick.ticker}"
        groups.setdefault(sector, []).append(index)
    limited = list(allocations)
    for indexes in groups.values():
        remaining = max(0.0, group_cap - sum(limited[index] for index in indexes if picks[index].ticker in locked_tickers))
        for index in (index for index in indexes if picks[index].ticker not in locked_tickers):
            allowed = min(limited[index], remaining)
            limited[index] = round(allowed, 2) if allowed >= minimum else 0.0
            remaining = max(0.0, remaining - limited[index])
    return limited


def auto_buy_virtual_trader(picks: list[Pick], path: str = "data/stock_alarm.db", sector_cap_override: float | None = None) -> dict | None:
    if not picks or os.environ.get("VIRTUAL_TRADER_AUTO_BUY", "1") != "1":
        return None
    from .data_store import virtual_buy, virtual_trader_state
    from .portfolio_risk import new_buys_allowed
    state = virtual_trader_state(path=path)
    if state.get("cash", 0) <= 0:
        return None
    allowed, _reason = new_buys_allowed(path=path)
    if not allowed:
        return None
    allocations = allocation_percentages(picks)
    allocations = correlation_limited_allocations(picks, allocations)
    sector_cap = env_float("SECTOR_GROUP_MAX_PCT", 100) if sector_cap_override is None else sector_cap_override
    if sector_cap < 100:
        existing = [Pick(row["ticker"], row["name"], int(row["current_price"]), 0, 0, 0) for row in state.get("holdings", [])]
        equity = float(state.get("total_equity") or 0)
        existing_allocations = [float(row["valuation"]) / equity * 100 if equity else 0.0 for row in state.get("holdings", [])]
        combined = existing + picks
        limited = sector_limited_allocations(combined, existing_allocations + allocations, locked_tickers={pick.ticker for pick in existing}, group_cap_override=sector_cap)
        allocations = limited[len(existing):]
    breadth = naver_market_up_ratio(date.today())
    exposure_limit = market_exposure_limit_pct(breadth)
    candidates = [
        {"ticker": pick.ticker, "name": pick.name, "close": pick.close, "score": pick.score, "allocation_pct": allocation,
         "portfolio_limit_pct": exposure_limit, "price_quality": "valid"}
        for pick, allocation in zip(picks, allocations)
    ]
    try:
        return virtual_buy(candidates, path=path)
    except ValueError:
        return None


def format_message(picks: list[Pick], virtual_result: dict | None = None) -> str:
    if not picks:
        return "오늘 조건에 맞는 관심 종목이 없습니다."
    lines = [
        f"[매수 추천 · {datetime.now().strftime('%H:%M')}]",
        f"추천 종목: {len(picks)}개",
        "가상투자 비중: 전체 가상계좌 자산 기준 종목별 목표 비중",
    ]
    allocations = allocation_percentages(picks)
    for index, (pick, allocation) in enumerate(zip(picks, allocations), 1):
        signal = reason_summary(pick.volume_ratio, pick.news_score, pick.disclosure_score, pick.performance_penalty)
        lines.extend([
            "",
            f"{index}. {pick.name}({pick.ticker})",
            f"현재가 {pick.close:,}원 · 가상투자 예정 {allocation:.2f}% · 점수 {pick.score:.1f}",
            f"신호: {signal} · 거래량 {pick.volume_ratio:.1f}배",
        ])
        risk = []
        if pick.atr20_pct:
            risk.append(f"ATR {pick.atr20_pct:.2f}%")
        if pick.relative_strength_pct:
            risk.append(f"상대강도 {pick.relative_strength_pct:+.2f}%p")
        if risk:
            lines.append("위험/강도: " + " · ".join(risk))
    executions = {row["ticker"]: row for row in (virtual_result or {}).get("executions", [])}
    if executions:
        lines.extend(["", "■ 가상 자동매수"])
        for pick in picks:
            execution = executions.get(pick.ticker)
            if execution:
                lines.append(f"- {pick.name}: {execution['quantity']:,}주 · {execution['cost']:,}원")
        lines.append(f"총 매수 {virtual_result.get('spent', 0):,}원 · 잔여 현금 {virtual_result.get('cash', 0):,}원")
    elif virtual_result is None:
        lines.extend(["", "가상 자동매수: 잔액 없음 또는 비활성"])
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
        virtual_result = auto_buy_virtual_trader(picks)
        # Secondary virtual-trader profiles buy the same picks with different
        # sizing/exit rules -- they don't get their own recommend() call or
        # tracked-position log entry, only their own DB and buy allocation.
        from .trading_profiles import PROFILES
        for name, profile in PROFILES.items():
            if name == "aggressive":
                continue
            auto_buy_virtual_trader(picks, path=profile["db_path"], sector_cap_override=profile["sector_cap_pct"])
        finish_run(run_id)
    except Exception:
        finish_run(run_id, "failed")
        raise
    if not picks and os.environ.get("SEND_EMPTY_RECOMMENDATION", "0") != "1":
        return
    from .notifier import send_notification

    send_notification(format_message(picks, virtual_result), event_type="recommendation", tickers=[pick.ticker for pick in picks])


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise
