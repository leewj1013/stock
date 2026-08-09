from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run(mode: str) -> int:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_stock_alarm.ps1"
    if sys.platform == "win32":
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), mode]
    else:
        modules = {"open": ["stock_alarm.market_summary"], "intraday": ["stock_alarm", "stock_alarm.sell_check", "stock_alarm.positions_report"], "daily": ["stock_alarm.positions_report", "stock_alarm.recommendation_performance", "stock_alarm.daily_summary", "stock_alarm.daily_check", "stock_alarm.dashboard", "stock_alarm.issue_alert"]}
        return max((subprocess.run([sys.executable, "-m", module], check=False).returncode for module in modules[mode]), default=0)
    return subprocess.run(command, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1]))
