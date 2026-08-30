import random
import unittest

from stock_alarm.peak_decay_backtest import block_bootstrap, block_bootstrap_indices


class PeakDecayBacktestTest(unittest.TestCase):
    def test_block_bootstrap_is_reproducible_and_keeps_length(self):
        values = [float(index) for index in range(100)]
        first = block_bootstrap(values, 37, 5, random.Random(42))
        second = block_bootstrap(values, 37, 5, random.Random(42))
        self.assertEqual(first, second)
        self.assertEqual(37, len(first))

    def test_each_bootstrap_block_preserves_source_adjacency(self):
        indices = block_bootstrap_indices(100, 20, 5, random.Random(7))
        for start in range(0, 20, 5):
            block = indices[start:start + 5]
            self.assertEqual(list(range(block[0], block[0] + len(block))), block)

    def test_bootstrap_resamples_from_observed_support(self):
        values = [-0.03, -0.01, 0.0, 0.02, 0.04]
        sample = block_bootstrap(values, 100, 2, random.Random(9))
        self.assertTrue(set(sample).issubset(set(values)))
        self.assertGreater(len(set(sample)), 1)


if __name__ == "__main__":
    unittest.main()
