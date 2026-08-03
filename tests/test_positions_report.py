import unittest
import csv
import os
import tempfile
from datetime import date, datetime
from unittest.mock import patch

from stock_alarm.positions_report import PositionRow, active_positions, change_summary, lines, position_rows, run, summary, write_log


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

    @patch("stock_alarm.positions_report.latest_sell_alert_times", return_value={"A": datetime(2026, 7, 30)})
    def test_active_positions_keeps_latest_entry_after_sell(self, _alerts):
        rows = active_positions(
            [
                {"ticker": "A", "entry_date": "2026-07-29"},
                {"ticker": "A", "entry_date": "2026-07-31"},
                {"ticker": "B", "entry_date": "2026-07-29"},
            ]
        )

        self.assertEqual(["A", "B"], [row["ticker"] for row in rows])
        self.assertEqual("2026-07-31", rows[0]["entry_date"])

    @patch("stock_alarm.positions_report.latest_naver_trading_day", return_value=date(2026, 7, 25))
    @patch("stock_alarm.positions_report.write_log")
    @patch("stock_alarm.positions_report.position_rows", return_value=[])
    @patch("stock_alarm.positions_report.read_positions", return_value=[{"ticker": "A"}, {"ticker": "B"}])
    @patch("stock_alarm.positions_report.active_positions", return_value=[{"ticker": "B"}])
    @patch("stock_alarm.positions_report.load_env")
    def test_run_reports_active_positions_only(self, _env, _active, positions, position_rows_mock, _write, _day):
        run()

        position_rows_mock.assert_called_once_with([{"ticker": "B"}], date(2026, 7, 25))


if __name__ == "__main__":
    unittest.main()
