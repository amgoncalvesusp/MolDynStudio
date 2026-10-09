from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest import mock

from core.evidence_bundle import export_evidence_bundle
from core.evidence_bundle import _linked
from core.run_manifest import new_manifest, save_manifest


class EvidenceBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        manifest = new_manifest(self.project, "protein", "source.pdb", None,
                                "amber99sb-ildn", "tip3p", "gmx", "moldynstudio", 2, "CPU")
        save_manifest(manifest, self.project / "moldynstudio_run.json")

    def test_bundle_preserves_evidence_and_inventories_large_trajectory(self):
        (self.project / "md.mdp").write_text("dt = 0.002\n", encoding="utf-8")
        (self.project / "md.xtc").write_bytes(b"trajectory-bytes")
        (self.project / "secret.env").write_text("not project evidence", encoding="utf-8")
        result = export_evidence_bundle(self.project, self.root / "evidence.zip")
        with zipfile.ZipFile(result) as archive:
            self.assertIn("files/md.mdp", archive.namelist())
            self.assertNotIn("files/md.xtc", archive.namelist())
            self.assertNotIn("files/secret.env", archive.namelist())
            index = json.loads(archive.read("evidence_manifest.json"))
            files = {entry["path"]: entry for entry in index["files"]}
            self.assertEqual(files["md.xtc"]["sha256"], hashlib.sha256(b"trajectory-bytes").hexdigest())
            self.assertFalse(files["md.xtc"]["included"])
            self.assertEqual(index["stages"]["production"], "not_ready")
            self.assertIn("not proof of convergence", archive.read("README.md").decode())

    def test_rejects_missing_manifest_and_existing_destination(self):
        with self.assertRaisesRegex(ValueError, "manifest"):
            export_evidence_bundle(self.root, self.root / "empty.zip")
        target = self.root / "existing.zip"
        target.write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            export_evidence_bundle(self.project, target)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_failure_leaves_no_partial_zip_or_source_changes(self):
        original = (self.project / "moldynstudio_run.json").read_bytes()
        target = self.root / "failure.zip"
        with mock.patch("core.evidence_bundle.zipfile.ZipFile.writestr", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                export_evidence_bundle(self.project, target)
        self.assertFalse(target.exists())
        self.assertEqual((self.project / "moldynstudio_run.json").read_bytes(), original)
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_exports_nested_analysis_results_without_following_links(self):
        folder = self.project / "analysis" / "run_1"
        folder.mkdir(parents=True)
        (folder / "rmsd.csv").write_text("time_ns,rmsd_nm\n0,0\n", encoding="utf-8")
        outside = self.root / "outside.pdb"
        outside.write_text("private", encoding="utf-8")
        link = self.project / "external.pdb"
        try:
            link.symlink_to(outside)
        except OSError:
            link = None
        target = export_evidence_bundle(self.project, self.root / "nested.zip")
        with zipfile.ZipFile(target) as archive:
            self.assertIn("files/analysis/run_1/rmsd.csv", archive.namelist())
            self.assertNotIn("files/external.pdb", archive.namelist())

    def test_racing_destination_is_not_overwritten(self):
        target = self.root / "raced.zip"
        original_link = __import__("os").link

        def concurrent_create(source, destination):
            Path(destination).write_bytes(b"other export")
            original_link(source, destination)

        with mock.patch("core.evidence_bundle.os.link", side_effect=concurrent_create):
            with self.assertRaises(FileExistsError):
                export_evidence_bundle(self.project, target)
        self.assertEqual(target.read_bytes(), b"other export")

    def test_onedrive_cloud_reparse_is_not_a_path_redirect(self):
        path = mock.Mock()
        path.is_symlink.return_value = False
        path.lstat.return_value.st_reparse_tag = 0x9000001A
        self.assertFalse(_linked(path))
        path.lstat.return_value.st_reparse_tag = 0xA0000003
        self.assertTrue(_linked(path))

    def test_qc_and_docklens_provenance_are_included(self):
        names = ("qc_commands.json", "docklens_launch.json", "docklens_result_frame.json")
        for name in names:
            (self.project / name).write_text('{"status":"failed"}', encoding="utf-8")
        with zipfile.ZipFile(export_evidence_bundle(self.project, self.root / "records.zip")) as archive:
            for name in names:
                self.assertEqual(json.loads(archive.read(f"files/{name}"))["status"], "failed")

    def test_structure_repair_sources_and_decisions_are_preserved(self):
        folder = self.project / "structure_repairs" / "case"
        folder.mkdir(parents=True)
        files = {
            "source.fasta": ">deposited_sequence\nASG\n",
            "audit.json": '{"missing_heavy_atoms":[]}',
            "plan.json": '{"choices":{"fill_gap_ids":[]}}',
            "repair_report.json": '{"coordinate_repair_ready":true,"md_requires_parameters":true}',
        }
        for name, content in files.items():
            (folder / name).write_bytes(content.encode())
        with zipfile.ZipFile(export_evidence_bundle(self.project, self.root / "repair.zip")) as archive:
            index = json.loads(archive.read("evidence_manifest.json"))
            records = {item["path"]: item for item in index["files"]}
            for name, content in files.items():
                relative = f"structure_repairs/case/{name}"
                self.assertEqual(archive.read(f"files/{relative}"), content.encode())
                self.assertEqual(records[relative]["sha256"], hashlib.sha256(content.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
