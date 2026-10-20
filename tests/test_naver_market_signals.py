import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from stock_alarm import naver_market_signals as signals


class NaverMarketSignalsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "signals.db")

    def test_parses_source_days_and_keeps_empty_ranking_on_basis_day(self):
        rows = signals._flow_rows({"sections": {"buyRankList": [
            {"itemcode": "005930", "bizdateTo": "20261020", "itemname": "삼성전자"}]}}, "buyRankList")
        self.assertEqual("2026-10-20", rows[0]["market_day"])
        with closing(signals.connect(self.path)) as db:
            signals._save(db, "risk", "ALL", [], "2026-10-21T07:30:00+09:00", "2026-10-20")
            signals._save(db, "risk", "ALL", [], "2026-10-22T07:30:00+09:00", "2026-10-21")
        state, members = signals.latest("risk", path=self.path)
        self.assertEqual("2026-10-21", state["last_day"])
        self.assertEqual([], members)

    def test_harvest_uses_only_observations_before_recommendation(self):
        source_time = "2026-10-21T09:00:00+09:00"
        with closing(signals.connect(self.path)) as db:
            signals._save(db, "high52", "ALL", [{"market_day": "2026-10-20", "ticker": "005930",
                "rank": 1, "payload": {"itemname": "삼성전자"}}], source_time)
            signals._save(db, "low52", "ALL", [{"market_day": "2026-10-20", "ticker": "005930",
                "rank": 1, "payload": {"itemname": "삼성전자"}}], "2026-10-21T09:10:00+09:00")
        candidate = {"ticker": "005930", "name": "삼성전자", "evaluated_at": "2026-10-21T09:05:00",
                     "benchmark_symbol": "KOSPI"}
        with patch.object(signals, "query_rows", return_value=[candidate]):
            self.assertEqual(1, signals.harvest(path=self.path))
        with closing(signals.connect(self.path)) as db:
            payload = json.loads(db.execute("SELECT payload FROM recommendation_signals").fetchone()[0])
        self.assertEqual(1, payload["high52"]["rank"])
        self.assertNotIn("low52", payload)


if __name__ == "__main__":
    unittest.main()
