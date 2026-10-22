import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.research_reliability import block_bootstrap, lifetime_audit, overlap_summary, price_audit, sample_audit, session, window_rows


class ResearchReliabilityTest(unittest.TestCase):
    def test_bootstrap_resamples_totals_and_counts_together(self):
        daily = [(2.0, 2), (10.0, 10), (0.0, 0)] * 10
        result = block_bootstrap(daily, 10, 100)
        self.assertEqual([1.0, 1.0], result["ci95"])
        self.assertEqual(0.0, result["standard_error"])

    def test_short_history_does_not_produce_an_interval(self):
        self.assertIsNone(block_bootstrap([(1, 1)], 10)["ci95"])

    def test_overlapping_windows_include_shared_endpoints(self):
        windows = [{"ticker": "A", "start": "01", "end": "10"},
                   {"ticker": "A", "start": "10", "end": "20"},
                   {"ticker": "B", "start": "02", "end": "11"},
                   {"ticker": "A", "start": "21", "end": "30"}]
        result = overlap_summary(windows)
        self.assertEqual(1, result["same_ticker_overlapping_rows"])
        self.assertEqual(3, result["same_ticker_nonoverlap_rows"])
        self.assertEqual(2, result["nonoverlapping_date_windows"])

    def test_missing_calendar_endpoint_is_excluded(self):
        rows = [{"ticker": "A", "entry_date": "2026-01-01"}]
        self.assertEqual([], window_rows(rows, ["2026-01-01"], 20))

    def test_duplicate_rows_do_not_increase_day_diversity(self):
        row = {"pick_date": "2026-01-01", "entry_date": "2026-01-02", "ticker": "A", "return_1d_pct": 1}
        result, daily = sample_audit([row] * 100, ["2026-01-02"])
        self.assertEqual(100, result["rows"])
        self.assertEqual(1, result["unique_keys"])
        self.assertEqual(1, result["balanced_day_equivalent"])
        self.assertEqual(100, daily[0]["count"])

    def test_source_pairs_keep_missing_references_and_separate_sessions(self):
        with sqlite3.connect(":memory:") as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("""CREATE TABLE price_quality(quality_id INTEGER, created_at TEXT,
                ticker TEXT, price_date TEXT, source TEXT, reference_source TEXT, close INTEGER,
                reference_close INTEGER, status TEXT, reason TEXT)""")
            for index, stamp, reference in [(1, "15:40:00", 99), (2, "15:50:00", None), (3, "16:01:00", 100)]:
                connection.execute("INSERT INTO price_quality VALUES (?,?,?,?,?,?,?,?,?,?)",
                                   (index, "2026-10-22T" + stamp, "A", "2026-10-22", "naver", "toss", 100, reference, "valid", ""))
            result, pairs = price_audit(connection, "2026-10-22")
        self.assertEqual(3, result["raw"]["rows"])
        self.assertEqual(2, result["deduplicated_observations"])
        self.assertEqual(1, sum(group["paired"] for group in result["groups"]))
        self.assertEqual(0, result["strict_comparable_pairs"])
        self.assertFalse(any(row["actual_reference_verified"] for row in pairs))

    def test_sessions_do_not_mix_live_and_after_hours_quotes(self):
        self.assertEqual("intraday", session("2026-10-22T15:29:59"))
        self.assertEqual("early_close", session("2026-10-22T15:30:00"))
        self.assertEqual("after_1600", session("2026-10-22T16:00:00"))

    def test_new_final_close_metadata_allows_same_day_pair(self):
        with sqlite3.connect(":memory:") as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("""CREATE TABLE price_quality(quality_id INTEGER, created_at TEXT,
                ticker TEXT, price_date TEXT, source TEXT, reference_source TEXT, close INTEGER,
                reference_close INTEGER, status TEXT, reason TEXT, source_fetched_at TEXT,
                reference_fetched_at TEXT, reference_price_date TEXT)""")
            connection.execute("INSERT INTO price_quality VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (1, "2026-10-22T16:10:00", "A", "2026-10-22", "naver", "pykrx", 100,
                                101, "valid", "", "2026-10-22T16:09:00", "2026-10-22T16:09:30", "2026-10-22"))
            result, pairs = price_audit(connection, "2026-10-22")
        self.assertEqual(1, result["same_day_close_candidates"])
        self.assertEqual(0, result["strict_comparable_pairs"])
        self.assertTrue(pairs[0]["actual_reference_verified"])

    def test_lifetime_marks_readiness_only_after_forward_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "data/backtest_expanded/pit_universe/ohlcv"
            folder.mkdir(parents=True)
            (folder / "A.csv").write_text("date,close\n20260105,100\n", encoding="utf-8")
            (root / "report.md").write_text("definition", encoding="utf-8")
            (root / "study.py").write_text("source", encoding="utf-8")
            studies = (("study", "2026-01-01", 2, "daily", "report.md", "study.py"),)
            with patch("scripts.research_reliability.STUDIES", studies):
                result, _ = lifetime_audit(root, "2026-01-05", ["2026-01-02", "2026-01-05"])
            self.assertEqual("ready_for_frozen_retest", result["studies"][0]["status"])

    def test_lifetime_freeze_does_not_claim_a_historical_code_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report, source = root / "report.md", root / "study.py"
            report.write_text("definition", encoding="utf-8")
            source.write_text("old", encoding="utf-8")
            studies = (("study", "2026-09-01", 12, "monthly", "report.md", "study.py"),)
            with patch("scripts.research_reliability.STUDIES", studies):
                first, _ = lifetime_audit(root, "2026-10-22")
                source.write_text("changed", encoding="utf-8")
                second, _ = lifetime_audit(root, "2026-10-23")
            self.assertEqual("2026-10-22", first["studies"][0]["hash_frozen_on"])
            self.assertEqual("source_changed_requires_review", second["studies"][0]["status"])
            self.assertEqual("2026-10-22", second["studies"][0]["hash_frozen_on"])


if __name__ == "__main__":
    unittest.main()
