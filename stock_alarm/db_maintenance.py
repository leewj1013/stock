from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from .data_store import DB_PATH, connect


def integrity_check(path: str = DB_PATH) -> str:
    with closing(connect(path)) as connection:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])


def backup_database(path: str = DB_PATH, backup_dir: str = "data/backups", keep: int = 8) -> str:
    # Named after the source file's own stem (stock_alarm / stock_alarm_neutral
    # / ...) so each profile's backups are pruned independently and never
    # collide with another profile's retention count.
    prefix = Path(path).stem
    Path(backup_dir).mkdir(parents=True, exist_ok=True)
    target = Path(backup_dir) / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}.db"
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    backups = sorted(Path(backup_dir).glob(f"{prefix}-*.db"), reverse=True)
    for old in backups[max(1, keep):]:
        old.unlink()
    return str(target)


def prune_old_snapshots(path: str = DB_PATH, retention_days: int | None = None) -> dict[str, int]:
    days = retention_days if retention_days is not None else int(os.environ.get("DB_RAW_RETENTION_DAYS", "365"))
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with closing(connect(path)) as connection:
        candidates = connection.execute("DELETE FROM candidate_snapshots WHERE evaluated_at < ?", (cutoff,)).rowcount
        positions = connection.execute("DELETE FROM position_checks WHERE checked_at < ?", (cutoff,)).rowcount
        price_quality = connection.execute("DELETE FROM price_quality WHERE created_at < ?", (cutoff,)).rowcount
        portfolio_risk = connection.execute("DELETE FROM portfolio_risk_snapshots WHERE created_at < ?", (cutoff,)).rowcount
        runs = connection.execute("DELETE FROM strategy_runs WHERE started_at < ? AND NOT EXISTS (SELECT 1 FROM candidate_snapshots c WHERE c.run_id = strategy_runs.run_id) AND NOT EXISTS (SELECT 1 FROM position_checks p WHERE p.run_id = strategy_runs.run_id)", (cutoff,)).rowcount
        connection.commit()
    return {"candidates": candidates, "positions": positions, "price_quality": price_quality, "portfolio_risk": portfolio_risk, "runs": runs}


def run(path: str = DB_PATH) -> dict[str, object]:
    status = integrity_check(path)
    if status != "ok":
        raise RuntimeError(f"database integrity check failed: {status}")
    backup = backup_database(path)
    pruned = prune_old_snapshots(path)
    with closing(connect(path)) as connection:
        connection.execute("PRAGMA optimize")
    return {"integrity": status, "backup": backup, "pruned": pruned}


def run_all_profiles() -> dict[str, dict[str, object]]:
    """Back up and maintain every virtual-trader profile's own DB, not just
    the default one -- a profile added after this ran once (e.g. the
    risk-neutral account) would otherwise never get backed up at all."""
    from .trading_profiles import PROFILES

    results = {}
    for name, profile in PROFILES.items():
        db_path = profile["db_path"]
        if not os.path.exists(db_path):
            continue
        results[name] = run(db_path)
    return results


def main() -> None:
    print(run_all_profiles())
    from .sell_research import PATH as research_path, run as run_exit_research
    result = run_exit_research()
    backup_database(research_path)
    print({"sell_research": result})
    from .entry_research import PATH as entry_path, run as run_entry_research
    entry_result = run_entry_research()
    backup_database(entry_path)
    print({"entry_research": entry_result})
    from .portfolio_research import PATH as portfolio_path, run as run_portfolio_research
    portfolio_result = run_portfolio_research()
    backup_database(portfolio_path)
    print({"portfolio_research": portfolio_result})
    from .earnings_research import PATH as earnings_path, run as run_earnings_research
    earnings_result = run_earnings_research()
    backup_database(earnings_path)
    print({"earnings_research": earnings_result})
    from .theme_research import PATH as theme_path, run as run_theme_research
    theme_result = run_theme_research()
    backup_database(theme_path)
    print({"theme_research": theme_result})
    from .market_group_snapshot import PATH as market_group_path, collect as collect_market_groups
    group_result = collect_market_groups()
    backup_database(market_group_path)
    print({"market_group_snapshot": group_result})
    from .naver_market_signals import PATH as naver_signals_path
    if Path(naver_signals_path).exists():
        backup_database(naver_signals_path)
    from .us_research import PATH as us_path, run as run_us_research
    us_result = run_us_research()
    backup_database(us_path)
    print({"us_research": us_result})
    if result["failures"] or entry_result["failures"] or portfolio_result["failures"] or earnings_result["failures"] or theme_result["failures"] or us_result["failures"]:
        raise RuntimeError("Research has collection failures; inspect research databases error columns")


if __name__ == "__main__":
    main()
