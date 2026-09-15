import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    # health.lines() and ~30 entry points call load_env(), which copies the real
    # workspace .env (and the secure secrets.env, where present) into os.environ.
    # Without this, any test that forgets to patch it leaks live settings (sizing
    # mode, credentials) into every later test. Point the env files at nothing
    # and undo all environ changes after each test.
    from stock_alarm import app

    missing = str(tmp_path / "no-such.env")
    for name in ("DEFAULT_ENV_PATH", "DEFAULT_SECURE_ENV_PATH"):
        if hasattr(app, name):  # the secure store path only exists in newer app.py
            monkeypatch.setattr(app, name, missing)
    with patch.dict(os.environ):
        os.environ.pop("STOCK_ALARM_SECURE_ENV_PATH", None)
        yield
