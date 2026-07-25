import unittest
import csv
import os
import tempfile
from datetime import date
from unittest.mock import patch

from stock_alarm.positions_report import PositionRow, change_summary, lines, position_rows, summary, write_log


class PositionsReportTest(unittest.TestCase):
    @patch("stock_alarm.positions_report.stock_name", return_value="Samsung")
    @patch("stock_alarm.positions_report.naver_rows", return_value=[[20260725, 0, 0, 0, 110, 1]])
    def test_position_rows(self, _rows, _name):
        rows = position_rows([{"ticker": "005930", "name": "Samsung", "entry_price": "100"}], date(2026, 7, 25))

        self.assertEqual(10, rows[0].return_pct)

    def test_summary(self):
        text = summary([PositionRow("A", "A", 100, 90, -10), PositionRow("B", "B", 100, 110, 10)])

        self.assertIn("positions=2", text)
        self.assertIn("worst=A(A) -10.00%", text)

    def test_lines(self):
        text = "\n".join(lines([PositionRow("A", "A", 100, 90, -10)]))

        self.assertIn("# positions report", text)
        self.assertIn("return=-10.00%", text)

    def test_write_log(self):
        with tempfile.NamedTemporaryFile(delete=False) as file:
            path = file.name
        os.unlink(path)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))

        write_log([PositionRow("A", "A", 100, 90, -10)], path)

        with open(path, newline="", encoding="utf-8-sig") as file:
            rows = list(csv.DictReader(file))
        self.assertEqual("-10.00", rows[0]["return_pct"])

    def test_change_summary(self):
        with tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8") as file:
            path = file.name
            writer = csv.writer(file)
            writer.writerow(["created_at", "ticker", "name", "entry_price", "close", "return_pct"])
            writer.writerow(["t1", "A", "A", "100", "100", "0.00"])
            writer.writerow(["t1", "B", "B", "100", "90", "-10.00"])
            writer.writerow(["t2", "A", "A", "100", "110", "10.00"])
            writer.writerow(["t2", "B", "B", "100", "95", "-5.00"])
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))

        self.assertIn("change=+7.50p", change_summary(path))


if __name__ == "__main__":
    unittest.main()
