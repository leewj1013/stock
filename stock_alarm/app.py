from __future__ import annotations

import csv
import json
import os
import ast
import traceback
import urllib.parse
import urllib.request
import time as time_module
import tempfile
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


def workspace_root(project_root: str = PROJECT_ROOT) -> str:
    # Scheduled tasks run a copy of this package outside the workspace
    # (scripts/deploy_secure_runtime.ps1), which has no .env of its own --
    # follow its workspace.path back so non-secret settings still load.
    # utf-8-sig: PowerShell 5.1's Set-Content -Encoding UTF8 writes a BOM.
    try:
        with open(os.path.join(project_root, "workspace.path"), encoding="utf-8-sig") as file:
            return file.read().strip() or project_root
    except OSError:
        return project_root


DEFAULT_ENV_PATH = os.path.join(workspace_root(), ".env")
DEFAULT_SECURE_ENV_PATH = os.path.join(
    os.path.expanduser("~"),
    ".stockAlarmSecure",
    "secrets.env",
)

# Credentials are deliberately kept outside the developer workspace.  The
# workspace is writable by development tools, while the secure file is
# provisioned with a user/SYSTEM-only ACL by scripts/migrate_secrets.ps1.
SENSITIVE_ENV_KEYS = {
    "DART_API_KEY",
    "DASHBOARD_LOCAL_TOKEN",
    "DASHBOARD_LOCAL_USERNAME",
    "DASHBOARD_LOCAL_PASSWORD_HASH",
    "DASHBOARD_REMOTE_TOKEN",
    "KAKAO_ACCESS_TOKEN",
    "KAKAO_JAVASCRIPT_KEY",
    "KAKAO_NATIVE_APP_KEY",
    "KAKAO_REFRESH_TOKEN",
    "KAKAO_REST_API_KEY",
    "KRX_API_KEY",
    "KRX_ID",
    "KRX_PW",
    "NAVER_ACCESS_KEY_ID",
    "NAVER_HUB_CLIENT_ID",
    "NAVER_HUB_CLIENT_SECRET",
    "NAVER_SECRET_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TOSS_CLIENT_ID",
    "TOSS_CLIENT_SECRET",
}


def secure_env_path() -> str:
    return os.environ.get("STOCK_ALARM_SECURE_ENV_PATH") or DEFAULT_SECURE_ENV_PATH


def _load_env_file(path: str, *, override: bool, skip_sensitive: bool = False) -> None:
    try:
        with open(path, encoding="utf-8") as file:
            for raw in file:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if skip_sensitive and key in SENSITIVE_ENV_KEYS:
                    continue
                if override:
                    os.environ[key] = value.strip()
                else:
                    os.environ.setdefault(key, value.strip())
    except (FileNotFoundError, PermissionError):
        return


