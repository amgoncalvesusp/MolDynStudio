from __future__ import annotations

import os
from http.client import IncompleteRead
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QCloseEvent

from tabs.md_setup_tab import ForceFieldImportWorker, MDSetupTab


class MDSetupTabTests(unittest.TestCase):
    def test_supported_charge_choices_and_legacy_invalid_choice_are_explicit(self):
        widget = MDSetupTab()
        self.addCleanup(widget.deleteLater)
        choices = widget.fields["charge_method"]
        self.assertEqual([choices.itemText(i) for i in range(choices.count())], ["AM1-BCC", "Gasteiger"])
        widget.set_state({"charge_method": "RESP"})
        self.assertEqual(choices.currentIndex(), -1)

    def test_preparation_blocks_duplicate_start_and_window_close(self):
        widget = MDSetupTab()
        self.addCleanup(widget.deleteLater)
        widget.fields["protein"].setText("protein.pdb")
        widget.fields["project_dir"].setText("project")
        worker = mock.Mock()
        worker.isRunning.return_value = True
        with mock.patch("tabs.md_setup_tab.PreparationWorker", return_value=worker) as factory:
            widget.generate_topology_preview()
            widget.generate_topology_preview()
        factory.assert_called_once()
        self.assertTrue(widget.is_busy())
        self.assertFalse(widget.generate_topology_button.isEnabled())
        event = QCloseEvent()
        self.assertTrue(widget.eventFilter(widget.window(), event))
        self.assertFalse(event.isAccepted())

    def test_preparation_respects_other_active_project_operations(self):
        widget = MDSetupTab()
        self.addCleanup(widget.deleteLater)
        widget.fields["protein"].setText("protein.pdb")
        widget.fields["project_dir"].setText("project")
        widget.operation_blocked = lambda: True
        with mock.patch("tabs.md_setup_tab.PreparationWorker") as factory:
            widget.generate_topology_preview()
        factory.assert_not_called()

    def test_pipeline_steps_are_mandatory_even_after_loading_legacy_choices(self):
        widget = MDSetupTab()
        self.addCleanup(widget.deleteLater)
        keys = ("run_minimization", "run_nvt", "run_npt", "run_production")
        widget.set_state({key: False for key in keys})
        for key in keys:
            self.assertTrue(widget.fields[key].isChecked())
            self.assertFalse(widget.fields[key].isEnabled())

    def test_close_is_deferred_while_force_field_worker_is_running(self):
        widget = MDSetupTab()
        worker = mock.Mock()
        worker.isRunning.return_value = True
        widget._force_field_import_worker = worker
        event = QCloseEvent()
        self.assertTrue(widget.eventFilter(widget.window(), event))
        self.assertFalse(event.isAccepted())
        worker.isRunning.return_value = False
        self.assertFalse(widget.eventFilter(widget.window(), QCloseEvent()))

    def test_download_uses_worker_and_disables_both_install_actions(self):
        widget = MDSetupTab()
        worker = mock.Mock()
        with mock.patch("tabs.md_setup_tab.ForceFieldImportWorker", return_value=worker) as worker_type:
            widget.charmm_download_button.click()
            worker_type.assert_called_once_with(None, parent=widget)
            self.assertFalse(widget.charmm_download_button.isEnabled())
            self.assertFalse(widget.charmm_import_button.isEnabled())
            worker.start.assert_called_once()
            with mock.patch("tabs.md_setup_tab.QMessageBox.warning"):
                widget._finish_charmm_import(False, "Network unavailable; import local package.")
            self.assertTrue(widget.charmm_download_button.isEnabled())
            self.assertTrue(widget.charmm_import_button.isEnabled())

    def test_worker_download_failure_reaches_gui(self):
        worker = ForceFieldImportWorker(None)
        result = []
        worker.done.connect(lambda ok, detail: result.append((ok, detail)))
        with mock.patch("tabs.md_setup_tab.download_charmm36_force_field", side_effect=OSError("offline")):
            worker.run()
        self.assertEqual(result, [(False, "offline")])

    def test_worker_reports_incomplete_http_response(self):
        worker = ForceFieldImportWorker(None)
        result = []
        worker.done.connect(lambda ok, detail: result.append((ok, detail)))
        with mock.patch("tabs.md_setup_tab.download_charmm36_force_field", side_effect=IncompleteRead(b"")):
            worker.run()
        self.assertFalse(result[0][0])
        self.assertIn("IncompleteRead", result[0][1])

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_exposes_local_charmm_package_import(self):
        with mock.patch(
            "tabs.md_setup_tab.is_valid_force_field",
            return_value=False,
        ):
            widget = MDSetupTab()

        self.assertEqual(
            widget.charmm_import_button.text(),
            "Import package…",
        )
        self.assertIn("Action needed", widget.charmm_status.text())
        self.assertIn("AMBER-family", widget.acpype.text())
        self.assertFalse(hasattr(widget, "cgenff"))
        self.assertFalse(hasattr(widget, "gaff2"))

    def test_installed_charmm_status_is_concise_and_keeps_path_in_tooltip(self):
        installed = Path("cache") / "charmm36-feb2026_cgenff-5.0.ff"
        with (
            mock.patch(
                "tabs.md_setup_tab.cached_force_field_dir",
                return_value=installed,
            ),
            mock.patch(
                "tabs.md_setup_tab.is_valid_force_field",
                return_value=True,
            ),
        ):
            widget = MDSetupTab()

        self.assertEqual(
            widget.charmm_status.text(),
            "Ready offline — CHARMM36m February 2026",
        )
        self.assertEqual(widget.charmm_status.toolTip(), str(installed))
        self.assertEqual(widget.charmm_import_button.text(), "Verify package…")

    def test_imports_charmm_archive_from_gui(self):
        widget = MDSetupTab()
        installed = Path("cache") / "charmm36-feb2026_cgenff-5.0.ff"
        worker = mock.Mock()
        with (
            mock.patch.object(
                widget,
                "_select_charmm_archive",
                return_value="official.tgz",
            ),
            mock.patch(
                "tabs.md_setup_tab.ForceFieldImportWorker",
                return_value=worker,
            ) as worker_type,
            mock.patch("tabs.md_setup_tab.QMessageBox.information"),
        ):
            widget._import_charmm_package()
            widget._finish_charmm_import(True, str(installed))

        worker_type.assert_called_once_with("official.tgz", parent=widget)
        worker.done.connect.assert_called_once()
        worker.start.assert_called_once()
        self.assertIn("Ready offline", widget.charmm_status.text())

    def test_force_field_import_worker_reports_validation_failure(self):
        worker = ForceFieldImportWorker("invalid.tgz")
        result: list[tuple[bool, str]] = []
        worker.done.connect(lambda ok, detail: result.append((ok, detail)))

        with mock.patch(
            "tabs.md_setup_tab.install_charmm36_archive",
            side_effect=ValueError("checksum mismatch"),
        ):
            worker.run()

        self.assertEqual(result, [(False, "checksum mismatch")])

    def test_charmm_requires_reviewed_hydrogenated_mol2(self):
        widget = MDSetupTab()
        widget.fields["project_dir"].setText("project")
        widget.fields["protein"].setText("protein.pdb")
        widget.fields["ligand"].setText("ligand.sdf")
        widget.fields["force_field"].setCurrentText("CHARMM36m")

        with (
            mock.patch("tabs.md_setup_tab.QMessageBox.warning") as warning,
            mock.patch("tabs.md_setup_tab.PreparationWorker") as prep,
        ):
            widget.generate_topology_preview()

        prep.assert_not_called()
        self.assertIn("MOL2", warning.call_args.args[2])

    def test_charmm_keeps_expected_charge_but_disables_amber_charge_method(self):
        widget = MDSetupTab()
        widget.fields["ligand"].setText("ligand.sdf")
        widget.fields["force_field"].setCurrentText("CHARMM36m")

        self.assertFalse(widget.fields["charge_method"].isEnabled())
        self.assertTrue(widget.fields["charge"].isEnabled())
        self.assertIn("MATCH", widget.ligand_compatibility.text())
        self.assertIn("MATCH", widget.acpype.text())
        self.assertTrue(widget.fields["charge_method"].isHidden())

        widget.fields["force_field"].setCurrentText("AMBER14SB")

        self.assertTrue(widget.fields["charge_method"].isEnabled())
        self.assertTrue(widget.fields["charge"].isEnabled())
        self.assertIn("Ready", widget.ligand_compatibility.text())

    def test_charmm_worker_receives_review_upload_and_all_complex_copies(self):
        widget = MDSetupTab()
        self.addCleanup(widget.deleteLater)
        widget.fields["project_dir"].setText("project")
        widget.fields["protein"].setText("complex.pdb")
        widget.fields["ligand"].setText("twb.mol2")
        widget.fields["force_field"].setCurrentText("CHARMM36m")
        widget.fields["charge"].setValue(-2)
        widget.fields["ligand_from_complex"].setChecked(True)
        widget.fields["source_residue_name"].setText("twb")
        widget.fields["excluded_residue_names"].setText("gol, EDO")
        widget.fields["ligand_protonation_confirmed"].setChecked(True)
        widget.fields["allow_ligand_upload"].setChecked(True)
        worker = mock.Mock()
        with mock.patch("tabs.md_setup_tab.PreparationWorker", return_value=worker) as factory:
            widget.generate_topology_preview()
        request = factory.call_args.args[0]
        self.assertEqual(request.ligand_backend, "swissparam_match")
        self.assertEqual(request.net_charge, -2)
        self.assertTrue(request.ligand_protonation_confirmed)
        self.assertTrue(request.allow_ligand_upload)
        self.assertEqual(request.source_residue_name, "TWB")
        self.assertEqual(request.excluded_residue_names, ("GOL", "EDO"))
        worker.start.assert_called_once()

    def test_loading_project_never_restores_upload_consent_or_chemistry_review(self):
        widget = MDSetupTab()
        self.addCleanup(widget.deleteLater)
        widget.set_state({"force_field": "CHARMM36m", "ligand": "twb.mol2",
                          "allow_ligand_upload": True, "ligand_protonation_confirmed": True})
        self.assertFalse(widget.fields["allow_ligand_upload"].isChecked())
        self.assertFalse(widget.fields["ligand_protonation_confirmed"].isChecked())

    def test_changing_ligand_or_charge_revokes_chemistry_review_and_upload(self):
        widget = MDSetupTab()
        self.addCleanup(widget.deleteLater)
        widget.fields["force_field"].setCurrentText("CHARMM36m")
        for change in (lambda: widget.fields["ligand"].setText("changed.mol2"),
                       lambda: widget.fields["charge"].setValue(-1)):
            widget.fields["allow_ligand_upload"].setChecked(True)
            widget.fields["ligand_protonation_confirmed"].setChecked(True)
            change()
            self.assertFalse(widget.fields["allow_ligand_upload"].isChecked())
            self.assertFalse(widget.fields["ligand_protonation_confirmed"].isChecked())

    def test_minimum_time_values_stay_positive_and_invalid_cadence_is_explained(self):
        widget = MDSetupTab()
        self.addCleanup(widget.deleteLater)
        for key in ("timestep", "save_every", "pressure"):
            widget.fields[key].setValue(0.001)
            self.assertEqual(widget.fields[key].value(), 0.001)
        widget.fields["timestep"].setValue(100.0)
        self.assertIn("Input error", widget.preview.toPlainText())
        widget.fields["project_dir"].setText("project")
        widget.fields["protein"].setText("protein.pdb")
        with mock.patch("tabs.md_setup_tab.QMessageBox.warning") as warning, \
             mock.patch("tabs.md_setup_tab.PreparationWorker") as factory:
            widget.generate_topology_preview()
        factory.assert_not_called()
        self.assertIn("one timestep", warning.call_args.args[2])

    def test_short_pipeline_preset_sets_all_md_stage_lengths(self):
        from utils.mdp_generator import generate_all_mdp
        widget = MDSetupTab()
        self.addCleanup(widget.deleteLater)
        widget.fields["force_field"].setCurrentText("CHARMM36m")
        widget.short_test_button.click()
        params = widget.md_parameters()
        self.assertEqual(params.force_field, "CHARMM36m")
        self.assertEqual(params.timestep_fs, 1.0)
        self.assertEqual(params.duration_ns, 0.002)
        self.assertEqual(params.nvt_steps, 2000)
        self.assertEqual(params.npt_steps, 2000)
        self.assertIn("nsteps                  = 2000", generate_all_mdp(params)["md.mdp"])

    def test_charmm_locks_the_supported_tip3p_water_model(self):
        widget = MDSetupTab()
        widget.fields["water_model"].setCurrentText("SPC/E")

        widget.fields["force_field"].setCurrentText("CHARMM36m")

        self.assertEqual(widget.fields["water_model"].currentText(), "TIP3P")
        self.assertFalse(widget.fields["water_model"].isEnabled())

        widget.fields["force_field"].setCurrentText("AMBER99SB-ILDN")

        self.assertTrue(widget.fields["water_model"].isEnabled())

    def test_starts_exactly_one_high_level_preparation_worker(self):
        settings = mock.Mock()
        settings.value.side_effect = lambda key, default=None: {
            "gromacs_binary": "custom-gmx",
            "conda_environment": "custom-env",
            "cores": 8,
            "gpu_mode": "CPU only",
        }.get(key, default)
        widget = MDSetupTab(settings=settings)
        widget.fields["project_dir"].setText("project")
        widget.fields["protein"].setText("protein.pdb")
        widget.fields["ligand"].setText("ligand.sdf")
        worker = mock.Mock()

        with mock.patch(
            "tabs.md_setup_tab.PreparationWorker", return_value=worker
        ) as worker_type:
            widget.generate_topology_preview()

        worker_type.assert_called_once()
        request = worker_type.call_args.args[0]
        self.assertEqual(request.ligand_path, "ligand.sdf")
        self.assertEqual(request.gromacs_binary, "custom-gmx")
        self.assertEqual(request.conda_environment, "custom-env")
        worker.log.connect.assert_called_once()
        worker.done.connect.assert_called_once()
        worker.start.assert_called_once()
        self.assertFalse(hasattr(widget, "_lig_worker"))

    def test_successful_preparation_emits_project_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            widget = MDSetupTab()
            self.addCleanup(widget.deleteLater)
            widget.fields["project_dir"].setText(directory)
            widget.fields["protein"].setText("protein.pdb")
            worker = mock.Mock()
            prepared: list[tuple[str, str]] = []
            widget.project_prepared.connect(
                lambda project, manifest: prepared.append((project, manifest))
            )

            with mock.patch(
                "tabs.md_setup_tab.PreparationWorker",
                return_value=worker,
            ):
                widget.generate_topology_preview()
                completion = worker.done.connect.call_args.args[0]
                completion(True, "System preparation complete.")

            project = Path(directory).resolve()
            self.assertEqual(
                prepared,
                [(str(project), str(project / "moldynstudio_run.json"))],
            )

    def test_failed_preparation_does_not_emit_project_handoff(self):
        widget = MDSetupTab()
        self.addCleanup(widget.deleteLater)
        widget.fields["project_dir"].setText("project")
        widget.fields["protein"].setText("protein.pdb")
        worker = mock.Mock()
        prepared: list[tuple[str, str]] = []
        widget.project_prepared.connect(
            lambda project, manifest: prepared.append((project, manifest))
        )

        with mock.patch(
            "tabs.md_setup_tab.PreparationWorker",
            return_value=worker,
        ):
            widget.generate_topology_preview()
            completion = worker.done.connect.call_args.args[0]
            completion(False, "preparation failed")

        self.assertEqual(prepared, [])

    def test_sequential_preparations_emit_their_own_project_directories(self):
        with (
            tempfile.TemporaryDirectory() as first_directory,
            tempfile.TemporaryDirectory() as second_directory,
        ):
            widget = MDSetupTab()
            self.addCleanup(widget.deleteLater)
            widget.fields["protein"].setText("protein.pdb")
            first_worker = mock.Mock()
            second_worker = mock.Mock()
            prepared: list[tuple[str, str]] = []
            widget.project_prepared.connect(
                lambda project, manifest: prepared.append((project, manifest))
            )

            with mock.patch(
                "tabs.md_setup_tab.PreparationWorker",
                side_effect=[first_worker, second_worker],
            ):
                widget.fields["project_dir"].setText(first_directory)
                widget.generate_topology_preview()
                first_completion = first_worker.done.connect.call_args.args[0]
                first_completion(True, "first complete")
                first_worker.isRunning.return_value = False
                first_worker.finished.connect.call_args.args[0]()

                widget.fields["project_dir"].setText(second_directory)
                widget.generate_topology_preview()
                second_completion = second_worker.done.connect.call_args.args[0]

                second_completion(True, "second complete")

            first_project = Path(first_directory).resolve()
            second_project = Path(second_directory).resolve()
            self.assertEqual(
                prepared,
                [
                    (
                        str(first_project),
                        str(first_project / "moldynstudio_run.json"),
                    ),
                    (
                        str(second_project),
                        str(second_project / "moldynstudio_run.json"),
                    ),
                ],
            )


if __name__ == "__main__":
    unittest.main()
