import csv
import os
import tempfile
import unittest

from stock_alarm.notifier import write_delivery_log


class DeliveryLogTest(unittest.TestCase):
    def test_writes_delivery_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "deliveries.csv")
            write_delivery_log("telegram", path, status="delivered", message_id="77", chat_id_suffix="1234")

            with open(path, newline="", encoding="utf-8-sig") as file:
                rows = list(csv.DictReader(file))

        self.assertEqual("telegram", rows[0]["channel"])
        self.assertEqual("delivered", rows[0]["status"])
        self.assertEqual("77", rows[0]["message_id"])


if __name__ == "__main__":
    unittest.main()
