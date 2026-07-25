from __future__ import annotations

import csv
import os
import subprocess
from datetime import datetime

from .app import latest_naver_trading_day, load_env, write_error_log
from .positions_check import position_count
from .positions_report import change_summary as positions_change_summary, position_rows, summary as positions_summary
from .sell_check import read_positions
from .tune_report import lines as tuning_lines


def tail_csv(path: str, count: int = 5) -> list[dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    return rows[-count:]


def latest_error(path: str = "logs/errors.log") -> str:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return "none"
    with open(path, encoding="utf-8", errors="replace") as file:
        lines = [line.strip() for line in file if line.strip()]
    return lines[-1] if lines else "none"


def latest_error_summary(deliveries: list[dict[str, str]], path: str = "logs/errors.log") -> str:
    error = latest_error(path)
    if error == "none" or not deliveries or not os.path.exists(path):
        return error
    try:
        last_delivery = datetime.fromisoformat(deliveries[-1].get("created_at", ""))
        last_error = datetime.fromtimestamp(os.path.getmtime(path))
    except ValueError:
        return error
    return "none since last delivery" if last_delivery > last_error else error


def delivery_status(deliveries: list[dict[str, str]]) -> str:
    telegram = [row for row in deliveries if row.get("channel") == "telegram"]
    if telegram:
        return f"telegram ok at {telegram[-1].get('created_at')}"
    if deliveries:
        return f"no recent telegram delivery; last={deliveries[-1].get('channel')} at {deliveries[-1].get('created_at')}"
    return "no deliveries yet"


def task_status() -> str:
    command = [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoProfile",
        "-Command",
        "$i=Get-ScheduledTaskInfo -TaskName stockAlarmDaily; "
        "Write-Output ('LastTaskResult={0}; NextRunTime={1}' -f $i.LastTaskResult,$i.NextRunTime)",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=10)
        output = (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()
        if result.returncode:
            if "Access is denied" in output:
                return "unavailable (permission denied; run scripts/status_daily_task.ps1 with ExecutionPolicy Bypass)"
            return "unavailable (run scripts/status_daily_task.ps1 with ExecutionPolicy Bypass)"
        if output and "LastTaskResult=;" not in output:
            return output
        return "unavailable (run scripts/status_daily_task.ps1 with ExecutionPolicy Bypass)"
    except Exception:
        return "unavailable"


def format_recommendation(row: dict[str, str]) -> str:
    score = row.get("score") or ""
    try:
        score_text = f"{float(score):.2f}"
        if float(score) > 100:
            score_text += " legacy"
    except ValueError:
        score_text = score or "?"
    return f"- {row.get('created_at')} {row.get('name')}({row.get('ticker')}) score={score_text}"


def format_sell_alert(row: dict[str, str]) -> str:
    return (
        f"- {row.get('created_at')} {row.get('name')}({row.get('ticker')}) "
        f"return={row.get('return_pct')}% reason={row.get('reason')}"
    )


def positions_report_summary() -> str:
    try:
        return positions_summary(position_rows(read_positions(), latest_naver_trading_day()))
    except Exception:
        return "unavailable"


def lines() -> list[str]:
    load_env()
    deliveries = tail_csv("logs/deliveries.csv", 3)
    delivery_history = tail_csv("logs/deliveries.csv", 50)
    recommendations = tail_csv("logs/recommendations.csv", 5)
    sell_alerts = tail_csv("logs/sell_alerts.csv", 5)
    backtest_summary = tail_csv("logs/backtest_summary.csv", 20)
    performance_summary = tail_csv("logs/recommendation_performance_summary.csv", 20)
    output = [
        "# stockAlarm report",
        f"notifier={os.environ.get('NOTIFIER', 'telegram')}",
        f"task={task_status()}",
        f"delivery_status={delivery_status(delivery_history)}",
        f"positions={position_count()}",
        f"positions_summary={positions_report_summary()}",
        f"positions_change={positions_change_summary()}",
        f"latest_error={latest_error_summary(delivery_history)}",
        "",
        "## recent deliveries",
    ]
    output.extend(f"- {row.get('created_at')} {row.get('channel')}" for row in deliveries)
    output.append("")
    output.append("## recent recommendations")
    output.extend(format_recommendation(row) for row in recommendations)
    output.append("")
    output.append("## recent sell alerts")
    output.extend(format_sell_alert(row) for row in sell_alerts)
    if not sell_alerts:
        output.append("- none")
    output.append("")
    output.append("## backtest summary")
    output.extend(f"- {row.get('metric')}: {row.get('value')}" for row in backtest_summary)
    output.append("")
    output.append("## recommendation performance")
    output.extend(f"- {row.get('metric')}: {row.get('value')}" for row in performance_summary)
    output.append("")
    output.append("## tuning")
    output.extend(f"- {line}" for line in tuning_lines()[1:])
    return output


def main() -> None:
    try:
        print("\n".join(lines()))
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
