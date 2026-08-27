from __future__ import annotations
import json
import tempfile
import unittest
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

    def test_manifest_path(self):
        self.assertEqual(manifest_path("project"), Path("project") / MANIFEST_FILENAME)

if __name__ == "__main__": unittest.main()
