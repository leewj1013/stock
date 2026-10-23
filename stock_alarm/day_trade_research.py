"""Frozen opening-range signal and minute-candle quality checks; no simulated fills."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from statistics import median

KST = timezone(timedelta(hours=9))

VERSION = "kr-opening-range-v1"
RULES = {"opening_start": "09:00", "opening_end": "09:29", "signal_end": "10:59",
         "relative_volume_min": 1.5, "planned_entry": "next_minute_open",
         "planned_exit": "15:20_next_minute_open", "opening_volume_excludes": "09:00"}


def _minute(day: date, clock: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(clock), KST)


def _stored_bars(db, ticker, day):
    return {datetime.fromisoformat(row["bar_at"]): dict(row) for row in db.execute(
        "SELECT * FROM minute_bars WHERE ticker=? AND substr(bar_at,1,10)=? ORDER BY bar_at",
        (ticker, day.isoformat()))}


def assess(db, ticker, day, observed_at, now, duplicate_source=0):
    """Assess only completed minutes; missing bars remain unknown, never zero-volume fills."""
    if now.tzinfo is None:
        raise ValueError("Assessment time needs a timezone")
    now = now.astimezone(KST)
    bars = _stored_bars(db, ticker, day)
    opening = [_minute(day, "09:00") + timedelta(minutes=i) for i in range(30)]
    last_expected = min(_minute(day, "15:29"), now.replace(second=0, microsecond=0) - timedelta(minutes=1))
    expected = []
    cursor = _minute(day, "09:00")
    while cursor <= last_expected:
        expected.append(cursor)
        cursor += timedelta(minutes=1)
    missing = [stamp for stamp in expected if stamp not in bars]
    opening_missing = [stamp for stamp in opening if stamp not in bars]
    final = now >= _minute(day, "15:30") or now.date() > day
    quality = {"expected": len(expected), "received": len(expected) - len(missing),
               "missing": len(missing), "opening_missing": len(opening_missing),
               "missing_first": [stamp.isoformat(timespec="minutes") for stamp in missing[:10]],
               "duplicate_source": duplicate_source,
               "status": "INCOMPLETE" if missing else "COMPLETE" if final else "COLLECTING"}
    signal = None
    state = ("QUALITY_BLOCKED" if last_expected >= _minute(day, RULES["signal_end"]) else "WAITING_OPENING") if opening_missing else "OBSERVING"
    if not opening_missing:
        opening_high = max(bars[stamp]["high"] for stamp in opening)
        opening_low = min(bars[stamp]["low"] for stamp in opening)
        baseline_volume = median(bars[stamp]["volume"] for stamp in opening[1:])
        if baseline_volume <= 0:
            state = "QUALITY_BLOCKED"
        else:
            observed = datetime.fromisoformat(observed_at)
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=KST)
            observed = observed.astimezone(KST)
            first = _minute(day, "09:30")
            end = min(_minute(day, RULES["signal_end"]), last_expected)
            cursor = first
            contiguous = True
            while cursor <= end:
                previous = cursor - timedelta(minutes=1)
                if cursor not in bars or previous not in bars:
                    contiguous = False
                elif contiguous and cursor > observed and bars[previous]["close"] <= opening_high:
                    bar = bars[cursor]
                    if (bar["close"] > opening_high
                            and bar["volume"] >= RULES["relative_volume_min"] * baseline_volume):
                        signal = {"rule_version": VERSION, "trigger_bar_at": cursor.isoformat(timespec="minutes"),
                                  "entry_earliest_at": (cursor + timedelta(minutes=1)).isoformat(timespec="minutes"),
                                  "planned_exit_at": _minute(day, "15:20").isoformat(timespec="minutes"),
                                  "opening_high": opening_high, "opening_low": opening_low,
                                  "trigger_close": bar["close"], "trigger_volume": bar["volume"],
                                  "opening_median_volume": baseline_volume,
                                  "observed_at": observed.isoformat(timespec="seconds")}
                        state = "SIGNAL_RECORDED"
                        break
                cursor += timedelta(minutes=1)
            if signal is None and end >= _minute(day, RULES["signal_end"]):
                state = "NO_SIGNAL" if contiguous else "QUALITY_BLOCKED"
            elif signal is None and not contiguous:
                state = "QUALITY_BLOCKED"
    return quality, state, signal


def record(db, ticker, day, observed_at, now, duplicate_source=0):
    db.execute("INSERT OR IGNORE INTO metadata VALUES ('day_signal_version',?)", (VERSION,))
    db.execute("INSERT OR IGNORE INTO metadata VALUES ('day_signal_rules',?)", (json.dumps(RULES, sort_keys=True),))
    saved = dict(db.execute("SELECT key,value FROM metadata WHERE key LIKE 'day_signal_%'"))
    if saved["day_signal_version"] != VERSION or json.loads(saved["day_signal_rules"]) != RULES:
        raise ValueError("Day signal rules changed; use a new versioned database")
    previous = db.execute("SELECT duplicate_source FROM minute_quality WHERE ticker=? AND signal_day=?",
                          (ticker, day.isoformat())).fetchone()
    duplicate_source = max(duplicate_source, previous[0] if previous else 0)
    quality, state, signal = assess(db, ticker, day, observed_at, now, duplicate_source)
    db.execute("""INSERT INTO minute_quality VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ticker,signal_day) DO UPDATE SET checked_at=excluded.checked_at,
        expected=excluded.expected,received=excluded.received,missing=excluded.missing,
        opening_missing=excluded.opening_missing,duplicate_source=excluded.duplicate_source,
        status=excluded.status,details=excluded.details""",
        (ticker, day.isoformat(), now.isoformat(timespec="seconds"), quality["expected"],
         quality["received"], quality["missing"], quality["opening_missing"],
         quality["duplicate_source"], quality["status"], json.dumps(quality)))
    # A signal is frozen after its first observation. Never replace it with a later revision.
    existing = db.execute("SELECT state FROM day_signals WHERE ticker=? AND signal_day=? AND rule_version=?",
                          (ticker, day.isoformat(), VERSION)).fetchone()
    if not existing or existing["state"] != "SIGNAL_RECORDED":
        db.execute("""INSERT INTO day_signals VALUES (?,?,?,?,?,?) ON CONFLICT(ticker,signal_day,rule_version)
            DO UPDATE SET state=excluded.state,checked_at=excluded.checked_at,signal_json=excluded.signal_json""",
            (ticker, day.isoformat(), VERSION, state, now.isoformat(timespec="seconds"),
             json.dumps(signal, ensure_ascii=False) if signal else None))
    return quality, state, signal
