import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.daily_review import health_issues
from stock_alarm.data_store import finish_run, start_run


class HealthIssuesTest(unittest.TestCase):
    def test_only_todays_successful_telegram_brief_counts_as_delivered(self):
        from stock_alarm.notifier import write_delivery_log

        today = date.today().isoformat()
        with tempfile.TemporaryDirectory() as directory:
            log = os.path.join(directory, "deliveries.csv")
            with patch("stock_alarm.data_store.query_rows", return_value=[]), patch.dict("stock_alarm.trading_profiles.PROFILES", {}, clear=True):
                for channel, status, event in [("telegram", "delivered", "sell"), ("console", "fallback", "daily_summary"), ("skipped_duplicate", "", "daily_summary")]:
                    write_delivery_log(channel, path=log, status=status, event_type=event)
                missing = "오늘 마감 브리핑 텔레그램 발송 성공 기록 없음"
                self.assertIn(missing, health_issues(today, errors_log="missing", deliveries_log=log))
                write_delivery_log("telegram", path=log, status="delivered", event_type="daily_summary")
                self.assertNotIn(missing, health_issues(today, errors_log="missing", deliveries_log=log))
                self.assertIn(missing, health_issues("2099-01-01", errors_log="missing", deliveries_log=log))

    def test_flags_failed_runs_missing_valuations_and_todays_errors(self):
        today = date.today().isoformat()
        with tempfile.TemporaryDirectory() as directory:
            db = os.path.join(directory, "main.db")
            finish_run(start_run("recommendation", today, db), "failed", db)
            errors = os.path.join(directory, "errors.log")
            with open(errors, "w", encoding="utf-8") as file:
                file.write(f"[{today}T10:00:00] ValueError: boom\n[2020-01-01T00:00:00] old\n")
            with patch.dict("stock_alarm.trading_profiles.PROFILES", {"aggressive": {"db_path": db}}, clear=True):
                issues = health_issues(today, path=db, errors_log=errors, deliveries_log=os.path.join(directory, "missing.csv"))

        self.assertEqual([
            "실패한 실행 1건 (strategy_runs)",
            "오늘 완료된 추천·매도 실행이 없음 (strategy_runs)",
            "aggressive 계좌 오늘 평가 기록 없음",
            "오늘 오류 로그 1건 (logs/errors.log)",
            "오늘 마감 브리핑 텔레그램 발송 성공 기록 없음",
        ], issues)


if __name__ == "__main__":
    unittest.main()
