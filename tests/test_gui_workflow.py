from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QPushButton

import gromacs_analysis_studio_v11 as studio
from core.settings import SettingsStore
from settings_dialog import SettingsDialog


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = studio.MainWindow()
        self.window._autosave_timer.stop()
        self.addCleanup(self.window.close)

    def test_new_project_restores_defaults_and_clears_results(self):
        project = self.window.pages["Project"]
        default = project.fields["project_name"].text()
        project.fields["project_name"].setText("stale")
        self.window.pages["Analysis"].fields["topology"].setText("old.tpr")
        self.window.new_session()
        self.assertEqual(project.fields["project_name"].text(), default)
        self.assertEqual(self.window.pages["Analysis"].fields["topology"].text(), "")

    def test_busy_project_rejects_new_open_and_direct_restore(self):
        project = self.window.pages["Project"]
        project.fields["project_name"].setText("running")
        with mock.patch.object(self.window, "_work_in_progress", return_value=True):
            self.window.new_session()
            with mock.patch.object(studio.QFileDialog, "getOpenFileName") as choose:
                self.window.load_session()
            self.window.apply_session_data({"pages": {"Project": {"project_name": "replaced"}}})
        choose.assert_not_called()
        self.assertEqual(project.fields["project_name"].text(), "running")

    def test_busy_window_cannot_close(self):
        event = mock.Mock()
        with mock.patch.object(self.window, "_work_in_progress", return_value=True):
            self.window.closeEvent(event)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()

    def test_opening_partial_project_clears_previous_inputs_and_results(self):
        analysis = self.window.pages["Analysis"]
        analysis.fields["topology"].setText("old-system.tpr")
        analysis.results.setPlainText("Old result")
        self.window.apply_session_data({"pages": {"Project": {"project_name": "New study"}}})
        self.assertEqual(analysis.fields["topology"].text(), "")
        self.assertEqual(analysis.results.toPlainText(), "")

    def test_invalid_session_rejected_before_changing_any_field(self):
        project = self.window.pages["Project"]
        project.fields["project_name"].setText("Current study")
        with self.assertRaises(studio.ProjectFormatError):
            self.window.apply_session_data({"pages": {"Project": {"project_name": "Broken study"}, "MD Setup": []}})
        self.assertEqual(project.fields["project_name"].text(), "Current study")

    def test_configuration_pages_have_no_fake_run_buttons(self):
        for name in ["Project", "Trajectory Preprocessing", "Structural Stability", "End-Point Free Energy", "Batch Comparison"]:
            with self.subTest(page=name):
                self.assertFalse(any("Run This Module" in button.text() for button in self.window.pages[name].findChildren(QPushButton)))

    def test_results_start_empty_with_real_qc_dashboard(self):
        viewer = self.window.pages["Results Viewer"]
        self.assertEqual(viewer.tabs.count(), 1)
        self.assertEqual(viewer.qc_dashboard.table.rowCount(), 0)

    def test_load_system_checks_file_existence_and_hands_off_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            topology = Path(temp) / "system.tpr"
            trajectory = Path(temp) / "traj.xtc"
            topology.write_bytes(b"tpr")
            trajectory.write_bytes(b"xtc")
            page = self.window.pages["Load System"]
            page.fields["tpr_file"].setText(str(topology))
            page.fields["trajectory_file"].setText(str(trajectory))
            page.validate_inputs()
            self.assertEqual(self.window.pages["Analysis"].fields["topology"].text(), str(topology))
            page.fields["tpr_file"].setText(str(topology.with_name("missing.tpr")))
            page.validate_inputs()
            self.assertIn("not found", page.summary.toPlainText().lower())
            self.assertEqual(self.window.pages["Analysis"].fields["topology"].text(), str(topology))

    def test_production_handoff_does_not_start_analysis(self):
        payload = {"topology": "md.tpr", "trajectory": "md.xtc", "energy": "md.edr"}
        page = self.window.pages["Analysis"]
        page.fields["prepared"].setChecked(True)
        with mock.patch.object(page, "run_qc_pipeline") as start:
            self.window._on_production_completed(payload)
        self.assertEqual(page.fields["trajectory"].text(), "md.xtc")
        self.assertEqual(self.window.pages["Load System"].fields["tpr_file"].text(), "md.tpr")
        start.assert_not_called()
        self.assertFalse(page.fields["prepared"].isChecked())

    def test_manual_inputs_and_new_session_do_not_reuse_run_backend(self):
        page = self.window.pages["Analysis"]
        runtime = {"gromacs_binary": "/configured/gmx", "conda_environment": "configured-env"}
        payload = {"topology": "md.tpr", "trajectory": "md.xtc", "gromacs_binary": "/run/gmx", "conda_environment": "run-env"}
        with mock.patch.object(page.settings, "value", side_effect=lambda key: runtime[key]):
            self.window._on_production_completed(payload)
            self.assertEqual(page.qc_inputs().gromacs_binary, "/run/gmx")
            self.window._on_analysis_inputs({"topology": "other.tpr", "trajectory": "other.xtc"})
            self.assertEqual(page.qc_inputs().conda_env, "configured-env")
            self.assertEqual(page.qc_inputs().gromacs_binary, "/configured/gmx")
            self.window._on_production_completed(payload)
            self.window.new_session()
            self.assertEqual(page.qc_inputs().gromacs_binary, "/configured/gmx")

    def test_reports_use_active_project_for_real_export(self):
        reports = self.window.pages["Reports & Export"]
        with tempfile.TemporaryDirectory() as temp:
            self.window.active_project_dir = Path(temp)
            reports.fields["export_dir"].setText(temp)
            archive = Path(temp) / "evidence.zip"
            with mock.patch.object(studio, "export_evidence_bundle", return_value=archive) as export:
                reports.export_bundle()
                self.assertTrue(reports._worker.wait(5000))
                self.app.processEvents()
            export.assert_called_once()
            self.assertEqual(export.call_args.args[0], Path(temp))
            self.assertEqual(export.call_args.args[1].parent, Path(temp))
            self.assertEqual(export.call_args.args[1].suffix, ".zip")
            self.assertIn("evidence.zip", reports.status.text())

    def test_all_main_menus_are_english(self):
        labels = [action.text() for action in self.window.menuBar().actions()]
        self.assertEqual(labels, ["File", "Workflow", "View", "Help"])

    def test_preferences_only_offer_supported_display_options(self):
        settings = SettingsStore("MolDynStudioTests", "workflow-preferences")
        with mock.patch.object(settings, "set_value") as save:
            dialog = SettingsDialog(settings)
            self.addCleanup(dialog.close)
            self.assertEqual([dialog.language.itemText(i) for i in range(dialog.language.count())], ["English"])
            self.assertEqual([dialog.theme.itemText(i) for i in range(dialog.theme.count())], ["Light"])
            dialog.docklens.setText("C:/Tools/DockLens.exe")
            dialog.save()
        self.assertIn(mock.call("docklens_binary", "C:/Tools/DockLens.exe"), save.call_args_list)


if __name__ == "__main__":
    unittest.main()
