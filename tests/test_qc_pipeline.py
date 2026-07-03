from __future__ import annotations

import unittest
from unittest import mock

from analysis.qc_pipeline import (
    QCPipelineInputs,
    build_qc_tasks,
    default_qc_output_dir,
    expected_output_files,
)
from core import wsl_bridge


class QCPipelineTests(unittest.TestCase):
    def test_builds_structural_qc_tasks_with_wsl_paths(self):
        inputs = QCPipelineInputs(
            topology=r"C:\work\topol.tpr",
            trajectory=r"C:\work\traj.xtc",
            output_dir=r"C:\work\qc",
        )

        with mock.patch.object(wsl_bridge, "IS_WINDOWS", True):
            tasks = build_qc_tasks(inputs)

        self.assertEqual([task.name for task in tasks], ["RMSD", "Radius of gyration", "SASA", "RMSF"])
        rmsd_command = " ".join(tasks[0].command)
        self.assertIn("/mnt/c/work/topol.tpr", rmsd_command)
        self.assertIn("/mnt/c/work/traj.xtc", rmsd_command)
        self.assertIn("rmsd.xvg", rmsd_command)
        rmsf_command = " ".join(tasks[3].command)
        self.assertIn("rmsf", rmsf_command)
        self.assertIn("-res", rmsf_command)
        self.assertIn("rmsf.xvg", rmsf_command)

    def test_energy_file_adds_energy_qc_tasks(self):
        inputs = QCPipelineInputs(
            topology="topol.tpr",
            trajectory="traj.xtc",
            energy="ener.edr",
            output_dir="qc",
        )

        tasks = build_qc_tasks(inputs)

        self.assertIn("Temperature", [task.name for task in tasks])
        self.assertIn("temperature.xvg", [task.output_file for task in tasks])
        self.assertIn("Total energy", [task.name for task in tasks])

    def test_expected_output_files_use_output_directory(self):
        inputs = QCPipelineInputs(
            topology="topol.tpr",
            trajectory="traj.xtc",
            energy="ener.edr",
            output_dir=r"C:\work\qc",
        )

        outputs = expected_output_files(inputs)

        self.assertIn(r"C:\work\qc\rmsd.xvg", outputs)
        self.assertIn(r"C:\work\qc\rmsf.xvg", outputs)
        self.assertIn(r"C:\work\qc\temperature.xvg", outputs)

    def test_default_output_dir_sits_next_to_trajectory(self):
        self.assertEqual(default_qc_output_dir(r"C:\run\prod.xtc"), r"C:\run\qc_xvg")


if __name__ == "__main__":
    unittest.main()
