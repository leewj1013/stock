from __future__ import annotations

import json

from .app import load_env
from .toss_client import TossClient


def run() -> dict:
    load_env()
    return TossClient().connection_check()


def main() -> None:
    print(json.dumps(run(), ensure_ascii=False))


if __name__ == "__main__":
    main()

