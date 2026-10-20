import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from stock_alarm import earnings_research as research


class EarningsResearchTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "earnings.db")

    def test_event_kind_distinguishes_provisional_periodic_and_correction(self):
        self.assertEqual(("provisional", None), research.event_kind("영업(잠정)실적(공정공시)"))
        self.assertEqual(("periodic", "11013"), research.event_kind("분기보고서 (2026.09)"))
        self.assertEqual(("periodic_correction", "11011"), research.event_kind("[기재정정]사업보고서 (2025.12)"))

    def test_classification_requires_numbers_and_uses_operating_quality(self):
        self.assertEqual("unclassified", research.classify({"revenue_growth_pct": None}))
        self.assertEqual("strong_positive", research.classify({"revenue_growth_pct": 12, "operating_income_growth_pct": 25, "operating_income": 10, "operating_margin_change_pctp": 1}))
        self.assertEqual("strong_negative", research.classify({"revenue_growth_pct": 1, "operating_income_growth_pct": -35, "operating_income": 10, "operating_margin_change_pctp": -1}))

    def test_locked_limit_is_not_counted_as_immediate_fill(self):
        bars = [["20261019", 100, 101, 99, 100, 1], ["20261020", 130, 130, 130, 130, 1],
                ["20261021", 128, 135, 127, 134, 1], ["20261022", 135, 140, 133, 138, 1]]
        result = research.simulate("strong_positive", bars)
        self.assertTrue(result["first_session"]["locked_limit_up"])
        self.assertIsNone(result["horizons"]["1"]["return_pct"]["immediate"])

    def test_confirmation_buys_only_next_session_without_lookahead(self):
        bars = [["20261019", 100, 101, 99, 100, 1], ["20261020", 110, 113, 108, 112, 1],
                ["20261021", 115, 120, 114, 118, 1], ["20261022", 120, 122, 119, 121, 1]]
        result = research.simulate("strong_positive", bars)
        self.assertIsNone(result["horizons"]["1"]["return_pct"]["confirmed"])
        self.assertIsNotNone(result["horizons"]["3"]["return_pct"]["confirmed"])

    def test_harvest_does_not_guess_provisional_numbers(self):
        filing = {"rcept_no": "1", "rcept_dt": "20261020", "report_nm": "영업(잠정)실적(공정공시)"}
        with patch.object(research, "universe", return_value=[{"ticker": "005930", "name": "삼성전자"}]):
            added, failures = research.harvest(path=self.path, disclosure_fetch=lambda *_: [filing],
                summary_fetch=lambda *_: self.fail("must not fetch periodic accounts"), now=datetime(2026, 10, 20, 18))
        self.assertEqual((1, 0), (added, failures))
        with closing(research.connect(self.path)) as db:
            row = db.execute("SELECT severity,state,time_precision FROM events").fetchone()
        self.assertEqual(("unclassified", "DATA_REQUIRED", "date_only_next_session_conservative"), tuple(row))

    def test_empty_dashboard(self):
        self.assertIn("관찰 DB 생성 전", research.dashboard_section(self.path))

    def test_simulation_uses_one_pre_event_close_then_post_event_sessions(self):
        bars = [["20261019", 100, 101, 99, 100, 1], ["20261020", 110, 113, 108, 112, 1],
                ["20261021", 115, 120, 114, 118, 1]]
        result = research.simulate("strong_positive", bars)
        self.assertEqual("20261020", result["first_session"]["day"])


if __name__ == "__main__":
    unittest.main()
