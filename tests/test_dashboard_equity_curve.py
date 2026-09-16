import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from stock_alarm.dashboard import equity_curve_section, equity_curve_series, time_weighted_returns

SNAPSHOTS = [
    ("2026-09-14T15:30:00", 10_000_000),
    ("2026-09-15T15:30:00", 10_100_000),   # +1.0%
    ("2026-09-16T15:30:00", 100_099_000),  # 9,000만원 deposit landed overnight
]
DEPOSITS = [("2026-09-14T09:00:00", 10_000_000), ("2026-09-15T21:19:00", 90_000_000)]


def build_db(path):
    with closing(sqlite3.connect(path)) as db:
        db.executescript(
            "CREATE TABLE virtual_valuation_snapshots(snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " created_at TEXT, equity INTEGER);"
            "CREATE TABLE virtual_deposits(deposit_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, amount INTEGER);"
        )
        db.executemany("INSERT INTO virtual_valuation_snapshots(created_at, equity) VALUES(?,?)", SNAPSHOTS)
        db.executemany("INSERT INTO virtual_deposits(created_at, amount) VALUES(?,?)", DEPOSITS)
        db.commit()


class EquityCurveTest(unittest.TestCase):
    def test_deposit_day_is_not_counted_as_a_gain(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "account.db")
            build_db(path)
            curve = time_weighted_returns(path)

        self.assertEqual(0.0, curve["2026-09-14"])
        self.assertEqual(1.0, curve["2026-09-15"])
        # equity went 10.1M -> 100.099M, but 90M of it was deposited, so the day
        # itself lost 0.01% and the cumulative curve stays at +0.99%, not +891%.
        self.assertAlmostEqual(0.99, curve["2026-09-16"], places=2)

    def test_curve_needs_two_days_before_it_draws(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "one.db")
            with closing(sqlite3.connect(path)) as db:
                db.executescript(
                    "CREATE TABLE virtual_valuation_snapshots(snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " created_at TEXT, equity INTEGER);"
                    "CREATE TABLE virtual_deposits(deposit_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, amount INTEGER);"
                )
                db.execute("INSERT INTO virtual_valuation_snapshots(created_at, equity) VALUES('2026-09-16T15:30:00', 100)")
                db.commit()
            profiles = {"aggressive": {"db_path": path}}
            with patch.dict("stock_alarm.trading_profiles.PROFILES", profiles, clear=True):
                self.assertEqual({"days": [], "series": []}, equity_curve_series())

    @patch("stock_alarm.dashboard.equity_curve_series", return_value={"days": [], "series": []})
    def test_empty_state_explains_itself(self, _series):
        html = equity_curve_section()
        self.assertIn("표시할 기록이 없습니다", html)
        self.assertNotIn("<polyline", html)

    @patch("stock_alarm.dashboard.equity_curve_series")
    def test_section_draws_one_line_per_series_with_latest_value(self, series):
        series.return_value = {
            "days": ["2026-09-14", "2026-09-15", "2026-09-16"],
            "series": [
                {"key": "aggressive", "label": "적극투자형", "color": "#378ADD", "points": [0.0, 1.0, 0.9]},
                {"key": "benchmark", "label": "KOSPI", "color": "#888780", "points": [0.0, None, -1.3], "dashed": True},
            ],
        }
        html = equity_curve_section()
        self.assertEqual(2, html.count("<polyline"))
        self.assertIn("+0.90%", html)
        self.assertIn("-1.30%", html)
        self.assertIn("stroke-dasharray", html)


if __name__ == "__main__":
    unittest.main()
