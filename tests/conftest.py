import os
import sqlite3
from pathlib import Path
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
    # The process-wide Toss client would otherwise carry one test's mock into the next.
    from stock_alarm.toss_client import reset_shared_client

    reset_shared_client()


REAL_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(autouse=True)
def no_writes_to_real_data(monkeypatch):
    # The real DBs hold live virtual accounts (and real deposits in the neutral
    # one). Any test that opens one for writing fails here instead of quietly
    # changing live data; read-only URIs (file:...?mode=ro) stay allowed.
    real_connect = sqlite3.connect

    def guarded(database, *args, **kwargs):
        text = os.fspath(database) if not isinstance(database, str) else database
        read_only = kwargs.get("uri") and "mode=ro" in text
        path = Path(text[5:].split("?", 1)[0] if text.startswith("file:") else text)
        if not read_only and text != ":memory:" and path.suffix == ".db":
            resolved = (Path.cwd() / path).resolve() if not path.is_absolute() else path.resolve()
            if REAL_DATA_DIR in resolved.parents:
                raise AssertionError(f"test tried to open real data for writing: {resolved}")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded)
    yield


@pytest.fixture(autouse=True)
def redirect_default_db(tmp_path, monkeypatch):
    # Most code reaches the DB through data_store.connect() with the real
    # default path (bound as a default argument, so patching DB_PATH is not
    # enough). Send those to a throwaway DB; the guard above still catches
    # anything that bypasses data_store.
    from stock_alarm import data_store

    real = data_store.connect

    def redirected(path=data_store.DB_PATH):
        resolved = Path(path).resolve()
        if REAL_DATA_DIR in resolved.parents:
            path = str(tmp_path / "redirected" / resolved.name)
        return real(path)

    monkeypatch.setattr(data_store, "connect", redirected)
    yield
