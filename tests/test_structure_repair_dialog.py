from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QCloseEvent
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from windows.structure_repair_dialog import StructureRepairDialog, StructureTask
from tabs.md_setup_tab import MDSetupTab


class StructureRepairDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "original.pdb"
        self.source.write_text("END\n", encoding="ascii")
        self.dialog = StructureRepairDialog(str(self.source), self.temp.name)
        self.addCleanup(self.dialog.close)

    def wait_idle(self):
        deadline = time.monotonic() + 5
        while self.dialog.is_busy() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.app.processEvents()
        self.assertFalse(self.dialog.is_busy())

    def test_initial_state_requires_inspection_and_explicit_adoption(self):
        self.assertFalse(self.dialog.generate_button.isEnabled())
        self.assertFalse(self.dialog.adopt_button.isEnabled())
        self.assertFalse(self.dialog.remove_heterogens.isChecked())
        self.assertFalse(self.dialog.missing_heavy.isChecked())
        self.assertEqual(self.dialog.gaps.count(), 0)

    def test_worker_reports_failure_without_destroying_running_thread(self):
        with mock.patch("windows.structure_repair_dialog.audit_structure", side_effect=ValueError("Malformed PDB atom record")):
            self.dialog.inspect_structure()
            self.wait_idle()
        self.assertIn("Malformed PDB atom record", self.dialog.status.text())
        self.assertFalse(self.dialog.adopt_button.isEnabled())
        self.assertEqual(self.source.read_text(), "END\n")

    def test_candidate_invalidated_when_repair_choice_changes(self):
        self.dialog._candidate = SimpleNamespace(ready=True)
        self.dialog.adopt_button.setEnabled(True)
        self.dialog.remove_heterogens.setChecked(True)
        self.assertIsNone(self.dialog._candidate)
        self.assertFalse(self.dialog.adopt_button.isEnabled())

    def test_adoption_rechecks_hash_and_rejects_changed_candidate(self):
        self.dialog._candidate = SimpleNamespace(ready=True, candidate_path="candidate.pdb", report_path="report.json")
        adopted = []
        self.dialog.repair_adopted.connect(lambda candidate, report: adopted.append((candidate, report)))
        with mock.patch("windows.structure_repair_dialog.verify_repair_result", side_effect=ValueError("Candidate hash mismatch")):
            self.dialog.adopt_candidate()
            self.wait_idle()
        self.assertEqual(adopted, [])
        self.assertIn("Candidate hash mismatch", self.dialog.status.text())
        self.assertFalse(self.dialog.adopt_button.isEnabled())

    def test_verified_adoption_emits_candidate_and_report_only_after_verification(self):
        result = SimpleNamespace(ready=True, candidate_path="candidate.pdb", report_path="report.json", report_sha256="a" * 64)
        self.dialog._candidate = result
        adopted = []
        self.dialog.repair_adopted.connect(lambda candidate, report, digest: adopted.append((candidate, report, digest)))
        with mock.patch("windows.structure_repair_dialog.verify_repair_result") as verify:
            self.dialog.adopt_candidate()
            self.assertEqual(adopted, [])
            self.wait_idle()
        verify.assert_called_once_with(result)
        self.assertEqual(adopted, [("candidate.pdb", "report.json", "a" * 64)])

    def test_busy_guard_blocks_close_reset_and_second_job(self):
        self.dialog._active = True
        event = QCloseEvent()
        self.dialog.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertFalse(self.dialog.reset_session())
        with mock.patch("windows.structure_repair_dialog.StructureTask") as factory:
            self.dialog.inspect_structure()
        factory.assert_not_called()
        self.dialog._active = False

    def test_other_operation_blocks_inspection(self):
        self.dialog.operation_blocked = lambda: True
        with mock.patch("windows.structure_repair_dialog.StructureTask") as factory:
            self.dialog.inspect_structure()
        factory.assert_not_called()

    def test_repair_choices_preserve_complex_and_gap_selection_is_explicit(self):
        self.dialog._add_choice(self.dialog.chains, "A", "Chain A", True)
        self.dialog._add_choice(self.dialog.chains, "B", "Chain B", True)
        self.dialog._add_choice(self.dialog.modres, "A:214:BHD", "BHD214 → ASP")
        self.dialog._add_choice(self.dialog.gaps, "A:internal:5", "Internal gap")
        self.dialog._add_choice(self.dialog.gaps, "A:n_terminal", "N-terminal gap")
        choices = self.dialog.choices()
        self.assertEqual(choices.chain_ids, ("A", "B"))
        self.assertEqual(choices.normalize_modres, ())
        self.assertEqual(choices.fill_gap_ids, ())
        self.assertFalse(choices.remove_heterogens)
        self.dialog.gaps.item(0).setCheckState(Qt.Checked)
        self.assertEqual(self.dialog.choices().fill_gap_ids, ("A:internal:5",))

    def test_fasta_audit_never_guesses_record_or_chain(self):
        with mock.patch("core.sequence_mapping.parse_fasta", return_value=()) as parse, mock.patch(
            "windows.structure_repair_dialog.audit_structure", return_value=object()
        ) as audit, mock.patch.object(StructureRepairDialog, "_describe_audit", return_value="audit"):
            self.dialog._inspect(str(self.source), "reference.fasta", None, None)
            self.assertEqual(audit.call_args.kwargs["fasta_path"], None)
            self.dialog._inspect(str(self.source), "reference.fasta", "B", "record-two")
            self.assertEqual(audit.call_args.kwargs["chain_id"], "B")
            self.assertEqual(audit.call_args.kwargs["fasta_record_id"], "record-two")
            parse.assert_called_with("reference.fasta")

    def complete_source(self):
        rows = [
            "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00 20.00           N  ",
            "ATOM      2  CA  ALA A   1       1.450   0.000   0.000  1.00 20.00           C  ",
            "ATOM      3  C   ALA A   1       2.900   0.000   0.000  1.00 20.00           C  ",
            "ATOM      4  O   ALA A   1       3.500   1.000   0.000  1.00 20.00           O  ",
            "ATOM      5  CB  ALA A   1       1.450   1.500   0.000  1.00 20.00           C  ",
            "HETATM    6  C1  LIG Z   2      10.000   0.000   0.000  1.00 20.00           C  ",
            "END",
        ]
        original = "\n".join(rows) + "\n"
        self.source.write_text(original)
        return original

    def test_real_inspection_retains_heterogen_chain_and_requires_fasta_mapping(self):
        original = self.complete_source()
        fasta = Path(self.temp.name) / "reference.fasta"
        fasta.write_text(">one reference\nA\n>two other\nGG\n")
        self.dialog.fasta.setText(str(fasta))
        self.dialog.inspect_structure()
        self.wait_idle()
        self.assertEqual(self.dialog.choices().chain_ids, ("A", "Z"))
        self.assertIsNone(self.dialog.fasta_chain.currentData())
        self.assertIsNone(self.dialog.fasta_record.currentData())
        self.dialog.fasta_chain.setCurrentIndex(self.dialog.fasta_chain.findData("A"))
        self.dialog.fasta_record.setCurrentIndex(self.dialog.fasta_record.findData("one"))
        self.dialog.inspect_structure()
        self.wait_idle()
        self.assertIn("Sequence mapping: FASTA / one", self.dialog.audit_report.toPlainText())
        self.assertEqual(self.source.read_text(), original)

    def test_real_audit_plan_candidate_and_verified_explicit_adoption(self):
        original = self.complete_source()
        page = MDSetupTab()
        self.addCleanup(page.deleteLater)
        self.dialog.repair_adopted.connect(page._adopt_structure_repair)
        self.dialog.inspect_structure()
        self.wait_idle()
        self.dialog.review_plan()
        self.assertTrue(self.dialog.generate_button.isEnabled())
        self.dialog.generate_candidate()
        self.wait_idle()
        self.assertTrue(self.dialog.adopt_button.isEnabled(), self.dialog.status.text())
        self.assertEqual(page.fields["protein"].text(), "")
        self.assertIn("Observed atoms retained: 6", self.dialog.candidate_report.toPlainText())
        self.assertIn("Modeled atoms added: 0", self.dialog.candidate_report.toPlainText())
        self.dialog.adopt_candidate()
        self.wait_idle()
        candidate = Path(page.fields["protein"].text())
        self.assertTrue(candidate.is_file())
        self.assertIn("LIG Z", candidate.read_text())
        self.assertTrue(Path(page.fields["structure_repair_report"].text()).is_file())
        self.assertEqual(self.source.read_text(), original)

    def test_changing_input_while_worker_runs_discards_old_result(self):
        with mock.patch.object(self.dialog, "_inspect", return_value=(object(), (), False)):
            with mock.patch.object(self.dialog, "_show_audit") as show:
                self.dialog.inspect_structure()
                self.dialog.source.setText("replacement.pdb")
                self.wait_idle()
        show.assert_not_called()
        self.assertIsNone(self.dialog._candidate)
        self.assertIn("discarded", self.dialog.status.text())

    def test_atom_line_deletions_require_explicit_positive_integers(self):
        self.dialog.delete_lines.setText("12, 19 34")
        self.assertEqual(self.dialog.choices().delete_atom_lines, (12, 19, 34))
        self.dialog.delete_lines.setText("12-34")
        with self.assertRaises(ValueError):
            self.dialog.choices()

    def test_empty_chain_selection_cannot_silently_mean_keep_all(self):
        self.dialog._add_choice(self.dialog.chains, "A", "Chain A", False)
        with self.assertRaisesRegex(ValueError, "at least one chain"):
            self.dialog.choices()

    def test_new_source_clears_line_specific_and_chemical_changes(self):
        self.dialog.delete_lines.setText("12, 19")
        self.dialog.remove_heterogens.setChecked(True)
        self.dialog.missing_heavy.setChecked(True)
        self.dialog.source.setText("another-complex.pdb")
        self.assertEqual(self.dialog.delete_lines.text(), "")
        self.assertFalse(self.dialog.remove_heterogens.isChecked())
        self.assertFalse(self.dialog.missing_heavy.isChecked())

    def test_task_records_results_and_errors(self):
        good = StructureTask(lambda: "done")
        good.run()
        self.assertEqual(good.result, "done")
        bad = StructureTask(lambda: (_ for _ in ()).throw(ValueError("failed")))
        bad.run()
        self.assertIn("failed", bad.error)

    def test_md_setup_keeps_report_with_adoption_and_clears_it_on_path_edit(self):
        page = MDSetupTab()
        self.addCleanup(page.deleteLater)
        page._adopt_structure_repair("candidate.pdb", "repair_report.json", "a" * 64)
        self.assertEqual(page.fields["protein"].text(), "candidate.pdb")
        self.assertEqual(page.fields["structure_repair_report"].text(), "repair_report.json")
        self.assertEqual(page.fields["structure_repair_report_sha256"].text(), "a" * 64)
        page.fields["protein"].setText("different.pdb")
        self.assertEqual(page.fields["structure_repair_report"].text(), "")
        self.assertEqual(page.fields["structure_repair_report_sha256"].text(), "")

    def test_approval_hash_is_persisted_and_transferred_without_recalculation(self):
        page = MDSetupTab()
        self.addCleanup(page.deleteLater)
        report = Path(self.temp.name) / "approved-report.json"
        report.write_text("{}")
        page._adopt_structure_repair(str(self.source), str(report), "b" * 64)
        page.fields["project_dir"].setText(self.temp.name)
        restored = MDSetupTab()
        self.addCleanup(restored.deleteLater)
        restored.set_state(dict(reversed(tuple(page.get_state().items()))))
        report.write_text('{"changes": "edited after adoption"}')
        worker = mock.Mock()
        worker.isRunning.return_value = False
        with mock.patch("tabs.md_setup_tab.PreparationWorker", return_value=worker) as factory:
            restored.generate_topology_preview()
        request = factory.call_args.args[0]
        self.assertEqual(request.structure_repair_report, str(report))
        self.assertEqual(request.structure_repair_report_sha256, "b" * 64)

    def test_legacy_repair_report_without_approved_hash_requires_readoption(self):
        page = MDSetupTab()
        self.addCleanup(page.deleteLater)
        page._adopt_structure_repair(str(self.source), "previous-report.json", "d" * 64)
        page.set_state({"protein": str(self.source), "project_dir": self.temp.name,
                        "structure_repair_report": "legacy-report.json"})
        with mock.patch("tabs.md_setup_tab.PreparationWorker") as factory, mock.patch("tabs.md_setup_tab.QMessageBox.warning") as warning:
            page.generate_topology_preview()
        factory.assert_not_called()
        self.assertIn("re-adopt", warning.call_args.args[2].lower())

    def test_changing_report_path_clears_approval_digest(self):
        page = MDSetupTab()
        self.addCleanup(page.deleteLater)
        page._adopt_structure_repair(str(self.source), "report.json", "c" * 64)
        page.fields["structure_repair_report"].setText("different-report.json")
        self.assertEqual(page.fields["structure_repair_report_sha256"].text(), "")

    def test_md_setup_preparation_blocks_a_previously_open_repair_dialog(self):
        page = MDSetupTab()
        self.addCleanup(page.deleteLater)
        page._preparation_active = True
        self.assertTrue(page._repair_operation_blocked())
        page._preparation_active = False
        self.assertFalse(page._repair_operation_blocked())


if __name__ == "__main__":
    unittest.main()
