from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from .secret_check import scan as scan_secrets
from .positions_check import validate_positions
from .watchlist_check import validate_watchlist
from .git_check import validate_ignores


class EnvironmentIsolatedResult(unittest.TextTestResult):
    """Restore process environment after every test, including failing tests."""

    def startTest(self, test) -> None:  # noqa: N802
        self._environment_before_test = dict(os.environ)
        super().startTest(test)

    def stopTest(self, test) -> None:  # noqa: N802
        try:
            os.environ.clear()
            os.environ.update(self._environment_before_test)
        finally:
            super().stopTest(test)


def main() -> int:
    errors = validate_watchlist()
    position_errors = validate_positions()
    secrets = scan_secrets()
    git_errors = validate_ignores()
    tests = unittest.defaultTestLoader.discover("tests")
    # unittest does not load tests/conftest.py. Keep its direct-discovery path
    # just as isolated as pytest so a test cannot load the real credentials or
    # leak a workspace setting into the next test.
    from . import app
    with tempfile.TemporaryDirectory() as temporary_directory:
        missing_env = os.path.join(temporary_directory, "missing.env")
        with patch.object(app, "DEFAULT_ENV_PATH", missing_env), patch.object(app, "DEFAULT_SECURE_ENV_PATH", missing_env):
            result = unittest.TextTestRunner(verbosity=1, resultclass=EnvironmentIsolatedResult).run(tests)

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
