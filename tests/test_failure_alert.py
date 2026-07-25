import unittest

from stock_alarm.failure_alert import message


class FailureAlertTest(unittest.TestCase):
    def test_message(self):
        text = message("sell_check", "1")

        self.assertIn("[stockAlarm 장애 알림]", text)
        self.assertIn("실패 단계: sell_check", text)
        self.assertIn("종료 코드: 1", text)


if __name__ == "__main__":
    unittest.main()
