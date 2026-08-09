import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_alarm.server_runner import run


class ServerRunnerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        patcher = patch("stock_alarm.server_runner._log_paths", return_value=(root / "out.log", root / "err.log"))
        patcher.start()
        self.addCleanup(patcher.stop)

    @patch("stock_alarm.server_runner.load_env")
    @patch("stock_alarm.server_runner.should_run", return_value=False)
    @patch("stock_alarm.server_runner._run_module")
    def test_gate_skips_all_steps(self, module, _gate, _env):
        self.assertEqual(0, run("intraday"))
        module.assert_not_called()

    @patch("stock_alarm.server_runner.load_env")
    @patch("stock_alarm.server_runner.should_run", return_value=True)
    @patch("stock_alarm.server_runner._write_log")
    @patch("stock_alarm.server_runner._run_module", side_effect=[1, 0])
    def test_required_failure_alerts_and_stops(self, module, _log, _gate, _env):
        self.assertEqual(1, run("intraday"))
        self.assertEqual("stock_alarm", module.call_args_list[0].args[0])
        self.assertEqual(("stock_alarm", "1"), module.call_args_list[1].args[4])
        self.assertEqual(2, module.call_count)

    @patch("stock_alarm.server_runner.load_env")
    @patch("stock_alarm.server_runner.should_run", return_value=True)
    @patch("stock_alarm.server_runner._write_log")
    @patch("stock_alarm.server_runner._run_module", side_effect=[1, 0, 0, 0, 0, 0, 0, 0])
    def test_optional_daily_failure_continues(self, module, _log, _gate, _env):
        self.assertEqual(0, run("daily"))
        self.assertGreater(module.call_count, 2)

    def test_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            run("unknown")


if __name__ == "__main__":
    unittest.main()
