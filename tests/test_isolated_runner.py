import io
import sys
import unittest
from unittest.mock import patch

from stock_alarm.isolated_runner import use_utf8_streams


class IsolatedRunnerTest(unittest.TestCase):
    def test_streams_are_switched_to_utf8(self):
        stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
        stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
        with patch.object(sys, "stdout", stdout), patch.object(sys, "stderr", stderr):
            use_utf8_streams()
            print("삼성전자")
            sys.stdout.flush()
        self.assertEqual("utf-8", stdout.encoding)
        self.assertEqual("utf-8", stderr.encoding)
        self.assertEqual("삼성전자".encode("utf-8"), stdout.buffer.getvalue().strip())

    def test_missing_streams_under_pythonw_are_left_alone(self):
        with patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
            use_utf8_streams()  # must not raise


if __name__ == "__main__":
    unittest.main()
