import csv
import os
import tempfile
import unittest
from datetime import date

from stock_alarm.daily_check import lines, status


class DailyCheckTest(unittest.TestCase):
    def write_log(self, rows):
        file = tempfile.NamedTemporaryFile("w", delete=False, newline="", encoding="utf-8")
        writer = csv.writer(file)
        writer.writerow(["created_at", "channel"])
        writer.writerows(rows)
        file.close()
        self.addCleanup(lambda: os.path.exists(file.name) and os.unlink(file.name))
        return file.name

    def test_ok_when_telegram_was_sent_today(self):
        path = self.write_log([["2026-07-25T16:10:01", "telegram"]])
        self.assertEqual("daily ok: telegram at 2026-07-25T16:10:01", status(date(2026, 7, 25), path))

    def test_ok_when_duplicate_was_skipped_today(self):
        path = self.write_log([["2026-07-25T16:10:01", "skipped_duplicate"]])
        self.assertEqual(
            "daily ok: skipped_duplicate at 2026-07-25T16:10:01",
            status(date(2026, 7, 25), path),
        )

    def test_not_ok_without_telegram_today(self):
        path = self.write_log([["2026-07-25T16:10:01", "console"]])
        self.assertEqual("daily not-ok: last=console at 2026-07-25T16:10:01", status(date(2026, 7, 25), path))

    def test_lines_include_latest_error(self):
        path = self.write_log([["2026-07-25T16:10:01", "telegram"]])

        text = "\n".join(lines(date(2026, 7, 25), path))

        self.assertIn("daily ok: telegram", text)
        self.assertIn("latest_error=", text)


if __name__ == "__main__":
    unittest.main()
