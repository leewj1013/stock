import os
import tempfile
import unittest

from stock_alarm.cleanup_logs import archive_logs


class CleanupLogsTest(unittest.TestCase):
    def test_archive_logs_copies_and_truncates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "deliveries.csv")
            with open(path, "w", encoding="utf-8") as file:
                file.write("x")

            self.assertEqual(["deliveries.csv"], archive_logs(True, directory))
            with open(path, encoding="utf-8") as file:
                self.assertEqual("", file.read())
            archive_root = os.path.join(directory, "archive")
            self.assertTrue(os.listdir(archive_root))


if __name__ == "__main__":
    unittest.main()
