import csv
import os
import tempfile
import unittest

from stock_alarm.app import Pick, track_positions


class TrackPositionsTest(unittest.TestCase):
    def test_adds_new_pick(self):
        with tempfile.NamedTemporaryFile(delete=False) as file:
            path = file.name
        os.unlink(path)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))

        added = track_positions([Pick("005930", "Samsung", 80000, 2, 100, 90)], path)

        with open(path, newline="", encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(1, added)
        self.assertEqual("005930", rows[0]["ticker"])

    def test_keeps_existing_entry_price(self):
        with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8") as file:
            file.write("ticker,name,entry_price,entry_date\n005930,Samsung,70000,2026-07-01\n")
            path = file.name
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))

        added = track_positions([Pick("005930", "Samsung", 80000, 2, 100, 90)], path)

        with open(path, newline="", encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(0, added)
        self.assertEqual("70000", rows[0]["entry_price"])

    def test_can_disable_auto_tracking(self):
        old = os.environ.get("AUTO_TRACK_PICKS")
        os.environ["AUTO_TRACK_PICKS"] = "0"
        try:
            self.assertEqual(0, track_positions([Pick("005930", "Samsung", 80000, 2, 100, 90)]))
        finally:
            if old is None:
                os.environ.pop("AUTO_TRACK_PICKS", None)
            else:
                os.environ["AUTO_TRACK_PICKS"] = old


if __name__ == "__main__":
    unittest.main()
