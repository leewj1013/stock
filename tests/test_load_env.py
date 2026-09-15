import os
import tempfile
import unittest
from unittest.mock import patch

from stock_alarm.app import DEFAULT_ENV_PATH, DEFAULT_SECURE_ENV_PATH, load_env, save_env_value, save_env_values, workspace_root


class LoadEnvTest(unittest.TestCase):
    def test_deployed_runtime_follows_workspace_path_back_to_the_workspace(self):
        with tempfile.TemporaryDirectory() as runtime, tempfile.TemporaryDirectory() as workspace:
            with open(os.path.join(runtime, "workspace.path"), "w", encoding="utf-8-sig") as file:
                file.write(workspace)
            self.assertEqual(workspace, workspace_root(runtime))

    def test_workspace_root_is_the_project_itself_without_a_marker(self):
        with tempfile.TemporaryDirectory() as project:
            self.assertEqual(project, workspace_root(project))

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

    def test_secure_file_overrides_workspace_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            public_path = os.path.join(directory, ".env")
            secure_path = os.path.join(directory, "secrets.env")
            with open(public_path, "w", encoding="utf-8") as file:
                file.write("TOSS_CLIENT_SECRET=stale-workspace-value\n")
            with open(secure_path, "w", encoding="utf-8") as file:
                file.write("TOSS_CLIENT_SECRET=protected-value\n")
            os.environ.pop("TOSS_CLIENT_SECRET", None)
            try:
                with patch("stock_alarm.app.DEFAULT_ENV_PATH", public_path), \
                     patch("stock_alarm.app.DEFAULT_SECURE_ENV_PATH", secure_path):
                    load_env()
                self.assertEqual("protected-value", os.environ["TOSS_CLIENT_SECRET"])
            finally:
                os.environ.pop("TOSS_CLIENT_SECRET", None)

    def test_default_workspace_env_cannot_supply_sensitive_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            public_path = os.path.join(directory, ".env")
            missing_secure_path = os.path.join(directory, "missing-secrets.env")
            with open(public_path, "w", encoding="utf-8") as file:
                file.write("DASHBOARD_LOCAL_USERNAME=attacker\nDASHBOARD_LOCAL_PASSWORD_HASH=attacker-hash\n")
            os.environ.pop("DASHBOARD_LOCAL_USERNAME", None)
            os.environ.pop("DASHBOARD_LOCAL_PASSWORD_HASH", None)
            try:
                with patch("stock_alarm.app.DEFAULT_ENV_PATH", public_path), patch("stock_alarm.app.DEFAULT_SECURE_ENV_PATH", missing_secure_path):
                    load_env()
                self.assertNotIn("DASHBOARD_LOCAL_USERNAME", os.environ)
                self.assertNotIn("DASHBOARD_LOCAL_PASSWORD_HASH", os.environ)
            finally:
                os.environ.pop("DASHBOARD_LOCAL_USERNAME", None)
                os.environ.pop("DASHBOARD_LOCAL_PASSWORD_HASH", None)

    def test_sensitive_value_is_saved_to_secure_path(self):
        with tempfile.TemporaryDirectory() as directory:
            public_path = os.path.join(directory, ".env")
            secure_path = os.path.join(directory, "secrets.env")
            open(secure_path, "w", encoding="utf-8").close()
            with patch("stock_alarm.app.DEFAULT_ENV_PATH", public_path), \
                 patch("stock_alarm.app.DEFAULT_SECURE_ENV_PATH", secure_path):
                save_env_value("TOSS_CLIENT_SECRET", "protected-value")
            self.assertFalse(os.path.exists(public_path))
            with open(secure_path, encoding="utf-8") as file:
                self.assertIn("TOSS_CLIENT_SECRET=protected-value", file.read())

    def test_sensitive_value_refuses_to_create_an_unprotected_store(self):
        with tempfile.TemporaryDirectory() as directory:
            secure_path = os.path.join(directory, "missing", "secrets.env")
            with patch("stock_alarm.app.DEFAULT_SECURE_ENV_PATH", secure_path):
                with self.assertRaises(RuntimeError):
                    save_env_value("TOSS_CLIENT_SECRET", "protected-value")

    def test_multiple_credentials_are_replaced_together_and_legacy_token_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            secure_path = os.path.join(directory, "secrets.env")
            with open(secure_path, "w", encoding="utf-8") as file:
                file.write("DASHBOARD_LOCAL_TOKEN=legacy\nDASHBOARD_LOCAL_USERNAME=old\nDASHBOARD_LOCAL_PASSWORD_HASH=old-hash\n")
            with patch("stock_alarm.app.DEFAULT_SECURE_ENV_PATH", secure_path):
                save_env_values(
                    {"DASHBOARD_LOCAL_USERNAME": "new", "DASHBOARD_LOCAL_PASSWORD_HASH": "new-hash"},
                    remove_keys={"DASHBOARD_LOCAL_TOKEN"},
                )
            with open(secure_path, encoding="utf-8") as file:
                content = file.read()
            self.assertNotIn("DASHBOARD_LOCAL_TOKEN", content)
            self.assertIn("DASHBOARD_LOCAL_USERNAME=new", content)
            self.assertIn("DASHBOARD_LOCAL_PASSWORD_HASH=new-hash", content)


if __name__ == "__main__":
    unittest.main()
