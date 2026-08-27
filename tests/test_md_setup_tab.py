from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from tabs.md_setup_tab import ForceFieldImportWorker, MDSetupTab


class MDSetupTabTests(unittest.TestCase):
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

    def test_blocks_automatic_ligand_parameterization_with_charmm(self):
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
        self.assertIn("CGenFF", warning.call_args.args[2])
        self.assertIn("AMBER", warning.call_args.args[2])

    def test_disables_ligand_charge_controls_for_charmm(self):
        widget = MDSetupTab()
        widget.fields["ligand"].setText("ligand.sdf")
        widget.fields["force_field"].setCurrentText("CHARMM36m")

        self.assertFalse(widget.fields["charge_method"].isEnabled())
        self.assertFalse(widget.fields["charge"].isEnabled())
        self.assertIn("Blocked", widget.ligand_compatibility.text())

        widget.fields["force_field"].setCurrentText("AMBER14SB")

        self.assertTrue(widget.fields["charge_method"].isEnabled())
        self.assertTrue(widget.fields["charge"].isEnabled())
        self.assertIn("Ready", widget.ligand_compatibility.text())

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


if __name__ == "__main__":
    unittest.main()
