import os
import unittest

from stock_alarm.health import enabled, yes


class HealthTest(unittest.TestCase):
    def test_yes(self):
        self.assertEqual("ok", yes(True))
        self.assertEqual("missing", yes(False))

    def test_enabled(self):
        old = os.environ.get("X_ENABLED")
        try:
            os.environ["X_ENABLED"] = "1"
            self.assertEqual("on", enabled("X_ENABLED"))
            os.environ["X_ENABLED"] = "0"
            self.assertEqual("off", enabled("X_ENABLED"))
        finally:
            if old is None:
                os.environ.pop("X_ENABLED", None)
            else:
                os.environ["X_ENABLED"] = old


if __name__ == "__main__":
    unittest.main()
