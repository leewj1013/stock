import unittest

from stock_alarm.tune import summarize


class TuneTest(unittest.TestCase):
    def test_summarize_returns_count_average_and_win_rate(self):
        self.assertEqual((2, "2.50", "50.0"), summarize([["", "", "", 0, 0, "10.00"], ["", "", "", 0, 0, "-5.00"]]))

    def test_summarize_empty_rows(self):
        self.assertEqual((0, "", ""), summarize([]))


if __name__ == "__main__":
    unittest.main()
