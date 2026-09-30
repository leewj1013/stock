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
    # sell_check.run() mirrors virtual sells onto shadow_orders in the default
    # (real) DB; tests that drive run() must never write there. Unit tests of
    # the mirror import the real function directly and pass a temp path.
    with patch.dict(os.environ), patch("stock_alarm.shadow_trader.sync_shadow_sells", return_value=0):
        os.environ.pop("STOCK_ALARM_SECURE_ENV_PATH", None)
        yield
