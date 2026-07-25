import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.app_setenv import main


class AppSetEnvTest(unittest.TestCase):
    def test_sets_value_from_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            cwd = os.getcwd()
            os.chdir(directory)
            try:
                with patch.object(sys, "argv", ["app_setenv", "DART_API_KEY"]), patch.dict(
                    os.environ, {"STOCK_ALARM_SETENV_VALUE": "secret"}, clear=False
                ):
                    main()

                with open(".env", encoding="utf-8") as file:
                    self.assertIn("DART_API_KEY=secret", file.read())
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
