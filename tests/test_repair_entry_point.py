"""Frozen reconstruction must bypass the interactive setup wizard."""
import unittest
import subprocess
import sys
from unittest import mock

import main as entry


class RepairEntryPointTests(unittest.TestCase):
    def test_importing_worker_entry_does_not_load_qt_native_runtime(self):
        result = subprocess.run(
            [sys.executable, "-c", "import main, sys; assert 'PyQt5.QtWidgets' not in sys.modules"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reconstruction_request_never_opens_gui(self):
        with mock.patch("sys.argv", ["MolDynStudio.exe", "--structure-repair-worker", "request.json"]), \
             mock.patch("core.repair_worker.main", return_value=7) as worker, \
             mock.patch("setup_wizard.SetupWizard"), \
             mock.patch("PyQt5.QtWidgets.QApplication") as app:
            self.assertEqual(entry.main(), 7)
        worker.assert_called_once_with("request.json")
        app.assert_not_called()

    def test_worker_argument_count_is_validated(self):
        with mock.patch("sys.argv", ["MolDynStudio.exe", "--structure-repair-worker"]), \
             mock.patch("setup_wizard.SetupWizard"), \
             mock.patch("PyQt5.QtWidgets.QApplication") as app:
            with self.assertRaisesRegex(SystemExit, "request JSON"):
                entry.main()
        app.assert_not_called()
