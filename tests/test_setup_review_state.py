from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from tabs.md_setup_tab import MDSetupTab


class SetupReviewStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tab = MDSetupTab()
        self.addCleanup(self.tab.deleteLater)

    def test_scientific_edits_emit_complete_current_snapshot(self):
        snapshots = []
        self.tab.setup_changed.connect(snapshots.append)
        self.tab.fields["protein"].setText("protein.pdb")
        self.tab.fields["duration"].setValue(25.0)
        self.assertEqual(snapshots[-1], self.tab.review_snapshot())
        self.assertEqual(snapshots[-1]["duration"], 25.0)
        self.assertEqual(snapshots[-1]["protein"], str(Path("protein.pdb").resolve()))

    def test_ephemeral_upload_consent_does_not_change_snapshot(self):
        self.tab.fields["ligand"].setText("ligand.mol2")
        self.tab.fields["force_field"].setCurrentText("CHARMM36m")
        before = self.tab.review_snapshot()
        snapshots = []
        self.tab.setup_changed.connect(snapshots.append)
        self.tab.fields["allow_ligand_upload"].setChecked(True)
        self.tab.fields["ligand_protonation_confirmed"].setChecked(True)
        self.assertEqual(before, self.tab.review_snapshot())
        self.assertEqual(snapshots, [])

    def test_session_restore_emits_one_final_snapshot_and_resets_consent(self):
        snapshots = []
        self.tab.setup_changed.connect(snapshots.append)
        self.tab.set_state({"protein": "other.pdb", "duration": 15.0,
                            "ligand": "ligand.mol2", "force_field": "CHARMM36m",
                            "allow_ligand_upload": True,
                            "ligand_protonation_confirmed": True})
        self.assertEqual(snapshots, [self.tab.review_snapshot()])
        self.assertFalse(self.tab.fields["allow_ligand_upload"].isChecked())
        self.assertFalse(self.tab.fields["ligand_protonation_confirmed"].isChecked())

    def test_advanced_controls_preserve_values_and_short_test_protocol(self):
        self.assertTrue(self.tab.protocol_advanced.isHidden())
        self.tab.protocol_advanced_button.click()
        self.assertFalse(self.tab.protocol_advanced.isHidden())
        self.tab.fields["nvt_steps"].setValue(12345)
        self.tab.protocol_advanced_button.click()
        self.assertEqual(self.tab.get_state()["nvt_steps"], 12345)
        self.tab.short_test_button.click()
        values = self.tab.get_state()
        self.assertEqual((values["duration"], values["nvt_steps"], values["npt_steps"]),
                         (0.002, 2000, 2000))
        self.assertEqual(values["timestep"], 1.0)
        self.assertIn("does not establish equilibration", self.tab.short_test_button.toolTip())

    def test_review_action_respects_active_project_operations(self):
        requests = []
        self.tab.review_requested.connect(lambda: requests.append(True))
        self.tab.review_run_button.click()
        self.assertEqual(requests, [True])
        self.tab.operation_blocked = lambda: True
        self.tab.review_run_button.click()
        self.assertEqual(requests, [True])

    def test_file_tree_tracks_current_inputs_for_review(self):
        self.tab.fields["project_dir"].setText("review-project")
        self.tab.fields["protein"].setText("selected-protein.pdb")
        self.tab.fields["ligand"].setText("selected-ligand.mol2")
        root = self.tab.tree.topLevelItem(0)
        self.assertEqual(root.text(0), "review-project/")
        children = [root.child(index).text(0) for index in range(root.childCount())]
        self.assertIn("selected-protein.pdb", children)
        self.assertIn("selected-ligand.mol2", children)

    def test_invalid_draft_path_does_not_escape_qt_signal(self):
        exceptions = []
        invalid_path = "invalid\x00protein.pdb"
        with mock.patch("sys.excepthook", side_effect=lambda *args: exceptions.append(args)):
            self.tab.fields["protein"].setText(invalid_path)
        self.assertEqual(exceptions, [])
        self.assertEqual(self.tab.review_snapshot()["protein"], invalid_path)
        self.tab.set_state({"protein": invalid_path, "duration": 15.0})
        self.assertEqual(self.tab.review_snapshot()["protein"], invalid_path)


if __name__ == "__main__":
    unittest.main()
