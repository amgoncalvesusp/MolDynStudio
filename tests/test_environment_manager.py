from __future__ import annotations

import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

from core import environment_manager
from core.environment_manager import (
    DependencyChecker,
    build_conda_install_command,
    package_import_name,
)
from core.environment_manager import REQUIRED_PACKAGES
from core.gromacs_runner import CommandSpec, GromacsCommandBuilder


class EnvironmentManagerTests(unittest.TestCase):
    def test_known_import_name_mapping(self):
        self.assertEqual(package_import_name("mdanalysis"), "MDAnalysis")
        self.assertEqual(package_import_name("numpy"), "numpy")

    def test_build_conda_command_groups_conda_specs(self):
        command = build_conda_install_command(["conda-forge::gromacs"], "moldynstudio")
        self.assertEqual(command[:6], ["conda", "install", "-n", "moldynstudio", "-c", "conda-forge"])
        self.assertIn("gromacs", command)

    def test_scientific_environment_excludes_gui_qt_packages(self):
        self.assertNotIn("pyqt5", REQUIRED_PACKAGES)
        self.assertNotIn("pytraj", REQUIRED_PACKAGES)
        self.assertNotIn("nglview", REQUIRED_PACKAGES)
        self.assertIn("acpype", REQUIRED_PACKAGES)

    def test_scientific_environment_keeps_ambertools_and_mdtraj_compatible(self):
        environment_file = Path(__file__).resolve().parents[1] / "environment.yml"
        contents = environment_file.read_text(encoding="utf-8")

        self.assertIn("ambertools=23", contents)
        self.assertIn("mdtraj<1.11", contents)
        self.assertIn("numpy>=1.26,<2", contents)
        self.assertEqual(REQUIRED_PACKAGES["mdtraj"], "conda-forge::mdtraj<1.11")
        self.assertEqual(
            REQUIRED_PACKAGES["numpy"],
            "conda-forge::numpy>=1.26,<2",
        )

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

    def test_dependency_check_runs_inside_scientific_environment(self):
        completed = CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with mock.patch.object(
            environment_manager.wsl_bridge,
            "run",
            return_value=completed,
        ) as run:
            installed = DependencyChecker().is_installed("mdanalysis")

        self.assertTrue(installed)
        self.assertEqual(run.call_args.kwargs["env_name"], "moldynstudio")
        self.assertIn("MDAnalysis", run.call_args.args[0][-1])

    def test_install_missing_uses_bridge_without_shell(self):
        completed = CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(
                environment_manager.wsl_bridge,
                "run_raw",
                return_value=completed,
            ) as run_raw,
            mock.patch.object(
                environment_manager.wsl_bridge,
                "run",
                return_value=completed,
            ) as run,
        ):
            result = DependencyChecker().install_missing(
                ["conda-forge::numpy", "pip::py3dmol"]
            )

        self.assertEqual(result, 0)
        self.assertEqual(run_raw.call_args.args[0][0:2], ["conda", "install"])
        self.assertEqual(run.call_args.args[0][0:4], ["python", "-m", "pip", "install"])

    def test_dependency_check_emits_missing_packages_and_install_preview(self):
        checker = DependencyChecker(auto_install=True)
        statuses: list[tuple[str, str]] = []
        previews: list[str] = []
        completed: list[bool] = []

        checker.status_update.connect(
            lambda package, status: statuses.append((package, status))
        )
        checker.command_preview.connect(previews.append)
        checker.all_done.connect(completed.append)

        with (
            mock.patch.object(checker, "is_installed", return_value=False),
            mock.patch.object(checker, "install_missing", return_value=0) as install,
        ):
            checker.run()

        self.assertEqual(len(statuses), len(REQUIRED_PACKAGES))
        self.assertTrue(all(status == "MISSING" for _, status in statuses))
        self.assertEqual(len(previews), 1)
        self.assertEqual(completed, [False])
        install.assert_called_once()


if __name__ == "__main__":
    unittest.main()
