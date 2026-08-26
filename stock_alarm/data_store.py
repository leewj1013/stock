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
SCHEMA_VERSION = 6
STRATEGY_VERSION = "anti-chase-confirmed-exit-v4"


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
            legacy_score REAL,
            legacy_passed INTEGER NOT NULL DEFAULT 0,
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
            entry_price INTEGER,
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
        CREATE TABLE IF NOT EXISTS virtual_accounts (
            account_id INTEGER PRIMARY KEY CHECK (account_id = 1),
            cash INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS virtual_deposits (
            deposit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount INTEGER NOT NULL CHECK (amount > 0),
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS virtual_trades (
            trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT,
            price INTEGER NOT NULL CHECK (price > 0),
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            cost INTEGER NOT NULL CHECK (cost > 0),
            allocation_pct REAL NOT NULL,
            score REAL NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'recommendation'
        );
        CREATE INDEX IF NOT EXISTS idx_virtual_trades_ticker ON virtual_trades(ticker, created_at);
        CREATE TABLE IF NOT EXISTS virtual_valuation_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            cash INTEGER NOT NULL,
            holdings_cost INTEGER NOT NULL,
            valuation INTEGER NOT NULL,
            equity INTEGER NOT NULL,
            profit_loss INTEGER NOT NULL,
            return_pct REAL NOT NULL,
            return_change_pct REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_virtual_valuation_time ON virtual_valuation_snapshots(created_at);
        CREATE TABLE IF NOT EXISTS virtual_sales (
            sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT,
            price INTEGER NOT NULL CHECK (price > 0),
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            proceeds INTEGER NOT NULL CHECK (proceeds > 0),
            cost_basis INTEGER NOT NULL CHECK (cost_basis > 0),
            realized_profit_loss INTEGER NOT NULL,
            reason TEXT
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
    if "legacy_score" not in candidate_columns:
        connection.execute("ALTER TABLE candidate_snapshots ADD COLUMN legacy_score REAL")
    if "legacy_passed" not in candidate_columns:
        connection.execute("ALTER TABLE candidate_snapshots ADD COLUMN legacy_passed INTEGER NOT NULL DEFAULT 0")
    position_columns = {row[1] for row in connection.execute("PRAGMA table_info(position_checks)")}
    for name, definition in (("atr20_pct", "REAL"), ("dynamic_stop_loss_pct", "REAL"), ("time_stop_triggered", "INTEGER NOT NULL DEFAULT 0")):
        if name not in position_columns:
            connection.execute(f"ALTER TABLE position_checks ADD COLUMN {name} {definition}")
    sell_columns = {row[1] for row in connection.execute("PRAGMA table_info(sell_outcomes)")}
    if "entry_price" not in sell_columns:
        connection.execute("ALTER TABLE sell_outcomes ADD COLUMN entry_price INTEGER")
    connection.commit()
    return connection


def virtual_trader_state(prices: dict[str, int] | None = None, path: str = DB_PATH) -> dict[str, Any]:
    prices = prices or {}
    with closing(connect(path)) as connection:
        account = connection.execute("SELECT cash FROM virtual_accounts WHERE account_id = 1").fetchone()
        rows = connection.execute(
            """WITH buys AS (
                 SELECT ticker, MAX(name) AS name, SUM(quantity) AS bought_quantity, SUM(cost) AS bought_cost
                 FROM virtual_trades GROUP BY ticker
               ), sales AS (
                 SELECT ticker, SUM(quantity) AS sold_quantity, SUM(cost_basis) AS sold_cost
                 FROM virtual_sales GROUP BY ticker
               )
               SELECT buys.ticker, buys.name,
                      buys.bought_quantity - COALESCE(sales.sold_quantity, 0) AS quantity,
                      buys.bought_cost - COALESCE(sales.sold_cost, 0) AS cost
               FROM buys LEFT JOIN sales USING(ticker)
               WHERE buys.bought_quantity > COALESCE(sales.sold_quantity, 0)
               ORDER BY buys.ticker"""
        ).fetchall()
        deposited = int(connection.execute("SELECT COALESCE(SUM(amount), 0) FROM virtual_deposits").fetchone()[0])
        realized_profit_loss = int(connection.execute("SELECT COALESCE(SUM(realized_profit_loss), 0) FROM virtual_sales").fetchone()[0])
        last_activity = connection.execute(
            """SELECT MAX(created_at) FROM (
                 SELECT created_at FROM virtual_deposits
                 UNION ALL SELECT created_at FROM virtual_trades
                 UNION ALL SELECT created_at FROM virtual_sales
               )"""
        ).fetchone()[0]
        today_prefix = datetime.now().date().isoformat() + "%"
        today_buys = int(connection.execute("SELECT COUNT(*) FROM virtual_trades WHERE created_at LIKE ?", (today_prefix,)).fetchone()[0])
        today_sells = int(connection.execute("SELECT COUNT(*) FROM virtual_sales WHERE created_at LIKE ?", (today_prefix,)).fetchone()[0])
    holdings = []
    for row in rows:
        cost, quantity = int(row["cost"]), int(row["quantity"])
        current_price = int(prices.get(row["ticker"]) or round(cost / quantity))
        valuation = current_price * quantity
        profit_loss = valuation - cost
        holdings.append({
            "ticker": row["ticker"], "name": row["name"], "quantity": quantity,
            "cost": cost, "average_price": round(cost / quantity, 2), "current_price": current_price,
            "valuation": valuation, "profit_loss": profit_loss,
            "return_pct": round(profit_loss / cost * 100, 2) if cost else 0,
        })
    cash = int(account["cash"]) if account else 0
    holdings_value = sum(row["valuation"] for row in holdings)
    invested_cost = sum(row["cost"] for row in holdings)
    unrealized_profit_loss = sum(row["profit_loss"] for row in holdings)
    total_equity = cash + holdings_value
    holdings_return_pct = round(unrealized_profit_loss / invested_cost * 100, 2) if invested_cost else 0
    total_return_pct = round((total_equity - deposited) / deposited * 100, 2) if deposited else 0
    return {
        "cash": cash,
        "holdings": holdings,
        "holdings_value": holdings_value,
        "invested_cost": invested_cost,
        "total_equity": total_equity,
        "unrealized_profit_loss": unrealized_profit_loss,
        "realized_profit_loss": realized_profit_loss,
        "total_profit_loss": total_equity - deposited,
        "holdings_return_pct": holdings_return_pct,
        "total_return_pct": total_return_pct,
        "deposited": deposited,
        "last_activity_at": last_activity or "",
        "auto_trading": os.environ.get("VIRTUAL_TRADER_AUTO_BUY", "1") == "1",
        "schedule": "평일 08:50~15:40",
        "today_buys": today_buys,
        "today_sells": today_sells,
    }


def virtual_deposit(amount: int, path: str = DB_PATH) -> dict[str, Any]:
    amount = int(amount)
    if amount <= 0:
        raise ValueError("deposit amount must be positive")
    now = datetime.now().isoformat(timespec="seconds")
    with closing(connect(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO virtual_accounts(account_id, cash, created_at, updated_at) VALUES(1, ?, ?, ?)
               ON CONFLICT(account_id) DO UPDATE SET cash = cash + excluded.cash, updated_at = excluded.updated_at""",
            (amount, now, now),
        )
        connection.execute("INSERT INTO virtual_deposits(amount, created_at) VALUES(?, ?)", (amount, now))
        connection.commit()
    return virtual_trader_state(path=path)


def virtual_buy(candidates: list[dict[str, Any]], path: str = DB_PATH) -> dict[str, Any]:
    valid = [row for row in candidates if int(float(row.get("close") or 0)) > 0 and row.get("ticker")]
    if not valid:
        raise ValueError("no recommendation candidates available")
    max_position_pct = max(0.0, min(100.0, float(os.environ.get("VIRTUAL_TRADER_MAX_POSITION_PCT", "30"))))
    now = datetime.now().isoformat(timespec="seconds")
    with closing(connect(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = connection.execute("SELECT cash FROM virtual_accounts WHERE account_id = 1").fetchone()
        cash = int(account["cash"]) if account else 0
        if cash <= 0:
            raise ValueError("cash balance is empty")
        position_rows = connection.execute(
            """WITH buys AS (
                 SELECT ticker, SUM(cost) AS cost FROM virtual_trades GROUP BY ticker
               ), sales AS (
                 SELECT ticker, SUM(cost_basis) AS cost FROM virtual_sales GROUP BY ticker
               )
               SELECT buys.ticker, buys.cost - COALESCE(sales.cost, 0) AS open_cost
               FROM buys LEFT JOIN sales USING(ticker)
               WHERE buys.cost > COALESCE(sales.cost, 0)"""
        ).fetchall()
        open_costs = {row["ticker"]: int(row["open_cost"]) for row in position_rows}
        account_equity = cash + sum(open_costs.values())
        spent = 0
        trades = []
        for row in valid:
            price = int(float(row["close"]))
            # Enforce the portfolio cap here as well, so stale logs or manual API
            # requests can never put the whole account into a single stock.
            allocation_pct = max(0.0, min(max_position_pct, float(row.get("allocation_pct") or 0)))
            if not allocation_pct:
                continue
            target_cost = int(account_equity * allocation_pct / 100)
            additional_budget = min(max(0, target_cost - open_costs.get(row["ticker"], 0)), cash - spent)
            quantity = int(additional_budget // price)
            cost = price * quantity
            if quantity < 1 or spent + cost > cash:
                continue
            trades.append((now, row["ticker"], row.get("name", ""), price, quantity, cost, allocation_pct, float(row.get("score") or 0)))
            spent += cost
        if not trades:
            raise ValueError("allocated amounts are below the stock prices")
        connection.executemany(
            """INSERT INTO virtual_trades(created_at,ticker,name,price,quantity,cost,allocation_pct,score)
               VALUES(?,?,?,?,?,?,?,?)""", trades,
        )
        connection.execute("UPDATE virtual_accounts SET cash = cash - ?, updated_at = ? WHERE account_id = 1", (spent, now))
        connection.commit()
    executions = [
        {"ticker": trade[1], "name": trade[2], "price": trade[3], "quantity": trade[4], "cost": trade[5], "allocation_pct": round(trade[6], 2)}
        for trade in trades
    ]
    return {**virtual_trader_state(path=path), "spent": spent, "bought": len(trades), "executions": executions}


def import_legacy_virtual_trader(state: dict[str, Any], path: str = DB_PATH) -> bool:
    """Import browser-local state only when the DB account has never been used."""
    cash = max(0, int(float(state.get("cash") or 0)))
    holdings = state.get("holdings") or {}
    rows = list(holdings.values()) if isinstance(holdings, dict) else list(holdings)
    now = datetime.now().isoformat(timespec="seconds")
    with closing(connect(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        used = connection.execute("SELECT EXISTS(SELECT 1 FROM virtual_deposits) OR EXISTS(SELECT 1 FROM virtual_trades)").fetchone()[0]
        if used:
            return False
        connection.execute(
            "INSERT OR REPLACE INTO virtual_accounts(account_id,cash,created_at,updated_at) VALUES(1,?,?,?)",
            (cash, now, now),
        )
        trades = []
        for row in rows:
            quantity = int(float(row.get("quantity") or 0))
            cost = int(float(row.get("cost") or 0))
            price = int(round(float(row.get("buyPrice") or row.get("average_price") or (cost / quantity if quantity else 0))))
            if row.get("ticker") and quantity > 0 and cost > 0 and price > 0:
                trades.append((now, row["ticker"], row.get("name", ""), price, quantity, cost, 0, 0, "local_storage_import"))
        connection.executemany(
            """INSERT INTO virtual_trades(created_at,ticker,name,price,quantity,cost,allocation_pct,score,source)
               VALUES(?,?,?,?,?,?,?,?,?)""", trades,
        )
        connection.commit()
    return bool(cash or trades)


def virtual_sell(alerts: list[dict[str, Any]], path: str = DB_PATH) -> dict[str, Any]:
    if not alerts:
        return virtual_trader_state(path=path)
    now = datetime.now().isoformat(timespec="seconds")
    with closing(connect(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        total_proceeds = 0
        sales = []
        for alert in alerts:
            ticker, price = str(alert.get("ticker") or ""), int(float(alert.get("close") or 0))
            if not ticker or price <= 0:
                continue
            bought = connection.execute("SELECT COALESCE(SUM(quantity),0), COALESCE(SUM(cost),0), MAX(name) FROM virtual_trades WHERE ticker=?", (ticker,)).fetchone()
            sold = connection.execute("SELECT COALESCE(SUM(quantity),0), COALESCE(SUM(cost_basis),0) FROM virtual_sales WHERE ticker=?", (ticker,)).fetchone()
            quantity, cost_basis = int(bought[0] - sold[0]), int(bought[1] - sold[1])
            if quantity <= 0 or cost_basis <= 0:
                continue
            proceeds = price * quantity
            sales.append((now, ticker, alert.get("name") or bought[2] or "", price, quantity, proceeds, cost_basis, proceeds - cost_basis, alert.get("reason", "")))
            total_proceeds += proceeds
        if sales:
            connection.executemany(
                """INSERT INTO virtual_sales(created_at,ticker,name,price,quantity,proceeds,cost_basis,realized_profit_loss,reason)
                   VALUES(?,?,?,?,?,?,?,?,?)""", sales,
            )
            account = connection.execute("SELECT 1 FROM virtual_accounts WHERE account_id=1").fetchone()
            if account:
                connection.execute("UPDATE virtual_accounts SET cash=cash+?, updated_at=? WHERE account_id=1", (total_proceeds, now))
            else:
                connection.execute("INSERT INTO virtual_accounts(account_id,cash,created_at,updated_at) VALUES(1,?,?,?)", (total_proceeds, now, now))
        connection.commit()
    executions = [
        {"ticker": sale[1], "name": sale[2], "price": sale[3], "quantity": sale[4], "proceeds": sale[5], "cost_basis": sale[6], "realized_profit_loss": sale[7]}
        for sale in sales
    ]
    return {**virtual_trader_state(path=path), "sold": len(sales), "proceeds": total_proceeds, "executions": executions}


def collection_settings() -> dict[str, str]:
    names = [
        "DATA_SOURCE", "TOP_N", "MIN_TRADING_VALUE", "VOLUME_MULTIPLIER",
        "MIN_RECOMMEND_SCORE", "MAX_DAY_CHANGE_PCT", "MAX_ENTRY_DAY_CHANGE_PCT",
        "MAX_MA20_DISTANCE_PCT", "MAX_MA20_DISTANCE_ATR", "MAX_AVG_RANGE_PCT",
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
        "legacy_score", "legacy_passed", "final_score", "passed", "selected", "rank", "rejection_reasons",
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
               legacy_score, legacy_passed, final_score, passed, selected, rank, rejection_reasons
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
    columns = ["alert_key", "alert_created_at", "ticker", "name", "entry_price", "alert_close", "execution_date", "execution_price", "return_1d_pct", "return_3d_pct", "return_5d_pct", "return_10d_pct", "execution_cost_bps", "updated_at"]
    values = [tuple(row.get(column) for column in columns) for row in rows]
    with closing(connect(path)) as connection:
        connection.execute("DELETE FROM sell_outcomes")
        if not values:
            connection.commit()
            return
        connection.executemany(f"INSERT OR REPLACE INTO sell_outcomes ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", values)
        connection.commit()


def recent_sell_outcomes(limit: int = 100, path: str = DB_PATH) -> list[dict[str, Any]]:
    return query_rows("SELECT * FROM sell_outcomes ORDER BY alert_created_at DESC LIMIT ?", (limit,), path)


def record_virtual_valuation(prices: dict[str, int], path: str = DB_PATH) -> dict[str, Any]:
    state = virtual_trader_state(prices, path)
    holdings_cost = sum(int(row["cost"]) for row in state["holdings"])
    valuation = sum(int(row["valuation"]) for row in state["holdings"])
    profit_loss = valuation - holdings_cost
    return_pct = round(profit_loss / holdings_cost * 100, 4) if holdings_cost else 0.0
    equity = int(state["cash"]) + valuation
    now = datetime.now().isoformat(timespec="seconds")
    with closing(connect(path)) as connection:
        previous = connection.execute(
            "SELECT return_pct FROM virtual_valuation_snapshots ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone()
        change = round(return_pct - float(previous["return_pct"]), 4) if previous else 0.0
        connection.execute(
            """INSERT INTO virtual_valuation_snapshots(
                   created_at,cash,holdings_cost,valuation,equity,profit_loss,return_pct,return_change_pct
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (now, state["cash"], holdings_cost, valuation, equity, profit_loss, return_pct, change),
        )
        connection.commit()
    return {
        "created_at": now, "cash": state["cash"], "holdings_cost": holdings_cost,
        "valuation": valuation, "equity": equity, "profit_loss": profit_loss,
        "return_pct": return_pct, "return_change_pct": change,
    }


def latest_virtual_valuation(path: str = DB_PATH) -> dict[str, Any]:
    rows = query_rows("SELECT * FROM virtual_valuation_snapshots ORDER BY snapshot_id DESC LIMIT 1", path=path)
    return rows[0] if rows else {}


def recent_virtual_trades(limit: int = 100, path: str = DB_PATH) -> list[dict[str, Any]]:
    return query_rows("SELECT * FROM virtual_trades ORDER BY trade_id DESC LIMIT ?", (limit,), path)
