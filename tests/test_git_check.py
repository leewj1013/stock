import subprocess
import unittest
from unittest.mock import patch

from stock_alarm.git_check import validate_ignores


class GitCheckTest(unittest.TestCase):
    @patch("stock_alarm.git_check.subprocess.run")
    def test_validate_ignores_reports_missing_ignore(self, run):
        run.return_value = subprocess.CompletedProcess(["git"], 1)

        self.assertIn("not ignored:", validate_ignores()[0])


if __name__ == "__main__":
    unittest.main()