def load_env(path: str | None = None) -> None:
    os.makedirs(".cache/matplotlib", exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", os.path.abspath(".cache/matplotlib"))
    if path is not None:
        _load_env_file(path, override=False)
        return
    _load_env_file(DEFAULT_ENV_PATH, override=False, skip_sensitive=True)
    # The protected file is authoritative for credentials, so a stale value
    # left in the developer environment cannot silently override it.
    _load_env_file(secure_env_path(), override=True)


def save_env_values(values: dict[str, str], path: str | None = None, *, remove_keys: set[str] | None = None) -> None:
    protected_default = path is None and any(key in SENSITIVE_ENV_KEYS for key in values)
    if protected_default and any(key not in SENSITIVE_ENV_KEYS for key in values):
        raise ValueError("sensitive and non-sensitive settings must be saved separately")
    path = path or (secure_env_path() if protected_default else DEFAULT_ENV_PATH)
    if protected_default and not os.path.exists(path):
        raise RuntimeError("protected credential store is missing; run scripts/migrate_secrets.ps1 first")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    lines: list[str] = []
    found: set[str] = set()
    remove_keys = remove_keys or set()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            lines = file.readlines()
    output: list[str] = []
    for raw in lines:
        current_key = raw.split("=", 1)[0].strip() if "=" in raw and not raw.lstrip().startswith("#") else ""
        if current_key in remove_keys:
            continue
        if current_key in values:
            output.append(f"{current_key}={values[current_key]}\n")
            found.add(current_key)
        else:
            output.append(raw)
    for key, value in values.items():
        if key not in found:
            output.append(f"{key}={value}\n")
    descriptor, temporary_path = tempfile.mkstemp(prefix=".stockalarm-env-", dir=os.path.dirname(os.path.abspath(path)), text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.writelines(output)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


def save_env_value(key: str, value: str, path: str | None = None) -> None:
    save_env_values({key: value}, path)


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
    # 140 calendar days comfortably covers a 63-trading-day (~3 month) window
    # for price_momentum_pct below, on top of the shorter windows (MA20, ATR)
    # everything else here only ever reads from the tail of `rows`.
    rows = price_rows if price_rows is not None else naver_rows(ticker, end_day - timedelta(days=140), end_day)
    used_toss_rows = False
    if price_rows is None and external_lookup and len(rows) < 21:
        # Naver came back empty/too-short (seen this session: SSL timeouts,
        # zero-filled placeholder rows) -- try Toss's official candles once
        # before giving up. Point-in-time backtests (price_rows supplied)
        # never take this path.
        from .toss_client import TossClient, candles_to_naver_rows
        try:
            fallback_rows = candles_to_naver_rows(TossClient().candles(ticker, count=140).get("candles", []))
        except Exception:
            fallback_rows = []
        if fallback_rows:
            rows, used_toss_rows = fallback_rows, True
    if len(rows) < 21:
        return CandidateEvaluation(ticker, name, {**base, "rejection_reasons": "insufficient_history"})
    from .data_quality import validate_price_rows
    reference_close, reference_source = None, ""
    if price_rows is None and external_lookup and not used_toss_rows:
        # Cross-check naver's close against Toss's independent candle feed
        # on every live evaluation, not just when naver fails outright --
        # catches a wrong-but-well-formed naver row (mismatched split/adjust,
        # stale cache) that would otherwise pass validate_price_rows() clean.
        from .toss_client import latest_close_for
        reference_close = latest_close_for(ticker)
        reference_source = "toss" if reference_close else ""
    quality = validate_price_rows(
        ticker, rows, end_day, allow_external_lookup=external_lookup,
        reference_close=reference_close, reference_source=reference_source,
    )
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
    momentum_1m_pct = price_momentum_pct(rows, 21)
    momentum_3m_pct = price_momentum_pct(rows, 63)
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
    if external_lookup:
        from .toss_client import blocking_warnings_for
        blocking = blocking_warnings_for(ticker)
        if blocking:
            return CandidateEvaluation(ticker, name, {**values, "rejection_reasons": f"stock_warning:{','.join(sorted(blocking))}"})
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
    if external_lookup:
        from .financial_statement_reference import financial_ratios
        ratios = financial_ratios(ticker)
    else:
        ratios = {}
    profile_categories = category_scores(
        ratios, financial_score, disclosure_score, float(fundamentals.get("dividend_yield") or 0), atr20_pct,
    )
    profile_categories["news_category_score"] = round(_scale(news_score, -3, 3), 2)
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
        **profile_categories,
        momentum_1m_pct=momentum_1m_pct,
        momentum_3m_pct=momentum_3m_pct,
        # Filled in by apply_relative_strength() once the benchmark-relative
        # move (needed for momentum) is known; 50 is a neutral placeholder.
        momentum_score=50.0,
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


def _scale(value: float | None, low: float, high: float, default: float = 50.0) -> float:
    """Linearly map value from [low, high] to [0, 100], clamped. None (data
    unavailable) maps to a neutral 50 rather than penalizing or rewarding."""
    if value is None or high == low:
        return default
    return max(0.0, min(100.0, (value - low) / (high - low) * 100))


def _avg_known(*values: float | None) -> float:
    known = [value for value in values if value is not None]
    return round(mean(known), 2) if known else 50.0


def category_scores(
    financial_ratios: dict,
    financial_score: float,
    disclosure_score_raw: float,
    dividend_yield: float,
    atr20_pct: float,
) -> dict[str, float]:
    """Six 0-100 profile-scoring categories, independent of the existing
    quality-gate score (volume/trading_value/trend/etc). Real DART financial
    figures are preferred; when DART_FINANCIALS_LOOKUP is off or a filing
    isn't available yet, each category falls back to the closest signal
    already computed elsewhere in the pipeline so profile ranking still works
    without the new data.
    """
    roe, operating_margin = financial_ratios.get("roe_pct"), financial_ratios.get("operating_margin_pct")
    if roe is None and operating_margin is None:
        profitability_score = max(0.0, min(100.0, financial_score / 5 * 100))
    else:
        profitability_score = _avg_known(_scale(roe, 0, 20) if roe is not None else None,
                                          _scale(operating_margin, 0, 20) if operating_margin is not None else None)
    revenue_growth, oi_growth = financial_ratios.get("revenue_growth_pct"), financial_ratios.get("operating_income_growth_pct")
    if revenue_growth is None and oi_growth is None:
        growth_score = _scale(disclosure_score_raw, -3, 3)
    else:
        growth_score = _avg_known(_scale(revenue_growth, -10, 30) if revenue_growth is not None else None,
                                   _scale(oi_growth, -10, 30) if oi_growth is not None else None)
    debt_ratio = financial_ratios.get("debt_ratio_pct")
    debt_component = 100.0 - _scale(debt_ratio, 0, 200) if debt_ratio is not None else 50.0
    volatility_component = 100.0 - _scale(atr20_pct, 0, 10)
    stability_score = mean([debt_component, volatility_component])
    dividend_score = _scale(dividend_yield, 0, 5)
    return {
        "profitability_score": round(profitability_score, 2),
        "growth_score": round(growth_score, 2),
        "stability_score": round(stability_score, 2),
        "dividend_score": round(dividend_score, 2),
    }


def price_momentum_pct(rows: list[list], trading_days: int) -> float | None:
    """% price change over the last `trading_days` sessions, or None if there
    isn't enough history yet (e.g. a recently-listed ticker)."""
    if len(rows) <= trading_days:
        return None
    recent, past = float(rows[-1][4]), float(rows[-1 - trading_days][4])
    return (recent / past - 1) * 100 if past else None


def momentum_score(
    relative_strength_score: float, volume_ratio: float, trend_score: float,
    momentum_1m_pct: float | None = None, momentum_3m_pct: float | None = None,
) -> float:
    # 1-day relative strength reacts fast but is noisy; the 1/3-month returns
    # (21/63 trading days -- the classic momentum-factor windows) capture a
    # sturdier trend that's less prone to single-day whipsaw.
    components = [
        _scale(relative_strength_score, -5, 5),
        _scale(volume_ratio, 0, 3),
        _scale(trend_score, 0, 30),
        _scale(momentum_1m_pct, -20, 20),
        _scale(momentum_3m_pct, -30, 30),
    ]
    return round(mean(components), 2)


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
    return 100.0 if up_ratio >= 0.60 else 40.0 if up_ratio >= 0.45 else 10.0


def effective_exposure_limit_pct(up_ratio: float, profile_limit_pct: float | None = None, regime_multiplier: float = 1.0) -> float:
    """Combine market, account-risk and optional profile caps in one place."""
    risk_limit = env_float("RISK_MAX_EXPOSURE_PCT", 70)
    profile_limit = risk_limit if profile_limit_pct is None else max(0.0, min(100.0, float(profile_limit_pct)))
    return max(0.0, min(market_exposure_limit_pct(up_ratio), profile_limit) * max(0.0, float(regime_multiplier)))


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

    ratio = cached_whole_market_up_ratio(as_of_day=end_day)
    return ratio if ratio is not None else watchlist_market_up_ratio(end_day)


def passes_market_filter(end_day: date) -> bool:
    minimum = env_float("MIN_MARKET_UP_RATIO", 0.25)
    return naver_market_up_ratio(end_day) >= minimum


def current_market_regime(end_day: date) -> str:
    """Classify today as bull/bear/sideways the same way the isolated
    backtest labels history (see backtest_data.label_market_regimes), so a
    profile can react to live market structure using the exact rule its own
    backtest results were measured against.
    """
    from .backtest_data import label_market_regimes
    ma_days = int(env_float("BACKTEST_REGIME_MA_DAYS", 120))
    return_days = int(env_float("BACKTEST_REGIME_RETURN_DAYS", 60))
    trend_pct = env_float("BACKTEST_REGIME_TREND_PCT", 5)
    warmup = max(ma_days, return_days)
    try:
        rows = naver_rows("KOSPI", end_day - timedelta(days=int(warmup * 1.6) + 30), end_day)
    except Exception:
        return "sideways"
    labels = label_market_regimes(rows, ma_days, return_days, trend_pct)
    return labels[-1]["regime"] if labels else "sideways"


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

        whole_market = cached_whole_market_average_change_pct(as_of_day=end_day)
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
        if item.pick:
            values["momentum_score"] = momentum_score(
                relative_score, item.pick.volume_ratio, item.pick.trend_score,
                values.get("momentum_1m_pct"), values.get("momentum_3m_pct"),
            )
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


def profile_total_score(values: dict, weights: dict[str, float]) -> float:
    from .trading_profiles import CATEGORY_VALUE_KEYS
    def category_value(category: str) -> float:
        value = values.get(CATEGORY_VALUE_KEYS[category])
        return 50.0 if value is None else float(value)

    return round(sum(category_value(category) * weight for category, weight in weights.items()), 2)


def select_for_profile(evaluations: list[CandidateEvaluation], profile: dict, top_n: int, path: str | None = None) -> list[Pick]:
    """Re-rank the shared, already quality-filtered candidate pool with one
    profile's own category weights, volatility cap, and max-holdings limit.
    Never loosens the safety filters `evaluations` already applied -- a
    candidate that failed those never reaches this function's ranking.
    """
    minimum = env_float("MIN_RECOMMEND_SCORE", 50)
    blocked = open_recommended_tickers()
    weights = profile.get("scoring_weights")
    max_volatility = profile.get("max_volatility_atr_pct")
    ranked = []
    for evaluation in evaluations:
        pick = evaluation.pick
        if not pick or pick.score < minimum or pick.ticker in blocked:
            continue
        if max_volatility is not None and pick.atr20_pct > max_volatility:
            continue
        rank_score = profile_total_score(evaluation.values, weights) if weights else pick.score
        ranked.append((rank_score, pick))
    ranked.sort(key=lambda item: item[0], reverse=True)
    room = top_n
    max_holdings = profile.get("max_holdings")
    if max_holdings is not None:
        from .data_store import virtual_trader_state
        held = len(virtual_trader_state(path=path or "data/stock_alarm.db").get("holdings", []))
        room = max(0, min(top_n, max_holdings - held))
    result, seen = [], set()
    for _score, pick in ranked:
        if pick.ticker in seen:
            continue
        seen.add(pick.ticker)
        result.append(pick)
        if len(result) >= room:
            break
    return result


def recommend_for_profiles(end_day: date, top_n: int, min_trading_value: int, volume_multiplier: float, run_id: str) -> dict[str, list[Pick]]:
    """Evaluate the shared candidate universe once, then let each virtual
    trading profile rank and select from it independently (own weights,
    volatility cap, max holdings) -- see select_for_profile.
    """
    from .trading_profiles import PROFILES
    from .data_store import write_candidates, write_profile_selections
    universe = recommend_universe(min_trading_value)
    if not passes_market_filter(end_day):
        write_candidates(run_id, ({"ticker": ticker, "name": name, "evaluated_at": datetime.now().isoformat(timespec="seconds"), "passed": 0, "selected": 0, "rejection_reasons": "market_filter"} for ticker, name in universe.items()))
        return {name: [] for name in PROFILES}
    evaluations = [evaluate_naver_candidate(ticker, name, end_day, min_trading_value, volume_multiplier, record_quality=True) for ticker, name in universe.items()]
    evaluations = apply_relative_strength(evaluations, market_benchmark_return(end_day))
    ranks = {evaluation.pick.ticker: index for index, evaluation in enumerate(
        sorted((item for item in evaluations if item.pick), key=lambda item: item.pick.score, reverse=True), 1,
    )}
    picks_by_profile: dict[str, list[Pick]] = {}
    for name, profile in PROFILES.items():
        selected = select_for_profile(evaluations, profile, top_n, path=profile["db_path"])
        picks_by_profile[name] = selected
        selected_tickers = {pick.ticker for pick in selected}
        weights = profile.get("scoring_weights")
        write_profile_selections(run_id, name, (
            {
                "ticker": evaluation.ticker,
                "rank": ranks.get(evaluation.ticker),
                "selected": int(evaluation.ticker in selected_tickers),
                "profile_score": profile_total_score(evaluation.values, weights) if weights and evaluation.pick else None,
            }
            for evaluation in evaluations if evaluation.pick
        ))
    aggressive_selected = {pick.ticker for pick in picks_by_profile.get("aggressive", [])}
    write_candidates(run_id, (
        {**evaluation.values, "rank": ranks.get(evaluation.ticker),
         "selected": int(evaluation.ticker in aggressive_selected),
         "rejection_reasons": ("score_or_portfolio_filter" if evaluation.pick and evaluation.ticker not in aggressive_selected and not evaluation.values.get("rejection_reasons") else evaluation.values.get("rejection_reasons"))}
        for evaluation in evaluations
    ))
    return picks_by_profile


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
                result[ticker] = {"time": event_time, "reason": row.get("reason", ""), "stage": row.get("stage", ""), "close": row.get("close", "")}
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


def recommend_picks_by_profile(markets: list[str], top_n: int, min_trading_value: int, volume_multiplier: float, run_id: str) -> dict[str, list[Pick]]:
    """Per-profile picks for run()'s live scheduled path. The Naver data
    source (the only one actually used in production) shares one candidate
    evaluation and lets each profile rank/select independently -- see
    recommend_for_profiles. The legacy pykrx path isn't profile-aware and
    just gives every profile the same shared list, matching its old behavior.
    """
    from .trading_profiles import PROFILES
    if os.environ.get("DATA_SOURCE", "naver").lower() == "naver":
        return recommend_for_profiles(latest_naver_trading_day(), top_n, min_trading_value, volume_multiplier, run_id)
    shared = recommend_for_day(latest_trading_day(), markets, top_n, min_trading_value, volume_multiplier)
    return {name: shared for name in PROFILES}


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


def allocation_percentages(
    picks: list[Pick],
    performance_path: str = "logs/recommendation_performance.csv",
    min_position_pct_override: float | None = None,
    max_position_pct_override: float | None = None,
) -> list[float]:
    """Return per-position target weights against total virtual-account equity.

    Sizing is driven by recent volatility and each ticker's own learned
    historical performance -- not by the recommendation score, which
    production data showed is negatively correlated with subsequent returns.
    """
    if not picks:
        return []
    if os.environ.get("VIRTUAL_TRADER_POSITION_SIZING_MODE", "fixed").strip().lower() == "fixed":
        fixed = max(0.0, min(100.0, env_float("VIRTUAL_TRADER_FIXED_POSITION_PCT", 10)))
        return [round(fixed, 2) for _pick in picks]
    learned = historical_allocation_factors(performance_path)
    min_default = env_float("VIRTUAL_TRADER_MIN_POSITION_PCT", 10) if min_position_pct_override is None else float(min_position_pct_override)
    max_default = env_float("VIRTUAL_TRADER_MAX_POSITION_PCT", 30) if max_position_pct_override is None else float(max_position_pct_override)
    minimum = max(0.0, min(100.0, min_default))
    maximum = max(minimum, min(100.0, max_default))
    allocations = []
    for pick in picks:
        volatility = max(0.5, min(1.0, 3 / max(pick.atr20_pct, 3)))
        target = maximum * volatility * learned.get(pick.ticker, 1.0)
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


def auto_buy_virtual_trader(
    picks: list[Pick],
    path: str = "data/stock_alarm.db",
    sector_cap_override: float | None = None,
    exposure_limit_override: float | None = None,
    min_position_pct_override: float | None = None,
    max_position_pct_override: float | None = None,
    regime_exposure_multiplier: dict[str, float] | None = None,
    risk_release_policy: dict | None = None,
) -> dict | None:
    if not picks or os.environ.get("VIRTUAL_TRADER_AUTO_BUY", "1") != "1":
        return None
    from .data_store import virtual_buy, virtual_trader_state
    from .portfolio_risk import buy_allocation_scale, new_buys_allowed
    state = virtual_trader_state(path=path)
    if state.get("cash", 0) <= 0:
        return None
    allowed, _reason = new_buys_allowed(path=path, release_policy=risk_release_policy)
    if not allowed:
        return None
    scale = buy_allocation_scale(path=path, release_policy=risk_release_policy)[0] if risk_release_policy else 1.0
    allocations = allocation_percentages(picks, min_position_pct_override=min_position_pct_override, max_position_pct_override=max_position_pct_override)
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
    regime_multiplier = 1.0
    if regime_exposure_multiplier:
        regime_multiplier = float(regime_exposure_multiplier.get(current_market_regime(date.today()), 1.0))
    exposure_limit = effective_exposure_limit_pct(breadth, exposure_limit_override, regime_multiplier)
    candidates = [
        {"ticker": pick.ticker, "name": pick.name, "close": pick.close, "score": pick.score, "allocation_pct": round(allocation * scale, 2),
         "portfolio_limit_pct": exposure_limit, "price_quality": "valid"}
        for pick, allocation in zip(picks, allocations)
    ]
    try:
        return virtual_buy(candidates, path=path, risk_release_policy=risk_release_policy)
    except ValueError:
        return None


def stop_and_target(pick: Pick) -> tuple[int, float, int, float]:
    """(stop price, stop %, first take-profit price, take-profit %) matching the
    rules sell_check applies: the stop is the wider of SELL_LOSS_PCT and the
    ATR-based distance, the first target is TAKE_PROFIT_1_PCT."""
    stop_pct = max(abs(env_float("SELL_LOSS_PCT", 5)), float(pick.atr20_pct or 0) * env_float("SELL_ATR_MULTIPLIER", 2))
    target_pct = env_float("TAKE_PROFIT_1_PCT", 10)
    return round(pick.close * (1 - stop_pct / 100)), stop_pct, round(pick.close * (1 + target_pct / 100)), target_pct


def fundamental_lines(data: dict | None) -> list[str]:
    """One line of the collected financials, plus a warning when they are bad."""
    if not data:
        return []
    parts = []
    if data.get("per"):
        parts.append(f"PER {float(data['per']):.1f}")
    free_cash_flow = data.get("free_cash_flow")
    if free_cash_flow is not None:
        parts.append(f"잉여현금흐름 {free_cash_flow / 1e8:+,.0f}억")
    if data.get("revenue_growth_pct") is not None:
        parts.append(f"매출 {float(data['revenue_growth_pct']):+.1f}%")
    lines = ["재무: " + " · ".join(parts)] if parts else []
    warnings = []
    if not data.get("per") and data.get("market"):
        warnings.append("적자 기업(PER 없음)")
    if free_cash_flow is not None and free_cash_flow < 0:
        warnings.append("잉여현금흐름 적자")
    if warnings:
        lines.append("⚠ " + " · ".join(warnings))
    return lines


def format_message(picks: list[Pick], virtual_result: dict | None = None, fundamentals: dict | None = None) -> str:
    if not picks:
        return "오늘 조건에 맞는 관심 종목이 없습니다."
    fundamentals = fundamentals or {}
    executions = {row["ticker"]: row for row in (virtual_result or {}).get("executions", [])}
    bought = [pick for pick in picks if pick.ticker in executions]
    shown = bought or picks
    now = datetime.now().strftime("%H:%M")
    if bought:
        lines = [f"[가상매수 체결 · {now}]",
                 f"{len(bought)}종목 · 총 {virtual_result.get('spent', 0):,}원 · 잔여 현금 {virtual_result.get('cash', 0):,}원"]
    else:
        lines = [f"[매수 추천 · {now}]", f"추천 {len(picks)}종목"]
    allocations = dict(zip((pick.ticker for pick in picks), allocation_percentages(picks)))
    for index, pick in enumerate(shown, 1):
        stop_price, stop_pct, target_price, target_pct = stop_and_target(pick)
        lines.extend(["", f"{index}. {pick.name}({pick.ticker})"])
        execution = executions.get(pick.ticker)
        if execution:
            price = int(execution.get("price") or pick.close)
            lines.append(f"매수 {execution['quantity']:,}주 × {price:,}원 = {execution['cost']:,}원")
        else:
            lines.append(f"현재가 {pick.close:,}원 · 목표 비중 {allocations.get(pick.ticker, 0):.0f}%")
        lines.append(f"손절 {stop_price:,}원(-{stop_pct:.1f}%) · 1차 익절 {target_price:,}원(+{target_pct:.0f}%)")
        signal = reason_summary(pick.volume_ratio, pick.news_score, pick.disclosure_score, pick.performance_penalty)
        detail = f"신호: {signal} · 거래량 {pick.volume_ratio:.1f}배"
        if pick.atr20_pct:
            detail += f" · 하루 변동폭 약 {pick.atr20_pct:.1f}%"
        lines.append(detail)
        lines.extend(fundamental_lines(fundamentals.get(pick.ticker)))
    others = [pick.name for pick in picks if pick not in shown]
    if others:
        lines.extend(["", "기타 추천(미매수): " + ", ".join(others)])
    lines.append("조건 기반 알림이며 투자 자문이 아닙니다.")
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
        picks_by_profile = recommend_picks_by_profile(markets, top_n, min_trading_value, volume_multiplier, run_id)
        picks = picks_by_profile.get("aggressive", [])
        track_positions(picks)
        write_log(picks)
        from .trading_profiles import PROFILES
        from .shadow_trader import latest_trade_id, record_intraday_buys
        last_trade_id = latest_trade_id()
        virtual_result = auto_buy_virtual_trader(picks, regime_exposure_multiplier=PROFILES["aggressive"]["regime_exposure_multiplier"])
        try:
            # Observation only: shadow the buys just made, at the same market
            # reading, before anything else changes the state they depend on.
            record_intraday_buys(last_trade_id)
        except Exception as error:
            print(f"shadow_trader intraday failed {error!r}")
        # Secondary virtual-trader profiles rank/select from the same shared
        # evaluation with their own weights (see select_for_profile) and buy
        # their own resulting pick list with their own sizing/exit rules --
        # they don't get their own tracked-position log entry.
        for name, profile in PROFILES.items():
            if name == "aggressive":
                continue
            auto_buy_virtual_trader(
                picks_by_profile.get(name, []),
                path=profile["db_path"],
                sector_cap_override=profile["sector_cap_pct"],
                exposure_limit_override=profile["exposure_limit_pct"],
                min_position_pct_override=profile["min_position_pct"],
                max_position_pct_override=profile["max_position_pct"],
                regime_exposure_multiplier=profile["regime_exposure_multiplier"],
                risk_release_policy=profile.get("risk_release"),
            )
        finish_run(run_id)
    except Exception:
        finish_run(run_id, "failed")
        raise
    if not picks and os.environ.get("SEND_EMPTY_RECOMMENDATION", "0") != "1":
        return
    executed = (virtual_result or {}).get("executions") or []
    if picks and not executed and os.environ.get("RECOMMENDATION_ALERTS", "bought") != "all":
        # Every 5-minute batch used to go out (16 alerts a day on average, up
        # to 26), burying the sell alerts; picks the virtual trader did not buy
        # are listed in the 16:00 briefing instead.
        print(f"recommendation alert held for the briefing picks={len(picks)} (no virtual buy)")
        return
    fundamentals: dict = {}
    try:
        from .screener import fundamentals_for
        fundamentals = fundamentals_for([pick.ticker for pick in picks])
    except Exception:
        pass
    from .notifier import send_notification

    send_notification(format_message(picks, virtual_result, fundamentals), event_type="recommendation", tickers=[pick.ticker for pick in picks])


def main() -> None:
    try:
        run()
    except Exception as error:
        write_error_log(error)
        raise
