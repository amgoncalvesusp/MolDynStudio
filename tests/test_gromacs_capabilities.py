from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest import mock

from core import gromacs_capabilities
from core.gromacs_capabilities import (
    GromacsCapabilities,
    GromacsCapabilityError,
    build_mdrun_resource_args,
    probe_gromacs,
    resolve_gromacs_binary,
)
from core.settings import SettingsStore
from setup_wizard import SetupWizard


VERSION_OUTPUT = """GROMACS version:    2026.3
GPU support:      CUDA
GPU FFT library:  cuFFT
"""

MDRUN_HELP = """
 -nt       <int>     Total number of threads
 -nb       <enum>    Perform non-bonded calculations on cpu or gpu
 -pme      <enum>    Perform PME calculations on cpu or gpu
 -bonded   <enum>    Perform bonded calculations on cpu or gpu
 -update   <enum>    Perform update and constraints on cpu or gpu
"""


def capabilities(
    *, gpu_support_text: str | None = "CUDA", help_text: str = MDRUN_HELP
) -> GromacsCapabilities:
    return GromacsCapabilities(
        executable="gmx",
        version_text=VERSION_OUTPUT,
        version="2026.3",
        executable_ok=True,
        gpu_support_text=gpu_support_text,
        hardware_notes=(),
        mdrun_help_text=help_text,
    )


class GromacsBinaryResolutionTests(unittest.TestCase):
    def test_auto_resolves_to_gmx_in_selected_environment(self):
        self.assertEqual(resolve_gromacs_binary("auto"), "gmx")
        self.assertEqual(resolve_gromacs_binary("  "), "gmx")

    def test_valid_explicit_linux_path_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "gmx-custom"
            executable.touch()
            executable.chmod(0o755)

            with mock.patch.object(
                gromacs_capabilities.wsl_bridge, "IS_WINDOWS", False
            ):
                self.assertEqual(
                    resolve_gromacs_binary(str(executable)), str(executable)
                )

    def test_missing_explicit_linux_path_is_rejected(self):
        missing = str(Path(tempfile.gettempdir()) / "missing-gmx-for-moldynstudio")
        with (
            mock.patch.object(gromacs_capabilities.wsl_bridge, "IS_WINDOWS", False),
            self.assertRaises(FileNotFoundError),
        ):
            resolve_gromacs_binary(missing)

    def test_wsl_accepts_linux_path_for_validation_during_probe(self):
        with mock.patch.object(gromacs_capabilities.wsl_bridge, "IS_WINDOWS", True):
            self.assertEqual(
                resolve_gromacs_binary("/opt/gromacs/bin/gmx"),
                "/opt/gromacs/bin/gmx",
            )

    def test_wsl_translates_non_exe_windows_path(self):
        with (
            mock.patch.object(gromacs_capabilities.wsl_bridge, "IS_WINDOWS", True),
            mock.patch.object(
                gromacs_capabilities.wsl_bridge,
                "win_to_wsl",
                return_value="/mnt/c/tools/gmx",
            ) as translate,
        ):
            result = resolve_gromacs_binary(r"C:\tools\gmx")

        self.assertEqual(result, "/mnt/c/tools/gmx")
        translate.assert_called_once_with(r"C:\tools\gmx")

    def test_wsl_rejects_windows_executable(self):
        with mock.patch.object(gromacs_capabilities.wsl_bridge, "IS_WINDOWS", True):
            with self.assertRaises(ValueError):
                resolve_gromacs_binary(r"C:\Program Files\GROMACS\gmx.exe")


class GromacsProbeTests(unittest.TestCase):
    def test_probe_uses_selected_binary_and_environment_for_both_commands(self):
        results = [
            CompletedProcess([], 0, VERSION_OUTPUT, ""),
            CompletedProcess([], 0, MDRUN_HELP, ""),
        ]
        with mock.patch.object(
            gromacs_capabilities.wsl_bridge, "run", side_effect=results
        ) as run:
            result = probe_gromacs("/opt/gromacs/bin/gmx", "research")

        self.assertTrue(result.executable_ok)
        self.assertEqual(result.executable, "/opt/gromacs/bin/gmx")
        self.assertEqual(result.version, "2026.3")
        self.assertEqual(result.gpu_support_text, "CUDA")
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["/opt/gromacs/bin/gmx", "--version"],
                ["/opt/gromacs/bin/gmx", "mdrun", "-h"],
            ],
        )
        self.assertTrue(
            all(call.kwargs["env_name"] == "research" for call in run.call_args_list)
        )

    def test_probe_reports_non_callable_binary_without_claiming_gpu(self):
        failed = CompletedProcess([], 127, "", "not found")
        with mock.patch.object(gromacs_capabilities.wsl_bridge, "run", return_value=failed):
            result = probe_gromacs("gmx", "moldynstudio")

        self.assertFalse(result.executable_ok)
        self.assertIsNone(result.version)
        self.assertIsNone(result.gpu_support_text)
        self.assertIn("not found", result.version_text)

    def test_disabled_backend_is_not_treated_as_usable_gpu_support(self):
        version = "GROMACS version: 2026.3\nGPU support: disabled\n"
        results = [
            CompletedProcess([], 0, version, ""),
            CompletedProcess([], 0, MDRUN_HELP, ""),
        ]
        with mock.patch.object(
            gromacs_capabilities.wsl_bridge, "run", side_effect=results
        ):
            result = probe_gromacs("gmx", "moldynstudio")

        self.assertTrue(result.executable_ok)
        self.assertIsNone(result.gpu_support_text)
        self.assertIn("GPU support: disabled", result.hardware_notes)


