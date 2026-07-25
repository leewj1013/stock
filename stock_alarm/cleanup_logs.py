from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime

from .app import write_error_log


LOG_FILES = [
    "backtest.csv",
    "backtest_summary.csv",
    "deliveries.csv",
    "errors.log",
    "recommendations.csv",
    "sent_keys.csv",
    "task.err.log",
    "task.out.log",
    "tuning.csv",
]


def archive_logs(apply: bool = False, logs_dir: str = "logs") -> list[str]:
    existing = [name for name in LOG_FILES if os.path.exists(os.path.join(logs_dir, name))]
    if not apply:
        return existing

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_dir = os.path.join(logs_dir, "archive", stamp)
    os.makedirs(archive_dir, exist_ok=True)
    for name in existing:
        source = os.path.join(logs_dir, name)
        shutil.copy2(source, os.path.join(archive_dir, name))
        open(source, "w", encoding="utf-8").close()
    return existing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="archive and truncate log files")
    args = parser.parse_args()
    try:
        files = archive_logs(args.apply)
        action = "archived" if args.apply else "would archive"
        print(f"{action}: {', '.join(files) if files else 'nothing'}")
    except Exception as error:
        write_error_log(error)
        raise


if __name__ == "__main__":
    main()
