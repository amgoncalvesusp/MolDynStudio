"""OpenMM wheels keep dynamically loaded plugins outside their Python package."""
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest import mock


class OpenMMPackagingTests(unittest.TestCase):
    def _hook(self):
        hook = Path(__file__).resolve().parents[1] / "build" / "hooks" / "hook-openmm.py"
        with mock.patch("PyInstaller.utils.hooks.get_package_paths", return_value=("missing", "missing/openmm")):
            return runpy.run_path(str(hook))

    def test_newer_complete_msvc_family_replaces_older_qt_runtime(self):
        hook = self._hook()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qt, current = root / "qt", root / "current"
            for directory in (qt, current):
                directory.mkdir()
                for name in hook["_MSVC_DLLS"]:
                    (directory / name).write_bytes(b"fixture")
            selected = hook["_select_msvc_runtime"](
                (qt, current),
                lambda path: (14, 50, 35719, 0) if path.parent == current else (14, 26, 28720, 3),
            )
            self.assertEqual(selected, current)

    def test_runtime_selection_does_not_downgrade_newer_wheel(self):
        hook = self._hook()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            qt, system = root / "qt", root / "system"
            for directory in (qt, system):
                directory.mkdir()
                for name in hook["_MSVC_DLLS"]:
                    (directory / name).write_bytes(b"fixture")
            selected = hook["_select_msvc_runtime"](
                (qt, system),
                lambda path: (14, 51, 0, 0) if path.parent == qt else (14, 50, 0, 0),
            )
            self.assertEqual(selected, qt)

    def test_missing_or_pre_mutex_runtime_fails_with_actionable_message(self):
        hook = self._hook()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in hook["_MSVC_DLLS"]:
                (root / name).write_bytes(b"fixture")
            with self.assertRaisesRegex(RuntimeError, "MOLDYN_MSVC_RUNTIME_DIR"):
                hook["_select_msvc_runtime"]((root,), lambda path: (14, 26, 0, 0))
            (root / "msvcp140_2.dll").unlink()
            with self.assertRaisesRegex(RuntimeError, "complete"):
                hook["_select_msvc_runtime"]((root,), lambda path: (14, 50, 0, 0))

    def test_mixed_family_cannot_silently_downgrade_newer_companion_dll(self):
        hook = self._hook()
        with tempfile.TemporaryDirectory() as tmp:
            qt, system = Path(tmp) / "qt", Path(tmp) / "system"
            for directory in (qt, system):
                directory.mkdir()
                for name in hook["_MSVC_DLLS"]:
                    (directory / name).write_bytes(b"fixture")
            def version(path):
                if path.parent == system:
                    return (14, 50, 0, 0)
                return (14, 51 if path.name == "msvcp140.dll" else 44, 0, 0)
            with self.assertRaisesRegex(RuntimeError, "downgrad"):
                hook["_select_msvc_runtime"]((qt, system), version)

    def test_hook_keeps_library_and_plugin_destination_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "openmm"
            package.mkdir()
            runtime = root / "OpenMM.libs" / "lib"
            plugins = runtime / "plugins"
            plugins.mkdir(parents=True)
            for name in ("OpenMM.dll", "OpenMM.lib"):
                (runtime / name).write_bytes(b"fixture")
            (plugins / "OpenMMCPU.dll").write_bytes(b"fixture")
            hook = Path(__file__).resolve().parents[1] / "build" / "hooks" / "hook-openmm.py"
            with mock.patch("PyInstaller.utils.hooks.get_package_paths", return_value=(str(root), str(package))):
                result = runpy.run_path(str(hook))
            collected = {(Path(source).name, destination) for source, destination in result["binaries"]}
            self.assertEqual(collected, {
                ("OpenMM.dll", "OpenMM.libs/lib"),
                ("OpenMMCPU.dll", "OpenMM.libs/lib/plugins"),
            })
