import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.app_setenv import main


class AppSetEnvTest(unittest.TestCase):
    def test_sets_value_from_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            # save_env_value()'s default path is anchored to the repo root (not
            # cwd) so it keeps working when invoked from elsewhere -- point it
            # at a throwaway file here instead of chdir-ing, so this test can't
            # ever write into the real project .env.
            env_path = os.path.join(directory, ".env")
            with patch.object(sys, "argv", ["app_setenv", "DART_API_KEY"]), \
                 patch("stock_alarm.app.DEFAULT_ENV_PATH", env_path), \
                 patch.dict(os.environ, {"STOCK_ALARM_SETENV_VALUE": "secret"}, clear=False):
                main()

            with open(env_path, encoding="utf-8") as file:
                self.assertIn("DART_API_KEY=secret", file.read())


if __name__ == "__main__":
    unittest.main()
