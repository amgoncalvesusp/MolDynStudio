from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

from build import collect_licenses


class CollectLicensesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        notice = self.root / "assets/licenses/THIRD_PARTY_NOTICES.md"
        notice.parent.mkdir(parents=True)
        notice.write_text("Application notice\n", encoding="utf-8")
        (self.root / "requirements.txt").write_text("# Runtime\nRoot_Pkg>=1\n", encoding="utf-8")
        (self.root / "LICENSE.txt").write_bytes(b"Python license\r\n")
        (self.root / "LICENSE").write_bytes(b"Application GPLv3\n")
        self.output = self.root / "output"
        self.distributions = {
            "root-pkg": self.distribution("root-pkg"),
            "pyinstaller": self.distribution("pyinstaller", ["build-only"]),
        }
        resolver = mock.patch.object(
            collect_licenses.importlib.metadata,
            "distribution",
            side_effect=self.distributions.__getitem__,
        )
        self.resolve = resolver.start()
        self.addCleanup(resolver.stop)
        prefix = mock.patch.object(collect_licenses.sys, "base_prefix", str(self.root))
        prefix.start()
        self.addCleanup(prefix.stop)

    def distribution(self, name, requirements=()):
        directory = self.root / "installed" / name
        directory.mkdir(parents=True)
        files = ("licenses/LICENSE.txt", "METADATA", "module.py")
        for filename in files:
            path = directory / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"Copyright upstream\r\nExact text: \xc2\xa9\n")
        return mock.Mock(
            version="1.2.3",
            requires=list(requirements),
            files=[PurePosixPath(filename) for filename in files],
            locate_file=lambda entry: directory / entry,
        )

    def collect(self):
        collect_licenses.collect_runtime_licenses(self.root, self.output)
        return json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))

    def test_traverses_runtime_dependencies_once_but_not_build_dependencies(self):
        self.distributions["root-pkg"].requires = ["child>=1", "CHILD"]
        self.distributions["child"] = self.distribution("child", ["Root_Pkg"])

        manifest = self.collect()

        self.assertEqual([item["name"] for item in manifest], ["child", "pyinstaller", "root-pkg"])
        self.assertCountEqual(self.resolve.call_args_list, [mock.call(name) for name in self.distributions])
        self.assertTrue(all(item["version"] == "1.2.3" for item in manifest))

    def test_preserves_license_and_metadata_bytes_without_copying_code(self):
        manifest = self.collect()

        package = self.output / "root-pkg-1.2.3"
        original = self.root / "installed/root-pkg"
        for filename in ("licenses/LICENSE.txt", "METADATA"):
            self.assertEqual((package / filename).read_bytes(), (original / filename).read_bytes())
        self.assertFalse((package / "module.py").exists())
        self.assertEqual((self.output / "PYTHON-LICENSE.txt").read_bytes(), b"Python license\r\n")
        self.assertEqual((self.output / "MOLDYNSTUDIO-LICENSE.txt").read_bytes(), b"Application GPLv3\n")
        self.assertEqual(
            next(item for item in manifest if item["name"] == "root-pkg")["license_files"],
            ["licenses/LICENSE.txt"],
        )

    def test_filters_inactive_platform_and_optional_extra_dependencies(self):
        self.distributions["root-pkg"].requires = [
            'active; python_version >= "3.0"',
            'inactive; python_version < "0.0"',
            'optional; extra == "test"',
        ]
        self.distributions["active"] = self.distribution("active")

        self.collect()

        self.assertCountEqual(self.resolve.call_args_list, [mock.call(name) for name in self.distributions])

    def test_missing_python_license_fails_without_success_manifest(self):
        (self.root / "LICENSE.txt").unlink()

        with self.assertRaisesRegex(FileNotFoundError, "Python runtime license missing"):
            self.collect()

        self.assertFalse((self.output / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
