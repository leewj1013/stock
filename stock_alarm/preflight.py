from __future__ import annotations

import unittest

from .secret_check import scan as scan_secrets
from .positions_check import validate_positions
from .watchlist_check import validate_watchlist
from .git_check import validate_ignores


def main() -> int:
    errors = validate_watchlist()
    position_errors = validate_positions()
    secrets = scan_secrets()
    git_errors = validate_ignores()
    tests = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=1).run(tests)

    if errors:
        print("watchlist ok=False")
        print("\n".join(errors))
    else:
        print("watchlist ok=True")

    if secrets:
        print("secret check ok=False")
        print("\n".join(secrets))
    else:
        print("secret check ok=True")

    if position_errors:
        print("positions ok=False")
        print("\n".join(position_errors))
    else:
        print("positions ok=True")

    if git_errors:
        print("git ignore ok=False")
        print("\n".join(git_errors))
    else:
        print("git ignore ok=True")

    return 0 if result.wasSuccessful() and not errors and not position_errors and not secrets and not git_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
