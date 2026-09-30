import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from stock_alarm.daily_review import health_issues
from stock_alarm.data_store import finish_run, start_run


class HealthIssuesTest(unittest.TestCase):
    def test_flags_failed_runs_missing_valuations_and_todays_errors(self):
        today = date.today().isoformat()
        with tempfile.TemporaryDirectory() as directory:
            db = os.path.join(directory, "main.db")
            finish_run(start_run("recommendation", today, db), "failed", db)
            errors = os.path.join(directory, "errors.log")
            with open(errors, "w", encoding="utf-8") as file:
                file.write(f"[{today}T10:00:00] ValueError: boom\n[2020-01-01T00:00:00] old\n")
            with patch.dict("stock_alarm.trading_profiles.PROFILES", {"aggressive": {"db_path": db}}, clear=True):
                issues = health_issues(today, path=db, errors_log=errors)

        self.assertEqual([
            "실패한 실행 1건 (strategy_runs)",
            "오늘 완료된 추천·매도 실행이 없음 (strategy_runs)",
            "aggressive 계좌 오늘 평가 기록 없음",
            "오늘 오류 로그 1건 (logs/errors.log)",
        ], issues)


if __name__ == "__main__":
    unittest.main()
