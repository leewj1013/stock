import os
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.app import DEFAULT_ENV_PATH, load_env, save_env_value


class LoadEnvTest(unittest.TestCase):
    def test_default_env_path_is_anchored_to_repo_root_not_cwd(self):
        # "python -m stock_alarm.<module>" only guarantees the package is
        # importable, not that the process's cwd is the repo root. A relative
        # ".env" default silently finds nothing from another directory (see
        # test_load_env_ignores_cwd below), which is exactly how
        # TELEGRAM_BOT_TOKEN went missing with no error anywhere in the logs.
        self.assertTrue(os.path.isabs(DEFAULT_ENV_PATH))
        self.assertTrue(DEFAULT_ENV_PATH.endswith(".env"))

    def test_load_env_ignores_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = os.path.join(directory, ".env")
            with open(env_path, "w", encoding="utf-8") as file:
                file.write("STOCK_ALARM_TEST_TOKEN=from-file\n")
            cwd = os.getcwd()
            os.chdir(tempfile.gettempdir())
            try:
                os.environ.pop("STOCK_ALARM_TEST_TOKEN", None)
                with patch("stock_alarm.app.DEFAULT_ENV_PATH", env_path):
                    load_env()
                self.assertEqual("from-file", os.environ.get("STOCK_ALARM_TEST_TOKEN"))
            finally:
                os.chdir(cwd)
                os.environ.pop("STOCK_ALARM_TEST_TOKEN", None)

    def test_load_env_missing_file_does_not_raise(self):
        with tempfile.TemporaryDirectory() as directory:
            load_env(os.path.join(directory, "does-not-exist.env"))

    def test_save_env_value_uses_default_path_when_none_given(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = os.path.join(directory, ".env")
            with patch("stock_alarm.app.DEFAULT_ENV_PATH", env_path):
                save_env_value("SOME_KEY", "some-value")
            with open(env_path, encoding="utf-8") as file:
                self.assertIn("SOME_KEY=some-value", file.read())


if __name__ == "__main__":
    unittest.main()
