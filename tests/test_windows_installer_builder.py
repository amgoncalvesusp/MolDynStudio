from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest import mock

from build import create_installer


class WindowsInstallerBuilderTests(unittest.TestCase):
    def test_spec_uses_pyinstaller_namespace_without_dunder_file(self):
        root = Path(__file__).resolve().parents[1]
        analysis = mock.Mock(pure=[], scripts=[], binaries=[], datas=[])
        namespace = {
            "SPECPATH": str(root),
            "Analysis": mock.Mock(return_value=analysis),
            "PYZ": mock.Mock(),
            "EXE": mock.Mock(),
        }
        spec = root / "MolDynStudio.spec"
        with (
            mock.patch("PyInstaller.utils.hooks.collect_all", return_value=([], [], [])) as collect,
            mock.patch("PyInstaller.utils.hooks.collect_submodules", return_value=[]),
        ):
            exec(compile(spec.read_bytes(), str(spec), "exec"), namespace)
        self.assertEqual(namespace["Analysis"].call_args.args[0], [str(root / "main.py")])
        self.assertEqual(namespace["Analysis"].call_args.kwargs["hookspath"],
                         [str(root / "build" / "hooks")])
        self.assertIn(mock.call("parmed"), collect.call_args_list)

    def test_build_collects_charmm_converter_and_exports_runtime_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir()
            for filename in ("environment.yml", "install_wsl.ps1", "main.py"):
                (root / filename).write_text("fixture")
            def fake_build(command, **kwargs):
                packages = [command[index + 1] for index, value in enumerate(command) if value == "--collect-all"]
                self.assertIn("parmed", packages)
                manifest = work / "MolDynStudio" / "openmm-msvc-runtime.json"
                manifest.parent.mkdir()
                manifest.write_text("runtime provenance")
                return 0
            with (
                mock.patch.object(create_installer, "REPO_ROOT", root),
                mock.patch.object(create_installer, "ENTRY", root / "main.py"),
                mock.patch.object(create_installer, "ensure_pyinstaller"),
                mock.patch.object(create_installer.tempfile, "mkdtemp", return_value=str(work)),
                mock.patch.object(create_installer, "collect_runtime_licenses", side_effect=lambda _, path: path.mkdir()),
                mock.patch.object(create_installer.subprocess, "call", side_effect=fake_build),
            ):
                self.assertEqual(create_installer.build(), 0)
            self.assertEqual((root / "dist/licenses/openmm-msvc-runtime.json").read_text(), "runtime provenance")

    def test_ensure_pyinstaller_uses_pinned_build_requirements_when_missing(self):
        missing = mock.Mock(returncode=1)

        with (
            mock.patch.object(create_installer.subprocess, "run", return_value=missing),
            mock.patch.object(create_installer.subprocess, "check_call") as check_call,
        ):
            create_installer.ensure_pyinstaller()

        check_call.assert_called_once_with(
            [
                create_installer.sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(create_installer.BUILD_REQUIREMENTS),
            ]
        )

    def test_ensure_pyinstaller_skips_install_when_available(self):
        present = mock.Mock(returncode=0)

        with (
            mock.patch.object(create_installer.subprocess, "run", return_value=present),
            mock.patch.object(create_installer.subprocess, "check_call") as check_call,
        ):
            create_installer.ensure_pyinstaller()

        check_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