class ResourceArgumentTests(unittest.TestCase):
    def test_auto_uses_safe_threads_without_gpu_flags(self):
        with mock.patch.object(gromacs_capabilities.os, "cpu_count", return_value=8):
            args = build_mdrun_resource_args("Auto", 99, capabilities())

        self.assertEqual(args, ("-nt", "8"))
        self.assertNotIn("-nb", args)

    def test_cpu_only_forces_verified_cpu_backends(self):
        args = build_mdrun_resource_args("CPU only", 4, capabilities())
        self.assertEqual(
            args,
            (
                "-nt",
                "4",
                "-nb",
                "cpu",
                "-pme",
                "cpu",
                "-bonded",
                "cpu",
                "-update",
                "cpu",
            ),
        )

    def test_cpu_only_rejects_unverified_flags(self):
        with self.assertRaises(GromacsCapabilityError):
            build_mdrun_resource_args(
                "CPU only", 4, capabilities(help_text="-nt <int>\n-nb <enum>\n")
            )

    def test_gpu_if_supported_forces_only_nonbonded_offload(self):
        args = build_mdrun_resource_args("GPU if supported", 4, capabilities())
        self.assertEqual(args, ("-nt", "4", "-nb", "gpu"))

    def test_explicit_gpu_mode_does_not_silently_fall_back(self):
        with self.assertRaisesRegex(
            GromacsCapabilityError, "does not report a usable GPU backend"
        ):
            build_mdrun_resource_args(
                "GPU if supported", 4, capabilities(gpu_support_text=None)
            )

    def test_thread_count_never_falls_below_one(self):
        args = build_mdrun_resource_args("Auto", 0, capabilities())
        self.assertEqual(args, ("-nt", "1"))


class SettingsMigrationTests(unittest.TestCase):
    def test_legacy_gpu_modes_are_migrated_when_loaded(self):
        store = SettingsStore()
        store._settings = None

        store.set_value("gpu_mode", "CUDA")
        self.assertEqual(store.value("gpu_mode"), "GPU if supported")

        store.set_value("gpu_mode", "OpenCL")
        self.assertEqual(store.value("gpu_mode"), "GPU if supported")


class SetupWizardCapabilityStatusTests(unittest.TestCase):
    def test_callable_cpu_build_is_green_without_claiming_verified_gpu(self):
        class FakeSettings:
            values = {
                "gromacs_binary": "/opt/gromacs/bin/gmx",
                "conda_environment": "research",
            }

            def value(self, key: str, default: str) -> str:
                return self.values.get(key, default)

        class FakeRow:
            def __init__(self) -> None:
                self.ok_message: str | None = None
                self.missing_message: str | None = None

            def set_ok(self, message: str) -> None:
                self.ok_message = message

            def set_missing(self, message: str, *_args) -> None:
                self.missing_message = message

            def set_busy(self, _message: str) -> None:
                raise AssertionError("environment is already available")

        wizard = mock.Mock()
        wizard._env_ok_cached = True
        wizard.settings = FakeSettings()
        wizard.row_gmx = FakeRow()
        wizard._create_env = mock.Mock()
        cpu_capabilities = capabilities(gpu_support_text=None)
        cpu_capabilities = GromacsCapabilities(
            **{
                **cpu_capabilities.__dict__,
                "executable": "/opt/gromacs/bin/gmx",
            }
        )

        with (
            mock.patch(
                "setup_wizard.resolve_gromacs_binary",
                return_value="/opt/gromacs/bin/gmx",
            ),
            mock.patch(
                "setup_wizard.probe_gromacs", return_value=cpu_capabilities
            ) as probe,
        ):
            SetupWizard._refresh_gmx(wizard)

        self.assertIsNone(wizard.row_gmx.missing_message)
        self.assertIn("/opt/gromacs/bin/gmx", wizard.row_gmx.ok_message)
        self.assertIn("GROMACS 2026.3", wizard.row_gmx.ok_message)
        self.assertIn("GPU support: not reported", wizard.row_gmx.ok_message)
        probe.assert_called_once_with("/opt/gromacs/bin/gmx", "research")


if __name__ == "__main__":
    unittest.main()
