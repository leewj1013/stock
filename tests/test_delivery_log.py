import csv
import os
import tempfile
import unittest

from stock_alarm.notifier import write_delivery_log


class DeliveryLogTest(unittest.TestCase):
    def test_writes_delivery_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "deliveries.csv")
            write_delivery_log("telegram", path)

            with open(path, newline="", encoding="utf-8-sig") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual("telegram", rows[0]["channel"])


if __name__ == "__main__":
    unittest.main()
