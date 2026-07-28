import unittest
from unittest.mock import patch

from stock_alarm.issue_alert import message, run


class IssueAlertTest(unittest.TestCase):
    def test_message_is_empty_without_issues(self):
        self.assertEqual("", message([{"source": "dashboard", "item": "issues", "status": "none"}]))

    def test_message_formats_issues(self):
        text = message([{"source": "run", "item": "positions_report", "status": "missing"}])

        self.assertIn("[stockAlarm 운영 이슈]", text)
        self.assertIn("positions_report: missing", text)

    @patch("stock_alarm.issue_alert.load_env")
    @patch("stock_alarm.issue_alert.is_trading_day", return_value=True)
    @patch("stock_alarm.issue_alert.issue_rows", return_value=[{"source": "dashboard", "item": "issues", "status": "none"}])
    def test_run_skips_when_no_issues(self, _issues, _trading, _env):
        self.assertEqual("no_issues", run())

    @patch("stock_alarm.issue_alert.load_env")
    @patch("stock_alarm.issue_alert.is_trading_day", return_value=True)
    @patch("stock_alarm.issue_alert.send_notification", return_value="telegram")
    @patch("stock_alarm.issue_alert.issue_rows", return_value=[{"source": "run", "item": "positions_report", "status": "missing"}])
    def test_run_sends_when_issues_exist(self, _issues, send, _trading, _env):
        self.assertEqual("telegram", run())
        send.assert_called_once()

    @patch("stock_alarm.issue_alert.write_error_log")
    @patch("stock_alarm.issue_alert.load_env")
    @patch("stock_alarm.issue_alert.is_trading_day", return_value=True)
    @patch("stock_alarm.issue_alert.send_notification", side_effect=RuntimeError("network"))
    @patch("stock_alarm.issue_alert.issue_rows", return_value=[{"source": "run", "item": "positions_report", "status": "missing"}])
    def test_run_does_not_fail_daily_task_when_send_fails(self, _issues, _send, _trading, _env, log):
        self.assertEqual("issue_alert_failed", run())
        log.assert_called_once()

    @patch("stock_alarm.issue_alert.load_env")
    @patch("stock_alarm.issue_alert.is_trading_day", return_value=False)
    @patch("stock_alarm.issue_alert.issue_rows")
    @patch("stock_alarm.issue_alert.send_notification")
    def test_run_skips_when_market_closed(self, send, issues, _trading, _env):
        self.assertEqual("market_closed", run())
        issues.assert_not_called()
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
