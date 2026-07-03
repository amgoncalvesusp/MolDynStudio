from __future__ import annotations

import unittest

from core.environment_manager import build_conda_install_command, package_import_name
from core.gromacs_runner import CommandSpec, GromacsCommandBuilder


class EnvironmentManagerTests(unittest.TestCase):
    def test_known_import_name_mapping(self):
        self.assertEqual(package_import_name("mdanalysis"), "MDAnalysis")
        self.assertEqual(package_import_name("pyqt5"), "PyQt5")
        self.assertEqual(package_import_name("numpy"), "numpy")

    def test_build_conda_command_groups_conda_specs(self):
        command = build_conda_install_command(["conda-forge::gromacs=2024.1"], "moldynstudio")
        self.assertEqual(command[:6], ["conda", "install", "-n", "moldynstudio", "-c", "conda-forge"])
        self.assertIn("gromacs=2024.1", command)

    def test_build_conda_command_includes_pip_specs(self):
        command = build_conda_install_command(["conda-forge::numpy", "pip::py3dmol"], "moldynstudio")
        self.assertIn("&&", command)
        self.assertIn("py3dmol", command)

    def test_build_conda_command_handles_pip_only_specs(self):
        command = build_conda_install_command(["pip::py3dmol"], "moldynstudio")
        self.assertEqual(command[:5], ["conda", "run", "-n", "moldynstudio", "pip"])
        self.assertIn("py3dmol", command)

    def test_command_spec_prefixes_conda(self):
        spec = CommandSpec("gmx", ("rms", "-s", "topol.tpr"), use_conda=True, conda_env="moldynstudio")
        self.assertEqual(spec.as_list()[:5], ["conda", "run", "-n", "moldynstudio", "gmx"])

    def test_gromacs_builder_keeps_subcommand_first(self):
        spec = GromacsCommandBuilder(conda_env="moldynstudio", executable="gmx").build("rmsd", ["-s", "topol.tpr"])
        self.assertIn("rmsd", spec.args)


if __name__ == "__main__":
    unittest.main()
