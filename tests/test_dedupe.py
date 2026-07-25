import os
import tempfile
import unittest

from stock_alarm.notifier import mark_sent, was_sent


class DedupeTest(unittest.TestCase):
    def test_already_sent_tracks_message_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "sent.csv")

            self.assertFalse(was_sent("telegram", "hello", path))
            mark_sent("telegram", "hello", path)
            self.assertTrue(was_sent("telegram", "hello", path))


if __name__ == "__main__":
    unittest.main()
