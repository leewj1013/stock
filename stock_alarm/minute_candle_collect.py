"""Collect Toss one-minute candles for prospective domestic day-trade candidates."""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone

from .domestic_horizon_research import PATH, connect, harvest

KST = timezone(timedelta(hours=9))
MAX_PAGES = 6


def normalize(candle, ticker, day, now):
    try:
        stamp = datetime.fromisoformat(str(candle["timestamp"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Minute timestamp needs a timezone")
        stamp = stamp.astimezone(KST)
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
            volume, "toss_unadjusted", now.isoformat(timespec="seconds"))


def fetch_day(client, ticker, day, now):
    """Page backward until the first regular-hours minute is crossed."""
    rows, before, seen = {}, None, set()
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
                rows[row[1]] = row
        if min(stamps).date() < day or min(stamps) <= datetime.combine(day, datetime.min.time(), KST).replace(hour=9):
            break
        next_before = result.get("nextBefore")
        if not next_before or next_before == before or next_before in seen:
            break
        seen.add(next_before)
        before = next_before
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
        candidates = db.execute("""SELECT ticker,signal_day,updated_at,error FROM observations
            WHERE kind='day' AND signal_day>=? AND signal_day<=? ORDER BY signal_day,ticker""",
            ((now.date() - timedelta(days=3)).isoformat(), now.date().isoformat())).fetchall()
        candidates = [row for row in candidates if row["signal_day"] == now.date().isoformat()
                      or not row["updated_at"] or row["error"]]
        if not candidates:
            return {"candidates": 0, "bars_added": 0, "failures": 0, "harvested": harvested}
        if client is None:
            client = shared_client()
        added = failures = 0
        for item in candidates:
            ticker, day = item["ticker"], date.fromisoformat(item["signal_day"])
            try:
                bars = fetch_day(client, ticker, day, now)
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
