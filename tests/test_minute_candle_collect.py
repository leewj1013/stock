from contextlib import closing
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from stock_alarm import domestic_horizon_research as research
from stock_alarm import minute_candle_collect as collector


def candle(time, price=100, volume=10):
    end = datetime.fromisoformat(f"2026-10-23T{time}:00+09:00") + timedelta(minutes=1)
    return {"timestamp": end.isoformat(), "openPrice": str(price),
            "highPrice": str(price + 1), "lowPrice": str(price - 1),
            "closePrice": str(price), "volume": str(volume)}


def test_fetch_pages_and_excludes_future_and_nonregular_times():
    client = Mock()
    client.candles.side_effect = [
        {"candles": [candle("09:40"), candle("09:35")], "nextBefore": "page2"},
        {"candles": [candle("09:00"), candle("08:59")], "nextBefore": "page3"},
    ]
    now = datetime.fromisoformat("2026-10-23T09:40:30+09:00")
    bars = collector.fetch_day(client, "005930", date(2026, 10, 23), now)
    assert [bar[1][11:16] for bar in bars] == ["09:35", "09:00"]
    assert all(bar[7] == "toss_unadjusted_start_v2" for bar in bars)
    assert client.candles.call_count == 2
    assert client.candles.call_args_list[0].kwargs["adjusted"] is False


def test_rejects_missing_timezone_and_invalid_price():
    now = datetime.fromisoformat("2026-10-23T10:00:00+09:00")
    bar = candle("09:30")
    bar["timestamp"] = "2026-10-23T09:30:00"
    with pytest.raises(ValueError, match="timezone"):
        collector.normalize(bar, "005930", date(2026, 10, 23), now)
    bad = candle("09:30")
    bad["highPrice"] = "90"
    with pytest.raises(ValueError, match="Invalid OHLCV"):
        collector.normalize(bad, "005930", date(2026, 10, 23), now)


def test_run_persists_only_candidates_and_is_idempotent(tmp_path):
    path = str(tmp_path / "research.db")
    with closing(research.connect(path)) as db:
        db.execute("""INSERT INTO observations(kind,ticker,signal_day,observed_at,payload,state)
            VALUES ('day','005930','2026-10-23','2026-10-23T09:35:00','{}','DATA_REQUIRED')""")
        db.commit()
    client = Mock()
    client.candles.return_value = {"candles": [candle("09:00"), candle("09:01")], "nextBefore": None}
    now = datetime.fromisoformat("2026-10-23T10:00:00+09:00")
    with patch.object(collector, "harvest", return_value={"swing": 0, "day": 0}):
        assert collector.run(path=path, client=client, now=now)["bars_added"] == 2
        assert collector.run(path=path, client=client, now=now)["bars_added"] == 0
    with closing(research.connect(path)) as db:
        assert db.execute("SELECT count(*) FROM minute_bars").fetchone()[0] == 2
        assert db.execute("SELECT error FROM observations").fetchone()[0] is None
        assert db.execute("SELECT opening_missing FROM minute_quality").fetchone()[0] == 28
        assert db.execute("SELECT state FROM day_signals").fetchone()[0] == "WAITING_OPENING"


def test_revised_bar_is_not_silently_overwritten(tmp_path):
    path = str(tmp_path / "research.db")
    with closing(research.connect(path)) as db:
        db.execute("""INSERT INTO observations(kind,ticker,signal_day,observed_at,payload,state)
            VALUES ('day','005930','2026-10-23','2026-10-23T09:35:00','{}','DATA_REQUIRED')""")
        db.commit()
    client = Mock()
    client.candles.return_value = {"candles": [candle("09:00")], "nextBefore": None}
    now = datetime.fromisoformat("2026-10-23T10:00:00+09:00")
    with patch.object(collector, "harvest", return_value={"swing": 0, "day": 0}):
        collector.run(path=path, client=client, now=now)
        client.candles.return_value = {"candles": [candle("09:00", price=101)], "nextBefore": None}
        result = collector.run(path=path, client=client, now=now)
    assert result["failures"] == 1
    with closing(research.connect(path)) as db:
        assert db.execute("SELECT close FROM minute_bars").fetchone()[0] == 100
        assert "Revised" in db.execute("SELECT error FROM observations").fetchone()[0]


def test_identical_source_duplicate_is_counted_without_creating_two_bars():
    client = Mock()
    client.candles.return_value = {"candles": [candle("09:00"), candle("09:00")], "nextBefore": None}
    diagnostics = {}
    rows = collector.fetch_day(client, "005930", date(2026, 10, 23),
                               datetime.fromisoformat("2026-10-23T10:00:00+09:00"), diagnostics)
    assert len(rows) == 1
    assert diagnostics["duplicate_source"] == 1


def test_end_timestamp_becomes_start_and_last_regular_minute_is_kept():
    day = date(2026,10,23)
    now = datetime.fromisoformat("2026-10-23T15:30:00+09:00")
    assert collector.normalize(candle("09:00"),"005930",day,now)[1][11:16] == "09:00"
    assert collector.normalize(candle("15:29"),"005930",day,now)[1][11:16] == "15:29"
    assert collector.normalize(candle("15:30"),"005930",day,now) is None
    assert collector.normalize(candle("10:00"),"005930",day,datetime.fromisoformat("2026-10-23T10:00:30+09:00")) is None


def test_legacy_dataset_and_signals_are_archived_once(tmp_path):
    path = str(tmp_path/"research.db")
    with closing(research.connect(path)) as db:
        db.execute("INSERT INTO minute_bars VALUES('A','2026-10-23T09:01:00+09:00',100,101,99,100,10,'toss_unadjusted','2026-10-23')")
        db.execute("INSERT INTO metadata VALUES('day_signal_version','kr-opening-range-v1')")
        db.execute("INSERT INTO day_signals VALUES('A','2026-10-23','kr-opening-range-v1','NO_SIGNAL','2026-10-23',NULL)")
        db.commit()
        collector.ensure_timestamp_basis(db)
        assert db.execute("SELECT count(*) FROM minute_bars").fetchone()[0] == 0
        assert db.execute("SELECT bar_at FROM minute_bars_timestamp_v1").fetchone()[0] == '2026-10-23T09:01:00+09:00'
        assert db.execute("SELECT count(*) FROM day_signals_timestamp_v1").fetchone()[0] == 1
        assert db.execute("SELECT value FROM metadata WHERE key='day_signal_version_timestamp_v1'").fetchone()[0] == 'kr-opening-range-v1'
        collector.ensure_timestamp_basis(db)
        assert db.execute("SELECT count(*) FROM minute_bars_timestamp_v1").fetchone()[0] == 1
