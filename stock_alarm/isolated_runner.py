"""Run a stockAlarm module from a trusted runtime under Python isolated mode."""

from __future__ import annotations

import os
import runpy
import sys


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].startswith("stock_alarm"):
        raise SystemExit("a stock_alarm module is required")
    runtime_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, runtime_root)
    module = sys.argv[1]
    sys.argv = sys.argv[1:]
    runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
