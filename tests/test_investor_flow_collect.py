import sqlite3
import unittest
from unittest.mock import patch

from stock_alarm import investor_flow_collect as flow

DAYS = [f"2026{m:02d}{d:02d}" for m in range(1, 10) for d in (5, 15, 25)]  # 27 fake sessions


def fake_page(ticker, bizdate):
    """Like the API: up to 3 sessions strictly before bizdate, newest first."""
    before = [d for d in DAYS if d < bizdate][-3:]
    return [{"itemCode": ticker, "bizdate": d, "foreignerPureBuyQuant": "+1", "organPureBuyQuant": "-1",
             "individualPureBuyQuant": "0", "foreignerHoldRatio": "1.0%", "closePrice": "100",
             "accumulatedTradingVolume": "10"} for d in reversed(before)]


class CollectTickerTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute(flow.SCHEMA)

    def stored(self):
        return [row[0] for row in self.db.execute("SELECT bizdate FROM investor_flow ORDER BY bizdate")]

    @patch.object(flow, "fetch_page", side_effect=fake_page)
    @patch.object(flow.time, "sleep")
    @patch.object(flow, "date")
    def test_later_runs_fill_new_sessions_and_keep_backfilling(self, fake_date, _sleep, _fetch):
        fake_date.today.return_value.strftime.return_value = "20260601"
        flow.collect_ticker(self.db, "A", "20260301", pause=0)
        # Whole pages are kept, so a few sessions just below start may come along.
        self.assertLessEqual({d for d in DAYS if "20260301" <= d < "20260601"}, set(self.stored()))
        self.assertLess(max(self.stored()), "20260601")

        # A week later, with an earlier start: new sessions on top, older ones below.
        fake_date.today.return_value.strftime.return_value = "20260901"
        flow.collect_ticker(self.db, "A", "20260101", pause=0)
        self.assertLessEqual({d for d in DAYS if "20260101" <= d < "20260901"}, set(self.stored()))
        self.assertLess(max(self.stored()), "20260901")


if __name__ == "__main__":
    unittest.main()
