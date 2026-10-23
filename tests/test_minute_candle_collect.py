from contextlib import closing
from datetime import date, datetime
from unittest.mock import Mock, patch

import pytest

from stock_alarm import domestic_horizon_research as research
from stock_alarm import minute_candle_collect as collector


def candle(time, price=100, volume=10):
    return {"timestamp": f"2026-10-23T{time}:00+09:00", "openPrice": str(price),
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
    assert all(bar[7] == "toss_unadjusted" for bar in bars)
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
