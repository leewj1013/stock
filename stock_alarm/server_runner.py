from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .app import load_env
from .run_gate import should_run


STEPS: dict[str, list[tuple[str, bool, bool]]] = {
    "open": [("stock_alarm.market_summary", False, True)],
    "intraday": [
        ("stock_alarm", False, True),
        ("stock_alarm.virtual_trader_report", False, True),
        ("stock_alarm.dashboard", False, False),
    ],
    "sell": [
        ("stock_alarm.sell_check", False, True),
        ("stock_alarm.positions_report", False, True),
    ],
    "daily": [
        ("stock_alarm.positions_report", True, False),
        ("stock_alarm.recommendation_performance", True, False),
        ("stock_alarm.sell_performance", True, False),
        ("stock_alarm.strategy_learning", True, False),
        ("stock_alarm.daily_summary", False, False),
        ("stock_alarm.daily_check", False, False),
        ("stock_alarm.dashboard", False, False),
        ("stock_alarm.issue_alert", False, False),
    ],
}


def _write_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")


def _log_paths() -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[1]
    return root / "logs" / "task.out.log", root / "logs" / "task.err.log"


def _run_module(module: str, no_cache: bool, stdout: Path, stderr: Path, arguments: tuple[str, ...] = ()) -> int:
    environment = os.environ.copy()
    if no_cache:
        environment["NO_CACHE"] = "1"
    with stdout.open("a", encoding="utf-8") as output, stderr.open("a", encoding="utf-8") as error:
        return subprocess.run([sys.executable, "-m", module, *arguments], stdout=output, stderr=error, env=environment, check=False).returncode


def run(mode: str) -> int:
    if mode not in STEPS:
        raise ValueError(f"unsupported mode: {mode}")
    load_env()
    if not should_run(mode):
        return 0

    stdout, stderr = _log_paths()
    stderr.parent.mkdir(parents=True, exist_ok=True)
    stderr.write_text("", encoding="utf-8")
    _write_log(stdout, f"MODE {mode}")

    for module, optional, no_cache in STEPS[mode]:
        _write_log(stdout, f"START {module}")
        code = _run_module(module, no_cache, stdout, stderr)
        if code == 0:
            _write_log(stdout, f"DONE {module}")
            continue
        _run_module("stock_alarm.failure_alert", False, stdout, stderr, (module, str(code)))
        _write_log(stdout, f"{'WARN' if optional else 'FAIL'} {module} exit={code}")
        if not optional:
            return code
    return 0


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1] if len(sys.argv) > 1 else "daily"))
