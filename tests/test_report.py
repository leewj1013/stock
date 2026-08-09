import csv
import os
import tempfile
import unittest
import subprocess

from unittest.mock import patch

from stock_alarm.report import delivery_status, format_recommendation, format_sell_alert, latest_error, latest_error_summary, lines, tail_csv, tail_text


class ReportTest(unittest.TestCase):
    def test_tail_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "x.csv")
            with open(path, "w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(["id"])
                writer.writerow(["1"])
                writer.writerow(["2"])

            self.assertEqual([{"id": "2"}], tail_csv(path, 1))

    def test_latest_error_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual("none", latest_error(os.path.join(directory, "missing.log")))

    def test_latest_error_summary_when_delivery_is_newer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "errors.log")
            with open(path, "w", encoding="utf-8") as file:
                file.write("old error\n")
            os.utime(path, (1, 1))

            self.assertEqual(
                "none since last delivery",
                latest_error_summary([{"created_at": "2026-07-25T01:15:32"}], path),
            )

    def test_tail_csv_missing(self):
        self.assertEqual([], tail_csv("does-not-exist.csv"))

    def test_tail_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "x.log")
            with open(path, "w", encoding="utf-8") as file:
                file.write("a\n\nb\nc\n")

            self.assertEqual(["b", "c"], tail_text(path, 2))

    def test_delivery_status_prefers_latest_telegram(self):
        self.assertEqual(
            "telegram ok at 2026-07-25T01:15:32",
            delivery_status(
                [
                    {"created_at": "2026-07-25T01:14:15", "channel": "console"},
                    {"created_at": "2026-07-25T01:15:32", "channel": "telegram"},
                ]
            ),
        )

    def test_delivery_status_warns_without_telegram(self):
        self.assertEqual(
            "no recent telegram delivery; last=console at 2026-07-25T01:18:35",
            delivery_status([{"created_at": "2026-07-25T01:18:35", "channel": "console"}]),
        )

    def test_format_recommendation_marks_legacy_score(self):
        text = format_recommendation({"created_at": "now", "name": "X", "ticker": "000001", "score": "644.41"})
        self.assertIn("score=644.41 legacy", text)

    def test_format_sell_alert(self):
        text = format_sell_alert({"created_at": "now", "name": "X", "ticker": "000001", "return_pct": "-6.00", "reason": "stop"})
        self.assertIn("return=-6.00%", text)

    @patch("stock_alarm.report.subprocess.run")
    def test_task_status_explains_permission_denied(self, run):
        from stock_alarm.report import task_status

        run.return_value = subprocess.CompletedProcess(["pwsh"], 1, b"", "Access is denied".encode())

        self.assertIn("permission denied", task_status())

    @patch("stock_alarm.report.subprocess.run")
    def test_task_status_checks_all_tasks(self, run):
        from stock_alarm.report import task_status

        run.return_value = subprocess.CompletedProcess(["pwsh"], 0, b"stockAlarmDaily: LastTaskResult=0", b"")

        self.assertIn("stockAlarmDaily", task_status())
        self.assertIn("stockAlarmOpen", run.call_args.args[0][-1])
        self.assertIn("stockAlarmIntradayEvery5Minutes", run.call_args.args[0][-1])
        self.assertIn("stockAlarmMaintenance", run.call_args.args[0][-1])
        self.assertNotIn("stockAlarmIntraday1030", run.call_args.args[0][-1])
        self.assertIn("yyyy-MM-dd HH:mm:ss", run.call_args.args[0][-1])

    @patch("stock_alarm.report.subprocess.run")
    def test_task_status_explains_generic_failure(self, run):
        from stock_alarm.report import task_status

        run.return_value = subprocess.CompletedProcess(["pwsh"], 1, b"", b"bad")

        self.assertIn("status_daily_task.ps1", task_status())

    @patch("stock_alarm.report.task_status", return_value="ok")
    @patch("stock_alarm.report.tuning_lines", return_value=["# tuning recommendation", "confidence=weak"])
    def test_lines_includes_tuning(self, _tuning, _task):
        report = "\n".join(lines())
        self.assertIn("## tuning", report)
        self.assertIn("- confidence=weak", report)
        self.assertIn("## recent sell alerts", report)
        self.assertIn("## recent task log", report)
        self.assertIn("## recent task errors", report)


if __name__ == "__main__":
    unittest.main()
