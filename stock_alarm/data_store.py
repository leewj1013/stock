from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from contextlib import closing
from datetime import datetime
from typing import Any, Iterable


DB_PATH = "data/stock_alarm.db"
SCHEMA_VERSION = 3
STRATEGY_VERSION = "time-volume-atr-v3"


def _file_hash(path: str) -> str:
    try:
        with open(path, "rb") as file:
            return hashlib.sha256(file.read()).hexdigest()[:16]
    except OSError:
        return "missing"


def strategy_identity() -> dict[str, str]:
    settings_json = json.dumps(collection_settings(), ensure_ascii=False, sort_keys=True)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=3, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True, timeout=3, check=True).stdout.strip())
        git_commit = f"{commit}-dirty" if dirty else commit
    except (OSError, subprocess.SubprocessError):
        git_commit = "unknown"
    return {
        "git_commit": git_commit,
        "config_hash": hashlib.sha256(settings_json.encode("utf-8")).hexdigest()[:16],
        "watchlist_hash": _file_hash("data/watchlist.csv"),
    }


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS strategy_runs (
            run_id TEXT PRIMARY KEY,
            run_type TEXT NOT NULL,
            started_at TEXT NOT NULL,
            market_date TEXT,
            strategy_version TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            settings_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'running',
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS candidate_snapshots (
            run_id TEXT NOT NULL REFERENCES strategy_runs(run_id),
            ticker TEXT NOT NULL,
            name TEXT,
            evaluated_at TEXT NOT NULL,
            close INTEGER,
            previous_close INTEGER,
            day_return_pct REAL,
            volume INTEGER,
            avg_volume REAL,
            raw_volume_ratio REAL,
            expected_volume_fraction REAL,
            volume_ratio REAL,
            raw_trading_value INTEGER,
            trading_value INTEGER,
            ma20 REAL,
            distance_ma20_pct REAL,
            avg_range_pct REAL,
            atr20_pct REAL,
            benchmark_symbol TEXT,
            market_proxy_return_pct REAL,
            relative_strength_pct REAL,
            relative_strength_score REAL,
            volume_score REAL,
            trading_value_score REAL,
            trend_score REAL,
            news_score REAL,
            disclosure_score REAL,
            performance_penalty REAL,
            financial_score REAL,
            financial_notes TEXT,
            per REAL,
            pbr REAL,
            dividend_yield REAL,
            final_score REAL,
            passed INTEGER NOT NULL,
            selected INTEGER NOT NULL DEFAULT 0,
            rank INTEGER,
            rejection_reasons TEXT,
            PRIMARY KEY (run_id, ticker)
        );
        CREATE TABLE IF NOT EXISTS position_checks (
            run_id TEXT NOT NULL REFERENCES strategy_runs(run_id),
            position_id TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT,
            entry_date TEXT,
            entry_price INTEGER,
            close INTEGER,
            holding_days INTEGER,
            return_pct REAL,
            previous_return_pct REAL,
            max_return_pct REAL,
            drawdown_from_peak_pct REAL,
            ma20 REAL,
            distance_ma20_pct REAL,
            atr20_pct REAL,
            dynamic_stop_loss_pct REAL,
            stop_loss_triggered INTEGER NOT NULL,
            ma20_break_triggered INTEGER NOT NULL,
            return_drop_triggered INTEGER NOT NULL,
            giveback_triggered INTEGER NOT NULL,
            time_stop_triggered INTEGER NOT NULL DEFAULT 0,
            decision TEXT NOT NULL,
            reasons TEXT,
            PRIMARY KEY (run_id, position_id)
        );
        CREATE TABLE IF NOT EXISTS sell_outcomes (
            alert_key TEXT PRIMARY KEY,
            alert_created_at TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT,
            alert_close INTEGER,
            execution_date TEXT,
            execution_price INTEGER,
            return_1d_pct REAL,
            return_3d_pct REAL,
            return_5d_pct REAL,
            return_10d_pct REAL,
            execution_cost_bps INTEGER,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_candidates_ticker_time
            ON candidate_snapshots(ticker, evaluated_at);
        CREATE INDEX IF NOT EXISTS idx_position_checks_ticker_time
            ON position_checks(ticker, checked_at);
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(strategy_runs)")}
    for name in ("git_commit", "config_hash", "watchlist_hash"):
        if name not in columns:
            connection.execute(f"ALTER TABLE strategy_runs ADD COLUMN {name} TEXT")
    candidate_columns = {row[1] for row in connection.execute("PRAGMA table_info(candidate_snapshots)")}
    for name in ("raw_volume_ratio", "expected_volume_fraction", "atr20_pct", "market_proxy_return_pct", "relative_strength_pct", "relative_strength_score", "financial_score", "per", "pbr", "dividend_yield"):
        if name not in candidate_columns:
            connection.execute(f"ALTER TABLE candidate_snapshots ADD COLUMN {name} REAL")
    if "financial_notes" not in candidate_columns:
        connection.execute("ALTER TABLE candidate_snapshots ADD COLUMN financial_notes TEXT")
    if "benchmark_symbol" not in candidate_columns:
        connection.execute("ALTER TABLE candidate_snapshots ADD COLUMN benchmark_symbol TEXT")
    if "raw_trading_value" not in candidate_columns:
        connection.execute("ALTER TABLE candidate_snapshots ADD COLUMN raw_trading_value INTEGER")
    position_columns = {row[1] for row in connection.execute("PRAGMA table_info(position_checks)")}
    for name, definition in (("atr20_pct", "REAL"), ("dynamic_stop_loss_pct", "REAL"), ("time_stop_triggered", "INTEGER NOT NULL DEFAULT 0")):
        if name not in position_columns:
            connection.execute(f"ALTER TABLE position_checks ADD COLUMN {name} {definition}")
    connection.commit()
    return connection


def collection_settings() -> dict[str, str]:
    names = [
        "DATA_SOURCE", "TOP_N", "MIN_TRADING_VALUE", "VOLUME_MULTIPLIER",
        "MIN_RECOMMEND_SCORE", "MAX_DAY_CHANGE_PCT", "MAX_AVG_RANGE_PCT",
        "MIN_MARKET_UP_RATIO", "NEWS_LOOKUP", "NEWS_SCORE_WEIGHT",
        "DART_LOOKUP", "DART_SCORE_WEIGHT", "SELL_LOSS_PCT", "SELL_DROP_PCT",
        "SELL_PROTECT_PROFIT_PCT", "SELL_GIVEBACK_PCT",
        "SELL_ATR_MULTIPLIER", "SELL_TIME_STOP_DAYS", "SELL_TIME_STOP_MIN_RETURN_PCT",
        "EXECUTION_COST_BPS", "PERFORMANCE_MIN_SAMPLES", "FUNDAMENTAL_LOOKUP", "MARKET_BENCHMARK_TICKER",
    ]
    return {name: os.environ.get(name, "") for name in names}


def start_run(run_type: str, market_date: str = "", path: str = DB_PATH) -> str:
    run_id = uuid.uuid4().hex
    identity = strategy_identity()
    with closing(connect(path)) as connection:
        connection.execute(
            """INSERT INTO strategy_runs
               (run_id, run_type, started_at, market_date, strategy_version, schema_version,
                settings_json, status, finished_at, git_commit, config_hash, watchlist_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'running', NULL, ?, ?, ?)""",
            (
                run_id,
                run_type,
                datetime.now().isoformat(timespec="seconds"),
                market_date,
                STRATEGY_VERSION,
                SCHEMA_VERSION,
                json.dumps(collection_settings(), ensure_ascii=False, sort_keys=True),
                identity["git_commit"],
                identity["config_hash"],
                identity["watchlist_hash"],
            ),
        )
        connection.commit()
    return run_id


def finish_run(run_id: str, status: str = "completed", path: str = DB_PATH) -> None:
    with closing(connect(path)) as connection:
        connection.execute(
            "UPDATE strategy_runs SET status = ?, finished_at = ? WHERE run_id = ?",
            (status, datetime.now().isoformat(timespec="seconds"), run_id),
        )
        connection.commit()


def write_candidates(run_id: str, rows: Iterable[dict[str, Any]], path: str = DB_PATH) -> None:
    columns = [
        "ticker", "name", "evaluated_at", "close", "previous_close", "day_return_pct",
        "volume", "avg_volume", "raw_volume_ratio", "expected_volume_fraction", "volume_ratio", "raw_trading_value", "trading_value", "ma20",
        "distance_ma20_pct", "avg_range_pct", "atr20_pct", "benchmark_symbol", "market_proxy_return_pct", "relative_strength_pct", "relative_strength_score", "volume_score", "trading_value_score",
        "trend_score", "news_score", "disclosure_score", "performance_penalty", "financial_score", "financial_notes", "per", "pbr", "dividend_yield",
        "final_score", "passed", "selected", "rank", "rejection_reasons",
    ]
    values = [(run_id, *(row.get(column) for column in columns)) for row in rows]
    if not values:
        return
    placeholders = ",".join("?" for _ in range(len(columns) + 1))
    with closing(connect(path)) as connection:
        connection.executemany(
            f"INSERT OR REPLACE INTO candidate_snapshots (run_id,{','.join(columns)}) VALUES ({placeholders})",
            values,
        )
        connection.commit()


def position_id(position: dict[str, str]) -> str:
    raw = f"{position.get('ticker', '').strip()}|{position.get('entry_date', '')}|{position.get('entry_price', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def write_position_checks(run_id: str, rows: Iterable[dict[str, Any]], path: str = DB_PATH) -> None:
    columns = [
        "position_id", "checked_at", "ticker", "name", "entry_date", "entry_price",
        "close", "holding_days", "return_pct", "previous_return_pct", "max_return_pct",
        "drawdown_from_peak_pct", "ma20", "distance_ma20_pct", "atr20_pct", "dynamic_stop_loss_pct", "stop_loss_triggered",
        "ma20_break_triggered", "return_drop_triggered", "giveback_triggered", "time_stop_triggered", "decision", "reasons",
    ]
    trigger_columns = {"stop_loss_triggered", "ma20_break_triggered", "return_drop_triggered", "giveback_triggered", "time_stop_triggered"}
    values = [(run_id, *(row.get(column, 0) if column in trigger_columns else row.get(column) for column in columns)) for row in rows]
    if not values:
        return
    placeholders = ",".join("?" for _ in range(len(columns) + 1))
    with closing(connect(path)) as connection:
        connection.executemany(
            f"INSERT OR REPLACE INTO position_checks (run_id,{','.join(columns)}) VALUES ({placeholders})",
            values,
        )
        connection.commit()


def query_rows(sql: str, parameters: tuple = (), path: str = DB_PATH) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]
    except sqlite3.DatabaseError:
        return []


def collection_summary(path: str = DB_PATH) -> dict[str, int]:
    rows = query_rows(
        """
        SELECT
          (SELECT COUNT(*) FROM strategy_runs) AS runs,
          (SELECT COUNT(*) FROM candidate_snapshots) AS candidates,
          (SELECT COUNT(*) FROM candidate_snapshots WHERE selected = 1) AS selected,
          (SELECT COUNT(*) FROM position_checks) AS position_checks,
          (SELECT COUNT(*) FROM position_checks WHERE decision = 'SELL') AS sell_decisions,
          (SELECT COUNT(*) FROM position_checks WHERE decision = 'HOLD') AS hold_decisions
        """,
        path=path,
    )
    return rows[0] if rows else {"runs": 0, "candidates": 0, "selected": 0, "position_checks": 0, "sell_decisions": 0, "hold_decisions": 0}


def recent_runs(limit: int = 20, path: str = DB_PATH) -> list[dict[str, Any]]:
    return query_rows(
        "SELECT started_at, run_type, market_date, strategy_version, schema_version, git_commit, config_hash, watchlist_hash, status, finished_at FROM strategy_runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
        path,
    )


def latest_candidates(limit: int = 100, path: str = DB_PATH) -> list[dict[str, Any]]:
    return query_rows(
        """
        SELECT evaluated_at, ticker, name, close, raw_volume_ratio, expected_volume_fraction,
               volume_ratio, raw_trading_value, trading_value, ma20, distance_ma20_pct, atr20_pct,
               benchmark_symbol, market_proxy_return_pct, relative_strength_pct, relative_strength_score,
               final_score, passed, selected, rank, rejection_reasons
        FROM candidate_snapshots
        WHERE run_id = (SELECT run_id FROM strategy_runs WHERE run_type = 'recommendation' ORDER BY started_at DESC LIMIT 1)
        ORDER BY selected DESC, passed DESC, rank ASC, ticker ASC LIMIT ?
        """,
        (limit,),
        path,
    )


def rejection_summary(path: str = DB_PATH) -> list[dict[str, Any]]:
    candidates = latest_candidates(100000, path)
    counts: dict[str, int] = {}
    for candidate in candidates:
        reasons = str(candidate.get("rejection_reasons") or "selected").split(",")
        for reason in reasons:
            reason = reason.strip() or "selected"
            counts[reason] = counts.get(reason, 0) + 1
    return [{"reason": reason, "count": count} for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def recent_position_checks(limit: int = 100, path: str = DB_PATH) -> list[dict[str, Any]]:
    return query_rows(
        """
        SELECT checked_at, ticker, name, entry_price, close, holding_days, return_pct,
               max_return_pct, drawdown_from_peak_pct, distance_ma20_pct, atr20_pct,
               dynamic_stop_loss_pct, time_stop_triggered, decision, reasons
        FROM position_checks ORDER BY checked_at DESC LIMIT ?
        """,
        (limit,),
        path,
    )


def write_sell_outcomes(rows: Iterable[dict[str, Any]], path: str = DB_PATH) -> None:
    columns = ["alert_key", "alert_created_at", "ticker", "name", "alert_close", "execution_date", "execution_price", "return_1d_pct", "return_3d_pct", "return_5d_pct", "return_10d_pct", "execution_cost_bps", "updated_at"]
    values = [tuple(row.get(column) for column in columns) for row in rows]
    if not values:
        return
    with closing(connect(path)) as connection:
        connection.executemany(f"INSERT OR REPLACE INTO sell_outcomes ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
        connection.commit()


def recent_sell_outcomes(limit: int = 100, path: str = DB_PATH) -> list[dict[str, Any]]:
    return query_rows("SELECT * FROM sell_outcomes ORDER BY alert_created_at DESC LIMIT ?", (limit,), path)
