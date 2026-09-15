import os
import unittest

from stock_alarm.app import load_env


class EnvIsolationTest(unittest.TestCase):
    def test_load_env_inside_tests_never_reads_the_real_env_files(self):
        before = dict(os.environ)
        load_env()
        added = set(os.environ) - set(before) - {"MPLCONFIGDIR"}
        self.assertEqual(set(), added)

    def test_environ_changes_do_not_survive_into_the_next_test_part_1(self):
        os.environ["STOCK_ALARM_ISOLATION_PROBE"] = "1"

    def test_environ_changes_do_not_survive_into_the_next_test_part_2(self):
        # assertFalse, not assertNotIn: a failure must never print os.environ (credentials).
        self.assertFalse("STOCK_ALARM_ISOLATION_PROBE" in os.environ, "environ change leaked from the previous test")


if __name__ == "__main__":
    unittest.main()
