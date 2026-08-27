from __future__ import annotations
import json
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import asdict
from pathlib import Path
from core.run_manifest import *

def make_manifest(ligand=None):
    return new_manifest("project", "protein_ligand" if ligand else "protein", "protein.pdb", ligand, "amber99sb", "tip3p", "gmx", "moldynstudio", 4, "auto")

class RunManifestTests(unittest.TestCase):
    def test_names_and_initial_statuses(self):
        self.assertEqual(len(tuple(StageName)), 5)
        manifest = make_manifest()
        self.assertEqual(set(manifest.stages), {name.value for name in StageName})
        self.assertTrue(all(stage.status == StageStatus.NOT_READY.value for stage in manifest.stages.values()))

    def test_protein_only_and_ligand_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            for original in (make_manifest(), make_manifest("ligand.mol2")):
                loaded = load_manifest(save_manifest(original, Path(d) / "manifest.json"))
                self.assertEqual(asdict(original), asdict(loaded))

    def test_command_exit_code_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = make_manifest()
            manifest.stages["production"].commands.append(CommandRecord("production", ["gmx", "mdrun"], "project", utc_now_iso(), exit_code=3, success=False))
            loaded = load_manifest(save_manifest(manifest, Path(d) / "manifest.json"))
            self.assertEqual(loaded.stages["production"].commands[0].exit_code, 3)

    def test_schema_and_unknown_stage_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "manifest.json"
            data = asdict(make_manifest()); data["schema_version"] = "99"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(RunManifestError): load_manifest(path)
            data = asdict(make_manifest()); data["stages"]["bogus"] = data["stages"].pop("nvt")
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(RunManifestError): load_manifest(path)

    def test_malformed_json_typed_and_atomic_replace(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "manifest.json"; path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(RunManifestError): load_manifest(path)
            path.write_text("old", encoding="utf-8"); save_manifest(make_manifest(), path)
            self.assertEqual(load_manifest(path).protein_source, "protein.pdb")

    def test_corrupt_nested_records_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "manifest.json"
            for mutate in (
                lambda x: x["stages"]["preparation"].update(inputs=[]),
                lambda x: x["stages"]["preparation"].update(outputs={"x": 3}),
                lambda x: x["stages"]["preparation"].pop("inputs"),
                lambda x: x["stages"]["preparation"].pop("outputs"),
                lambda x: x["stages"]["preparation"].update(commands=["bad"]),
                lambda x: x["stages"]["preparation"].update(commands=[{"stage": "npt", "argv": [], "cwd": "p", "started_at": "t"}]),
                lambda x: x.update(requested_cores="4"),
            ):
                data = asdict(make_manifest()); mutate(data)
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(RunManifestError): load_manifest(path)

    def test_missing_stage_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "manifest.json"
            for field in ("name", "status", "started_at", "finished_at", "message", "commands", "inputs", "outputs"):
                data = asdict(make_manifest()); data["stages"]["preparation"].pop(field)
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(RunManifestError): load_manifest(path)

    def test_atomic_failure_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "manifest.json"
            path.write_text("old-content", encoding="utf-8")
            with patch("core.run_manifest.os.replace", side_effect=OSError("simulated crash")):
                with self.assertRaises(RunManifestError): save_manifest(make_manifest(), path)
            self.assertEqual(path.read_text(encoding="utf-8"), "old-content")

    def test_manifest_path(self):
        self.assertEqual(manifest_path("project"), Path("project") / MANIFEST_FILENAME)

if __name__ == "__main__": unittest.main()
