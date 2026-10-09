import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import main as entry
from core import bundle_check


class BundleCheckTests(unittest.TestCase):
    def test_self_check_dispatch_bypasses_interactive_wizard(self):
        with (mock.patch.object(sys, "argv", ["MolDynStudio.exe", "--self-check", "report.json"]),
              mock.patch.object(bundle_check, "run_self_check", return_value=3) as check,
              mock.patch("setup_wizard.SetupWizard") as wizard):
            self.assertEqual(entry.main(), 3)
        check.assert_called_once_with("report.json")
        wizard.assert_not_called()

    def test_worker_dispatch_and_missing_report_argument(self):
        with (mock.patch.object(sys, "argv", ["MolDynStudio.exe", "--bundle-check-worker", "report.json"]),
              mock.patch.object(bundle_check, "worker_main", return_value=2) as worker):
            self.assertEqual(entry.main(), 2)
        worker.assert_called_once_with("report.json")
        with mock.patch.object(sys, "argv", ["MolDynStudio.exe", "--self-check"]):
            with self.assertRaisesRegex(SystemExit, "report JSON"):
                entry.main()

    def test_python_probe_error_writes_failed_report_and_runs_remaining_checks(self):
        probes = (("broken", mock.Mock(side_effect=RuntimeError("missing native library"))),
                  ("working", lambda: {"value": 5}))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            with mock.patch.object(bundle_check, "PROBES", probes):
                self.assertEqual(bundle_check.worker_main(path), 1)
            report = json.loads(path.read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["checks"][0]["error"], "missing native library")
        self.assertEqual(report["checks"][1]["status"], "passed")

    def test_native_crash_cannot_leave_a_successful_final_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            def crash(command, **kwargs):
                Path(command[-1]).write_text(json.dumps({"status": "running", "active_probe": "openmm", "checks": []}))
                return subprocess.CompletedProcess(command, 3221225477)
            with mock.patch.object(bundle_check.subprocess, "run", side_effect=crash):
                self.assertEqual(bundle_check.run_self_check(path), 1)
            report = json.loads(path.read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["active_probe"], "openmm")
        self.assertEqual(report["native_exit_code"], 3221225477)

    def test_timeout_is_recorded_and_frozen_command_uses_same_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            with (mock.patch.object(sys, "frozen", True, create=True),
                  mock.patch.object(bundle_check.subprocess, "run", side_effect=subprocess.TimeoutExpired("probe", 180)) as run):
                self.assertEqual(bundle_check.run_self_check(path), 1)
            self.assertEqual(run.call_args.args[0][:2], [sys.executable, "--bundle-check-worker"])
            self.assertEqual(json.loads(path.read_text())["status"], "failed")

    def test_incomplete_child_report_cannot_claim_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            def incomplete(command, **kwargs):
                Path(command[-1]).write_text(json.dumps({"status": "passed", "checks": [{"name": "mdanalysis", "status": "passed"}]}))
                return subprocess.CompletedProcess(command, 0)
            with mock.patch.object(bundle_check.subprocess, "run", side_effect=incomplete):
                self.assertEqual(bundle_check.run_self_check(path), 1)
            self.assertEqual(json.loads(path.read_text())["status"], "failed")

    def test_corrupt_child_json_still_produces_a_valid_failure_report(self):
        for payload in ("{broken", "[]"):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "report.json"
                def corrupt(command, **kwargs):
                    Path(command[-1]).write_text(payload)
                    return subprocess.CompletedProcess(command, 0)
                with mock.patch.object(bundle_check.subprocess, "run", side_effect=corrupt):
                    self.assertEqual(bundle_check.run_self_check(path), 1)
                self.assertEqual(json.loads(path.read_text())["status"], "failed")
