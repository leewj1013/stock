"""Run a stockAlarm module from a trusted runtime under Python isolated mode."""

from __future__ import annotations

import os
import runpy
import sys


def use_utf8_streams() -> None:
    """Make stdout/stderr UTF-8 regardless of the Windows code page.

    -I (isolated mode) ignores PYTHONIOENCODING, so the scheduled tasks
    printed CP949 into logs that PowerShell decodes as UTF-8, and every
    Korean name in task.out.log came out garbled. pythonw (the dashboard
    server) has no streams at all, hence the None check.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    use_utf8_streams()
    if len(sys.argv) < 2 or not sys.argv[1].startswith("stock_alarm"):
        raise SystemExit("a stock_alarm module is required")
    runtime_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, runtime_root)
    module = sys.argv[1]
    sys.argv = sys.argv[1:]
    runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
