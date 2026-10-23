from contextlib import closing
from datetime import date, datetime, timedelta

from stock_alarm import day_trade_research as research
from stock_alarm.domestic_horizon_research import connect


DAY = date(2026, 10, 23)


def put_bars(db, through="09:40", missing=(), breakout=None):
    cursor = datetime.fromisoformat("2026-10-23T09:00:00+09:00")
    end = datetime.fromisoformat(f"2026-10-23T{through}:00+09:00")
    while cursor <= end:
        clock = cursor.strftime("%H:%M")
        if clock not in missing:
            close = 102 if clock == breakout else 100
            volume = 20 if clock == breakout else 10
            db.execute("""INSERT OR REPLACE INTO minute_bars VALUES (?,?,?,?,?,?,?,?,?)""",
                       ("005930", cursor.isoformat(timespec="seconds"), close, close + 1, close - 1,
                        close, volume, "toss_unadjusted", "2026-10-23T11:00:00+09:00"))
        cursor += timedelta(minutes=1)
    db.commit()


def test_signal_uses_complete_opening_range_and_only_post_observation_crossing(tmp_path):
    with closing(connect(str(tmp_path / "research.db"))) as db:
        put_bars(db, breakout="09:36")
        now = datetime.fromisoformat("2026-10-23T09:41:00+09:00")
        quality, state, signal = research.record(db, "005930", DAY, "2026-10-23T09:35:00", now)
        assert quality["opening_missing"] == 0
        assert state == "SIGNAL_RECORDED"
        assert "T09:36+09:00" in signal["trigger_bar_at"]
        assert "T09:37+09:00" in signal["entry_earliest_at"]
        assert "T15:20+09:00" in signal["planned_exit_at"]
        put_bars(db, through="09:45", breakout="09:36")
        research.record(db, "005930", DAY, "2026-10-23T09:35:00",
                        datetime.fromisoformat("2026-10-23T09:46:00+09:00"))
        assert db.execute("SELECT state FROM day_signals").fetchone()[0] == "SIGNAL_RECORDED"


def test_pre_observation_breakout_cannot_be_claimed(tmp_path):
    with closing(connect(str(tmp_path / "research.db"))) as db:
        put_bars(db, breakout="09:34")
        _, state, signal = research.assess(db, "005930", DAY, "2026-10-23T09:35:00",
                                           datetime.fromisoformat("2026-10-23T09:41:00+09:00"))
        assert state == "OBSERVING" and signal is None


def test_missing_opening_or_intermediate_minute_blocks_signal(tmp_path):
    with closing(connect(str(tmp_path / "research.db"))) as db:
        put_bars(db, missing={"09:10", "09:35"}, breakout="09:36")
        quality, state, signal = research.assess(db, "005930", DAY, "2026-10-23T09:34:00",
                                                  datetime.fromisoformat("2026-10-23T11:01:00+09:00"))
        assert quality["opening_missing"] == 1
        assert quality["missing"] > 0
        assert state == "QUALITY_BLOCKED" and signal is None


def test_no_signal_only_after_complete_cutoff(tmp_path):
    with closing(connect(str(tmp_path / "research.db"))) as db:
        put_bars(db, through="10:59")
        quality, state, signal = research.record(db, "005930", DAY, "2026-10-23T09:35:00",
                                                 datetime.fromisoformat("2026-10-23T11:00:00+09:00"), 2)
        assert quality["missing"] == 0 and quality["duplicate_source"] == 2
        assert state == "NO_SIGNAL" and signal is None
        assert db.execute("SELECT status FROM minute_quality").fetchone()[0] == "COLLECTING"
        research.record(db, "005930", DAY, "2026-10-23T09:35:00",
                        datetime.fromisoformat("2026-10-23T11:01:00+09:00"))
        assert db.execute("SELECT duplicate_source FROM minute_quality").fetchone()[0] == 2
