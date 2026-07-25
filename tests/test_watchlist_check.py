import os
import tempfile
import unittest

from stock_alarm.watchlist_check import validate_watchlist


class WatchlistCheckTest(unittest.TestCase):
    def write_temp(self, text):
        file = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", newline="")
        file.write(text)
        file.close()
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))
        return file.name

    def test_accepts_valid_watchlist(self):
        path = self.write_temp("ticker,name\n005930,Samsung\n000660,SK Hynix\n")
        self.assertEqual([], validate_watchlist(path))

    def test_rejects_bad_rows(self):
        path = self.write_temp("ticker,name\n5930,\n005930,Samsung\n005930,Duplicate\n")
        errors = validate_watchlist(path)
        self.assertIn("line 2: invalid ticker '5930'", errors)
        self.assertIn("line 2: empty name", errors)
        self.assertIn("line 4: duplicate ticker 005930", errors)


if __name__ == "__main__":
    unittest.main()
