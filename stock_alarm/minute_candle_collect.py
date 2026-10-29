"""Collect Toss one-minute candles for prospective domestic day-trade candidates."""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from .domestic_horizon_research import PATH, connect, harvest

KST = timezone(timedelta(hours=9))
MAX_PAGES = 6
TIMESTAMP_BASIS = "bar_start_v2"


def ensure_timestamp_basis(db):
    """Archive the incorrectly labelled dataset before collecting corrected bars."""
    db.execute("BEGIN IMMEDIATE")
    try:
        saved = db.execute("SELECT value FROM metadata WHERE key='minute_timestamp_basis'").fetchone()
        if saved and saved[0] != TIMESTAMP_BASIS:
            raise ValueError("Unknown minute timestamp basis")
        if not saved:
            for table in ("minute_bars", "minute_quality", "day_signals"):
                if db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]:
                    schema = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
                    db.execute(f"ALTER TABLE {table} RENAME TO {table}_timestamp_v1")
                    db.execute(schema)
            for key in ("day_signal_version", "day_signal_rules"):
                db.execute("INSERT OR IGNORE INTO metadata SELECT key||'_timestamp_v1',value FROM metadata WHERE key=?", (key,))
                db.execute("DELETE FROM metadata WHERE key=?", (key,))
            db.execute("INSERT INTO metadata VALUES ('minute_timestamp_basis',?)", (TIMESTAMP_BASIS,))
            db.execute("INSERT INTO metadata VALUES ('minute_timestamp_migrated_at',?)", (datetime.now(KST).isoformat(timespec="seconds"),))
        db.commit()
    except Exception:
        db.rollback()
        raise


def normalize(candle, ticker, day, now):
    try:
        stamp = datetime.fromisoformat(str(candle["timestamp"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Minute timestamp needs a timezone")
        # Toss 1m timestamps mark the end of [timestamp - 1 minute, timestamp).
        stamp = stamp.astimezone(KST) - timedelta(minutes=1)
        if stamp.date() != day or not (9 <= stamp.hour < 16) or stamp.time() >= datetime.strptime("15:30", "%H:%M").time():
            return None
        if stamp.second or stamp.microsecond or stamp >= now.replace(second=0, microsecond=0):
            return None
        prices = [float(candle[key]) for key in ("openPrice", "highPrice", "lowPrice", "closePrice")]
        volume = int(candle["volume"])
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid minute candle for {ticker}: {error}") from error
    opening, high, low, close = prices
    if (not all(0 < value < float("inf") for value in prices) or high < max(opening, close)
            or low > min(opening, close) or low > high or volume < 0):
        raise ValueError(f"Invalid OHLCV for {ticker} at {stamp.isoformat()}")
    return (ticker, stamp.isoformat(timespec="seconds"), opening, high, low, close,
            volume, "toss_unadjusted_start_v2", now.isoformat(timespec="seconds"))


def fetch_day(client, ticker, day, now, diagnostics=None):
    """Page backward until the first regular-hours minute is crossed."""
    rows, seen = {}, set()
    before = datetime.combine(day, datetime.min.time(), KST).replace(hour=15, minute=30).isoformat()
    duplicate_source = 0
    for _ in range(MAX_PAGES):
        result = client.candles(ticker, interval="1m", count=200, before=before, adjusted=False)
        page = result.get("candles") or []
        if not page:
            break
        stamps = []
        for candle in page:
            stamp = datetime.fromisoformat(str(candle["timestamp"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Minute timestamp needs a timezone")
            stamps.append(stamp.astimezone(KST))
            row = normalize(candle, ticker, day, now)
            if row:
                old = rows.get(row[1])
                if old and old[2:7] != row[2:7]:
                    raise ValueError(f"Conflicting minute candle for {ticker} at {row[1]}")
                if old:
                    duplicate_source += 1
                rows[row[1]] = row
        if min(stamps).date() < day or min(stamps) <= datetime.combine(day, datetime.min.time(), KST).replace(hour=9):
            break
        next_before = result.get("nextBefore")
        if not next_before or next_before == before or next_before in seen:
            break
        seen.add(next_before)
        before = next_before
    if diagnostics is not None:
        diagnostics["duplicate_source"] = duplicate_source
    return list(rows.values())


def run(path=PATH, source=None, client=None, now=None):
    from .data_store import DB_PATH
    from .toss_client import shared_client
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        raise ValueError("Collection time needs a timezone")
    now = now.astimezone(KST)
    harvested = harvest(source or DB_PATH, path)
    with closing(connect(path)) as db:
        ensure_timestamp_basis(db)
        candidates = db.execute("""SELECT o.ticker,o.signal_day,o.updated_at,o.error,q.missing
            FROM observations o LEFT JOIN minute_quality q ON q.ticker=o.ticker AND q.signal_day=o.signal_day
            WHERE o.kind='day' AND o.signal_day<=? ORDER BY o.signal_day,o.ticker""",
            (now.date().isoformat(),)).fetchall()
        candidates = [row for row in candidates if row["signal_day"] == now.date().isoformat()
                      or row["missing"] is None or not row["updated_at"] or row["error"]
                      or (row["missing"] and row["signal_day"] >= (now.date()-timedelta(days=3)).isoformat())]
        if not candidates:
            return {"candidates": 0, "bars_added": 0, "failures": 0, "harvested": harvested}
        if client is None:
            client = shared_client()
        added = failures = 0
        for item in candidates:
            ticker, day = item["ticker"], date.fromisoformat(item["signal_day"])
            try:
                from .day_trade_research import record
                diagnostics = {}
                bars = fetch_day(client, ticker, day, now, diagnostics)
                candidate_added = 0
                # Never overwrite the first version of a bar without an explicit revision audit.
                for bar in bars:
                    previous = db.execute("""SELECT open,high,low,close,volume FROM minute_bars
                        WHERE ticker=? AND bar_at=?""", bar[:2]).fetchone()
                    if previous and tuple(previous) != bar[2:7]:
                        raise ValueError(f"Revised minute candle for {ticker} at {bar[1]}")
                    candidate_added += db.execute("""INSERT OR IGNORE INTO minute_bars
                        (ticker,bar_at,open,high,low,close,volume,source,collected_at)
                        VALUES (?,?,?,?,?,?,?,?,?)""", bar).rowcount
                observed_at = db.execute("""SELECT observed_at FROM observations
                    WHERE kind='day' AND ticker=? AND signal_day=?""", (ticker, item["signal_day"])).fetchone()[0]
                record(db, ticker, day, observed_at, now, diagnostics.get("duplicate_source", 0))
                db.execute("""UPDATE observations SET updated_at=?,error=NULL
                    WHERE kind='day' AND ticker=? AND signal_day=?""",
                    (now.isoformat(timespec="seconds"), ticker, item["signal_day"]))
                db.commit()
                added += candidate_added
            except Exception as error:
                db.rollback()
                failures += 1
                db.execute("""UPDATE observations SET updated_at=?,error=?
                    WHERE kind='day' AND ticker=? AND signal_day=?""",
                    (now.isoformat(timespec="seconds"), str(error), ticker, item["signal_day"]))
                db.commit()
    return {"candidates": len(candidates), "bars_added": added, "failures": failures, "harvested": harvested}


def main():
    from .app import load_env
    load_env()
    print(run())


if __name__ == "__main__":
    main()
