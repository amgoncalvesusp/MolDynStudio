"""Repaired coordinates must retain provenance at the simulation boundary."""

from dataclasses import replace
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from core.preparation_orchestrator import PreparationError, PreparationOrchestrator
from core.run_manifest import load_manifest, save_manifest
from tests.test_preparation_orchestrator import MaterializingRunner, request


class RepairPreparationHandoffTests(unittest.TestCase):
    def test_edited_report_after_adoption_is_rejected_before_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, runner = self._service(Path(tmp))
            service.request = replace(service.request, structure_repair_report_sha256="b" * 64)
            with mock.patch("core.preparation_orchestrator._verify_repair_report", return_value={"report_sha256": "d" * 64}):
                with self.assertRaisesRegex(PreparationError, "changed after adoption"):
                    service.run()
            self.assertEqual(runner.commands, [])

    def test_report_without_saved_adoption_digest_requires_readoption(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, runner = self._service(Path(tmp))
            service.request = replace(service.request, structure_repair_report_sha256="")
            with self.assertRaisesRegex(PreparationError, "adopt"):
                service.run()
            self.assertEqual(runner.commands, [])

    def _service(self, root, report="repair_report.json"):
        runner = MaterializingRunner()
        item = replace(request(root), structure_repair_report=str(root / report) if report else "",
                       structure_repair_report_sha256="b" * 64 if report else "")
        service = PreparationOrchestrator(item, runner=runner, structure_validator=lambda _path: None)
        return service, runner

    def test_changed_repair_rejected_before_any_external_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, runner = self._service(Path(tmp))
            with mock.patch("core.preparation_orchestrator._verify_repair_report", side_effect=ValueError("Candidate hash changed")):
                with self.assertRaisesRegex(PreparationError, "hash changed"):
                    service.run()
            self.assertEqual(runner.commands, [])

    def test_verified_report_is_recorded_and_rechecked_before_pdb2gmx(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, runner = self._service(root)
            metadata = {"report_path": str(root / "repair_report.json"), "candidate_sha256": "a" * 64,
                        "report_sha256": "b" * 64}
            with mock.patch("core.preparation_orchestrator._verify_repair_report", return_value=metadata) as verify:
                service.run()
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(runner.commands[0].argv[1], "pdb2gmx")
            self.assertEqual(load_manifest(root / "moldynstudio_run.json").metadata["structure_repair"], metadata)

    def test_change_after_initial_validation_still_blocks_topology_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, runner = self._service(Path(tmp))
            with mock.patch("core.preparation_orchestrator._verify_repair_report", side_effect=({"report_sha256": "b" * 64}, ValueError("Changed after review"))):
                with self.assertRaisesRegex(PreparationError, "Changed after review"):
                    service.run()
            self.assertEqual(runner.commands, [])

    def test_new_unrepaired_input_drops_previous_repair_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, _runner = self._service(root, report="")
            service.run()
            path = root / "moldynstudio_run.json"
            manifest = load_manifest(path)
            save_manifest(replace(manifest, metadata={"structure_repair": {"old": True}, "owner": "lab"}), path)
            service.run()
            self.assertEqual(load_manifest(path).metadata, {"owner": "lab"})

    def test_replacing_candidate_and_report_together_does_not_change_reviewed_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            service, runner = self._service(Path(tmp))
            first = {"candidate_sha256": "a" * 64, "report_sha256": "b" * 64}
            replacement = {"candidate_sha256": "c" * 64, "report_sha256": "d" * 64}
            with mock.patch("core.preparation_orchestrator._verify_repair_report", side_effect=(first, replacement)):
                with self.assertRaisesRegex(PreparationError, "Repair evidence changed"):
                    service.run()
            self.assertEqual(runner.commands, [])
