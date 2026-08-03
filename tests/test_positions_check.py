import os
import tempfile
import unittest

from stock_alarm.positions_check import active_position_count, position_count, validate_positions


class PositionsCheckTest(unittest.TestCase):
    def write_temp(self, text):
        file = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", newline="")
        file.write(text)
        file.close()
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))
        return file.name

    def test_accepts_valid_positions(self):
        path = self.write_temp("ticker,name,entry_price,entry_date\n005930,Samsung,80000,2026-07-25\n")
        self.assertEqual([], validate_positions(path))
        self.assertEqual(1, position_count(path))

    def test_rejects_bad_rows(self):
        path = self.write_temp("ticker,name,entry_price,entry_date\n5930,,0,2026-07-25\n005930,Samsung,x,2026-07-25\n005930,Dup,1,2026-07-25\n")
        errors = validate_positions(path)
        self.assertIn("line 2: invalid ticker '5930'", errors)
        self.assertIn("line 2: empty name", errors)
        self.assertIn("line 2: entry_price must be positive", errors)
        self.assertIn("line 3: invalid entry_price 'x'", errors)
        self.assertIn("line 4: duplicate ticker 005930", errors)

    def test_active_position_count_excludes_sold_tickers(self):
        positions = self.write_temp("ticker,name,entry_price,entry_date\n005930,Samsung,80000,2026-07-25\n000660,SK hynix,100000,2026-07-25\n")
        alerts = self.write_temp("created_at,ticker\n2026-07-26T09:00:00,005930\n")

        self.assertEqual(1, active_position_count(positions, alerts))


if __name__ == "__main__":
    unittest.main()
