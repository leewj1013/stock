import os
import tempfile
import unittest

from stock_alarm.core_satellite_tracker import record


class CoreSatelliteTrackerTest(unittest.TestCase):
    def test_holds_within_a_quarter_and_rebalances_at_the_next(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "core30.db")
            first = record(10_000, 100_000_000, path=path, now="2026-09-15T15:00:00")
            self.assertEqual((30_000_000, 70_000_000, 1), (first["core_value"], first["satellite_value"], first["rebalanced"]))

            # Same quarter: index +10%, satellite flat -> weights drift, no rebalance.
            drift = record(11_000, 100_000_000, path=path, now="2026-09-30T15:00:00")
            self.assertEqual((33_000_000, 70_000_000, 0), (drift["core_value"], drift["satellite_value"], drift["rebalanced"]))

            # New quarter: back to 30/70, paying 0.05% on the 2,100,000 moved.
            rebalanced = record(11_000, 100_000_000, path=path, now="2026-10-01T15:00:00")
            self.assertEqual(1, rebalanced["rebalanced"])
            self.assertEqual(103_000_000 - 1_050, rebalanced["equity"])
            self.assertAlmostEqual(0.3, rebalanced["core_value"] / rebalanced["equity"], places=6)


if __name__ == "__main__":
    unittest.main()
