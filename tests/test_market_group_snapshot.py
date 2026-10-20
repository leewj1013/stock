import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from stock_alarm import market_group_snapshot as groups
from stock_alarm import market_group_context as context


def item(code, name, cap, change, category="industries"):
    return {"code": code, "name": name, "changeRate": str(change), "totalMarketCap": str(cap),
            "totalTradingValue": "5000000000", "risingCount": "2", "fallingCount": "1",
            "unchangedCount": "0", "updatedAt": "2026-10-20T15:30:00+09:00",
            "topByMarketCap": [{"code": "005930", "name": "삼성전자"}],
            "topByTradingValue": [{"code": "005930", "name": "삼성전자"}]}


class MarketGroupSnapshotTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "groups.db")

    def test_market_cap_top_ten_is_sorted_by_cap_not_change(self):
        industries = [item(str(i), f"업종{i}", i * 1_000_000_000_000, 100 - i) for i in range(1, 12)]
        themes = [item("T1", "테마1", 0, 3, "themes")]
        groups.collect(self.path, {"industries": industries, "themes": themes})
        html = groups.dashboard_section(self.path)
        top_ten = html.split("업종 시가총액 TOP 10", 1)[1]
        self.assertIn("업종11", top_ten)
        self.assertNotIn("업종1</td>", top_ten)
        self.assertLess(top_ten.index("업종11"), top_ten.index("업종10"))
        self.assertIn("테마1", html)

    def test_invalid_response_does_not_replace_snapshot(self):
        original = {"industries": [item("1", "원본", 100, 1)], "themes": [item("T", "테마", 0, 1)]}
        groups.collect(self.path, original)
        with self.assertRaises(ValueError):
            groups.collect(self.path, {"industries": [item("2", "변경", 200, 2)], "themes": []})
        self.assertEqual("원본", groups.latest("industries", self.path)[0]["name"])

    def test_theme_members_paginates_and_uses_full_membership(self):
        with patch.object(context, "_json", side_effect=[
            {"items": [{"itemCode": "005930"}], "hasNext": True, "cursor": "NEXT=="},
            {"items": [{"itemCode": "000660"}], "hasNext": False},
        ]) as get:
            self.assertEqual({"005930", "000660"}, context.theme_members("210"))
        self.assertIn("cursor=NEXT%3D%3D", get.call_args.args[0])

    def test_context_rejects_future_ranking(self):
        self.assertFalse(context._before_or_equal("2026-10-20T10:00:00+09:00", "2026-10-20T09:25:00"))
        self.assertTrue(context._before_or_equal("2026-10-20T09:20:00+09:00", "2026-10-20T09:25:00"))

    def test_recommendation_context_is_captured_once_from_prior_snapshot(self):
        original = {"industries": [item("1", "업종", 100, 2)], "themes": [item("T", "테마", 0, 3)]}
        groups.collect(self.path, original)
        recommendation = [{"ticker": "005930", "name": "삼성전자", "evaluated_at": "2026-10-20T15:35:00"}]
        with patch.object(context, "query_rows", return_value=recommendation), \
             patch.object(context, "theme_members", return_value={"005930"}), \
             patch.object(context, "_json", return_value={"industryCode": "1"}):
            self.assertEqual(1, context.harvest(path=self.path, today=date(2026, 10, 20)))
            self.assertEqual(0, context.harvest(path=self.path, today=date(2026, 10, 20)))
        with closing(context.connect(self.path)) as db:
            row = db.execute("SELECT industry_rank,top_theme_rank,industry_breadth FROM recommendation_context").fetchone()
        self.assertEqual((1, 1, 2 / 3), tuple(row))


if __name__ == "__main__":
    unittest.main()
