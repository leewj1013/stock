import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path

from stock_alarm.point_in_time_store import KST, PointInTimeStore, connect, conservative_date_availability, iso_utc


class PointInTimeStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "pit.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_future_news_is_never_visible(self):
        with closing(connect(self.path)) as db:
            for title, published, available in (
                ("호실적 성장 발표", datetime(2024, 1, 2, 10, tzinfo=KST), datetime(2024, 1, 2, 10, tzinfo=KST)),
                ("악재 손실 발표", datetime(2024, 1, 2, 16, tzinfo=KST), datetime(2024, 1, 2, 16, tzinfo=KST)),
            ):
                db.execute("INSERT INTO news_events VALUES(?,?,?,?,?,?,?,?)", ("005930", title, "", iso_utc(published), iso_utc(available), iso_utc(available), "test", "{}"))
            db.commit()
        result = PointInTimeStore(self.path).scores_asof("005930", date(2024, 1, 2))
        self.assertEqual(result["news_score"], 1.0)
        self.assertTrue(result["pit_sources"]["news"])

    def test_date_only_buffer_skips_weekend(self):
        available = conservative_date_availability(date(2024, 1, 5), 1)
        self.assertEqual(available, datetime.combine(date(2024, 1, 8), time.min, KST))

    def test_financial_snapshot_only_after_availability(self):
        with closing(connect(self.path)) as db:
            db.execute("INSERT INTO financial_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                "005930", "2024-01-02", iso_utc(datetime(2024, 1, 2, tzinfo=KST)),
                iso_utc(datetime(2024, 1, 3, tzinfo=KST)), 10, 1, 2, 100, 1000,
                "test", "next_day", "test", iso_utc(datetime(2024, 1, 3, tzinfo=KST))))
            db.commit()
        store = PointInTimeStore(self.path)
        self.assertFalse(store.scores_asof("005930", date(2024, 1, 2))["pit_sources"]["financial"])
        self.assertEqual(store.scores_asof("005930", date(2024, 1, 3))["financial_score"], 5.0)

    def test_coverage_counts_sources(self):
        with closing(connect(self.path)) as db:
            db.execute("INSERT INTO news_events VALUES(?,?,?,?,?,?,?,?)", ("005930", "호실적 성장 뉴스", "", "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00", "test", "{}"))
            db.commit()
        rows = PointInTimeStore(self.path).coverage(["005930"], date(2024, 1, 1), date(2024, 1, 2))
        self.assertEqual({row["source"]: row["records"] for row in rows}, {"news": 1, "disclosure": 0, "financial": 0})

    def test_partial_collection_marks_only_actual_news_coverage_as_observed(self):
        with closing(connect(self.path)) as db:
            db.execute(
                "INSERT INTO collection_log(source,ticker,start_date,end_date,status,records,message,collected_at) VALUES(?,?,?,?,?,?,?,?)",
                ("news", "005930", "2024-06-01", "2024-12-31", "partial", 1000, "provider limit", "2025-01-01T00:00:00+00:00"),
            )
            db.commit()

        store = PointInTimeStore(self.path)
        self.assertFalse(store.scores_asof("005930", date(2024, 5, 31))["pit_sources"]["news"])
        self.assertTrue(store.scores_asof("005930", date(2024, 6, 1))["pit_sources"]["news"])


if __name__ == "__main__":
    unittest.main()
