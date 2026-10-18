from types import SimpleNamespace

import pytest

from stock_alarm import preflight


@pytest.mark.parametrize("exit_code", [0, 1, 2, 5])
def test_preflight_runs_pytest_and_propagates_failure(monkeypatch, exit_code):
    for name in ("validate_watchlist", "validate_positions", "scan_secrets", "validate_ignores"):
        monkeypatch.setattr(preflight, name, lambda: [])
    commands = []
    def run(command):
        commands.append(command)
        return SimpleNamespace(returncode=exit_code)
    monkeypatch.setattr(preflight.subprocess, "run", run)
    assert preflight.main() == (0 if exit_code == 0 else 1)
    assert commands == [[preflight.sys.executable, "-m", "pytest", "-q", "tests"]]
