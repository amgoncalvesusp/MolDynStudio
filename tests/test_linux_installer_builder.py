from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from build import create_linux_installer


class LinuxInstallerBuilderTests(unittest.TestCase):
    def run_shell(self, script, **variables):
        bash = ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt"
                else shutil.which("bash"))
        if not bash or not Path(bash).is_file():
            self.skipTest("Bash is required for installer execution tests")
        variables = {
            key: (f"/{value[0].lower()}{value[2:]}"
                  if os.name == "nt" and value[1:3] == ":/" else value)
            for key, value in variables.items()
        }
        return subprocess.run(
            [bash], input="set -euo pipefail\n" + script,
            env={**os.environ, **variables}, capture_output=True, text=True,
        )

    def test_install_preserves_forcefields_and_rejects_other_unmanaged_content(self):
        stub = create_linux_installer.INSTALLER_STUB
        section = stub[stub.index('if [ -e "$INSTALL_DIR" ]; then'):
                       stub.index('if ! "$PYTHON_BIN" -m venv')]
        for marked, unrelated, expected in ((True, False, 0), (False, False, 0),
                                             (False, True, 1)):
            with self.subTest(marked=marked, unrelated=unrelated), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                install = root / "installed app"
                cache = install / "forcefields" / "imported.ff"
                cache.mkdir(parents=True)
                sentinel = cache / "forcefield.itp"
                sentinel.write_text("user forcefield", encoding="utf-8")
                if marked:
                    (install / ".moldynstudio-install").touch()
                if unrelated:
                    (install / "unrelated.txt").touch()
                payload_root = root / "payload"
                payload_root.mkdir()
                main = payload_root / "main.py"
                main.write_text("# new version\n", encoding="utf-8")
                (root / "payload.tar.gz").write_bytes(
                    create_linux_installer.build_payload([main], payload_root))
                result = self.run_shell(section, INSTALL_DIR=install.as_posix(),
                                        tmp_dir=root.as_posix())
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertEqual(sentinel.read_text(), "user forcefield")
                self.assertEqual((install / "main.py").exists(), expected == 0)

    def test_desktop_exec_quotes_spaces_and_reserved_characters(self):
        stub = create_linux_installer.INSTALLER_STUB
        section = stub[stub.index('desktop_exec='):stub.index("if command -v update-desktop-database")]
        with tempfile.TemporaryDirectory() as tmp:
            desktop = Path(tmp) / "app.desktop"
            result = self.run_shell(
                section, PYTHON_BIN=sys.executable.replace("\\", "/"),
                LAUNCHER='/home/Ana Silva/100%/a\\b"$`/moldynstudio',
                INSTALL_DIR="/home/Ana Silva/app", DESKTOP_FILE=desktop.as_posix())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                r'Exec="/home/Ana Silva/100%%/a\\\\b\\"\\$\\`/moldynstudio"',
                desktop.read_text())

    def test_payload_file_selection_excludes_build_outputs_and_caches(self):
        files = create_linux_installer.iter_payload_files()
        rels = {
            path.relative_to(create_linux_installer.REPO_ROOT).as_posix()
            for path in files
        }

        self.assertIn("main.py", rels)
        self.assertIn("requirements.txt", rels)
        self.assertIn("assets/logo_512.png", rels)
        self.assertIn("assets/forcefields/README.md", rels)
        self.assertFalse(any(rel.endswith((".tgz", ".tar.gz")) for rel in rels))
        self.assertNotIn("dist/MolDynStudio.exe", rels)
        self.assertFalse(any("__pycache__" in rel for rel in rels))
        self.assertFalse(any(rel.startswith("build/") for rel in rels))

    def test_payload_is_valid_tarball_with_linux_launcher_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launcher = root / "launch_analysis_studio.sh"
            launcher.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            payload = create_linux_installer.build_payload([launcher], root)
            archive = root / "payload.tar.gz"
            archive.write_bytes(payload)

            with tarfile.open(archive, mode="r:gz") as tar:
                info = tar.getmember("launch_analysis_studio.sh")

        self.assertTrue(info.mode & 0o111)

    def test_create_installer_writes_executable_self_extracting_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "MolDynStudio.run"

            result = create_linux_installer.create_installer(output)

            self.assertEqual(result, output)
            self.assertTrue(output.read_bytes().startswith(b"#!/usr/bin/env bash\n"))
            self.assertIn(
                b"__MOLDYNSTUDIO_PAYLOAD_BELOW__\n",
                output.read_bytes(),
            )
            if os.name != "nt":
                self.assertTrue(output.stat().st_mode & 0o111)

    def test_installer_refuses_to_overwrite_unmanaged_directory(self):
        stub = create_linux_installer.INSTALLER_STUB

        self.assertIn(".moldynstudio-install", stub)
        self.assertIn("was not created by this installer", stub)

    def test_installer_rejects_dangerous_install_directories_before_extraction(self):
        stub = create_linux_installer.INSTALLER_STUB
        guard = 'case "$INSTALL_DIR" in'
        dangerous_patterns = '"/"|"$HOME")'
        extraction = 'tar -xzf "$tmp_dir/payload.tar.gz" -C "$INSTALL_DIR"'

        self.assertIn(guard, stub)
        self.assertIn(dangerous_patterns, stub)
        self.assertIn('"$HOME"/*)', stub)
        self.assertIn("must be inside the current user's home", stub)
        self.assertLess(stub.index(guard), stub.index(extraction))

    def test_installer_launcher_defaults_to_xcb_without_overriding_user_choice(self):
        stub = create_linux_installer.INSTALLER_STUB

        self.assertIn('QT_QPA_PLATFORM="\\${QT_QPA_PLATFORM:-xcb}"', stub)
        self.assertIn("sudo apt install -y $UBUNTU_QT_DEPS", stub)

    def test_source_launcher_bootstraps_a_local_virtualenv(self):
        launcher = (create_linux_installer.REPO_ROOT / "launch_analysis_studio.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('PYTHON_BIN="${PYTHON:-python3}"', launcher)
        self.assertIn('VENV_DIR="${MOLDYNSTUDIO_VENV_DIR:-$SCRIPT_DIR/.venv}"', launcher)
        self.assertIn('exec "$VENV_DIR/bin/python" main.py', launcher)


if __name__ == "__main__":
    unittest.main()
