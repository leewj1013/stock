import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from stock_alarm.sector_reference import load_sector_mapping, save_sector_mapping


class SectorReferenceTest(unittest.TestCase):
    def test_cache_hit_with_all_tickers_present_skips_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sector_mapping.json"
            save_sector_mapping({"005930": "전기전자"}, {}, path)
            with patch("stock_alarm.sector_reference.fetch_sector_mapping") as fetch:
                mapping = load_sector_mapping({"005930"}, path)
        self.assertEqual({"005930": "전기전자"}, mapping)
        fetch.assert_not_called()

    def test_missing_ticker_triggers_fetch_and_merges_into_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sector_mapping.json"
            save_sector_mapping({"005930": "전기전자"}, {}, path)
            with patch("stock_alarm.sector_reference.fetch_sector_mapping", return_value=({"000660": "반도체"}, {})) as fetch:
                mapping = load_sector_mapping({"005930", "000660"}, path)
            self.assertEqual({"005930": "전기전자", "000660": "반도체"}, mapping)
            fetch.assert_called_once_with({"000660"})
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual({"005930": "전기전자", "000660": "반도체"}, saved["mapping"])

    def test_recent_failed_fetch_is_not_retried_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sector_mapping.json"
            save_sector_mapping({"005930": "전기전자"}, {}, path)
            with patch("stock_alarm.sector_reference.fetch_sector_mapping", return_value=({}, {})) as fetch:
                load_sector_mapping({"999999"}, path, retry_after_seconds=600)
                mapping = load_sector_mapping({"999999"}, path, retry_after_seconds=600)
        self.assertEqual(1, fetch.call_count)
        self.assertEqual({"005930": "전기전자"}, mapping)

    def test_stale_cache_beyond_retry_window_retries_missing_ticker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sector_mapping.json"
            old = (datetime.now().astimezone() - timedelta(seconds=1200)).isoformat(timespec="seconds")
            path.write_text(json.dumps({"fetched_at": old, "mapping": {"005930": "전기전자"}}), encoding="utf-8")
            with patch("stock_alarm.sector_reference.fetch_sector_mapping", return_value=({}, {})) as fetch:
                load_sector_mapping({"999999"}, path, retry_after_seconds=600)
        fetch.assert_called_once_with({"999999"})

    def test_save_leaves_no_partial_write_if_a_shorter_save_follows(self):
        # Regression: two profiles refreshing the same cache used to
        # write_text() straight into the target file, so an overlapping
        # second (shorter) write could leave the first write's tail appended
        # after the second's closing brace -- corrupt, unparseable JSON that
        # then made every holding show as 미분류 until fixed by hand.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sector_mapping.json"
            save_sector_mapping({"005930": "전기전자", "000660": "반도체", "035420": "IT서비스"}, {}, path)
            save_sector_mapping({"005930": "전기전자"}, {}, path)
            content = path.read_text(encoding="utf-8")
            saved = json.loads(content)  # raises if any trailing/leftover bytes remain
            self.assertEqual({"005930": "전기전자"}, saved["mapping"])
            self.assertEqual([], [entry for entry in Path(directory).iterdir() if entry != path])


if __name__ == "__main__":
    unittest.main()
