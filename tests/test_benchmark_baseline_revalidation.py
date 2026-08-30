import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_alarm.benchmark_baseline_revalidation import _old_random_percentiles


class BenchmarkBaselineRevalidationTest(unittest.TestCase):
    def test_old_percentile_parser_reads_random_table_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "BENCHMARK_COMPARISON_REPORT.md").write_text("| all | 100 | -1 | 0 | 1 | 8.5 | 78 | 0.2 |\n", encoding="utf-8")
            with patch("stock_alarm.benchmark_baseline_revalidation.OLD", root):
                self.assertEqual(78.0, _old_random_percentiles()["all"])


if __name__ == "__main__":
    unittest.main()
