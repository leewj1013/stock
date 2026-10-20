import tempfile
import unittest
from pathlib import Path

from stock_alarm import theme_research as research


def item(day="2026-10-20", change="3", rising="3", falling="1", trading="10000000000"):
    return {"code": "529", "name": "AI", "changeRate": change, "risingCount": rising,
            "fallingCount": falling, "unchangedCount": "0", "totalTradingValue": trading,
            "topByTradingValue": [{"code": "005930", "name": "삼성전자", "value": trading}],
            "updatedAt": day + "T15:40:00+09:00"}


class ThemeResearchTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "theme.db")

    def test_eligibility_requires_breadth_change_members_and_liquidity(self):
        self.assertTrue(research.eligible(research.normalize(item())))
        self.assertFalse(research.eligible(research.normalize(item(rising="1", falling="3"))))
        self.assertFalse(research.eligible(research.normalize(item(change="1.9"))))

    def test_collection_starts_episode_once_until_theme_reactivates(self):
        first = research.collect(self.path, [item()])
        second = research.collect(self.path, [item()])
        self.assertEqual((1, 0), (first["episodes"], second["episodes"]))

    def test_two_weak_sessions_trigger_reduction(self):
        snapshots = [
            {"market_day": "2026-10-21", "breadth": .3, "change_pct": 1},
            {"market_day": "2026-10-22", "breadth": .6, "change_pct": -1},
        ]
        self.assertEqual("2026-10-22", research.weak_trigger_day(snapshots))

    def test_confirmation_enters_only_after_confirming_close(self):
        bars = []
        for day in range(1, 21):
            bars.append([f"202610{day:02d}", 100, 101, 99, 100, 1000])
        bars += [["20261021", 101, 103, 100, 102, 1000], ["20261022", 103, 105, 102, 104, 1000],
                 ["20261023", 105, 107, 104, 106, 1000], ["20261024", 106, 108, 105, 107, 1000]]
        snapshots = [dict(research.normalize(item("2026-10-20"))), dict(research.normalize(item("2026-10-21")))]
        result = research.simulate("2026-10-20", bars, snapshots)
        self.assertEqual("20261021", result["first_entry_day"])
        self.assertEqual("20261022", result["confirmed_entry_day"])
        self.assertIsNone(result["horizons"]["1"]["confirmed_entry_return_pct"])
        self.assertIsNotNone(result["horizons"]["3"]["confirmed_entry_return_pct"])

    def test_empty_dashboard(self):
        self.assertIn("관찰 DB 생성 전", research.dashboard_section(self.path))


if __name__ == "__main__":
    unittest.main()
