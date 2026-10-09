from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.charmm_ligand import prepare_charmm_ligand
from core.charmm_conversion import convert_charmm_ligand
from tests.test_charmm_conversion import MOL2, PAR, RTF


class CharmmLigandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source.mol2"
        self.source.write_text(MOL2)
        self.package = self.root / "package"
        self.package.mkdir()
        for name, text in (("lig.rtf", RTF), ("lig.par", PAR), ("lig.mol2", MOL2)):
            (self.package / name).write_text(text)
        self.refresh_manifest()

    def refresh_manifest(self):
        values = {"method": "MATCH", "session": "123", "input_sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                  "output_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                    for path in self.package.iterdir() if path.name != "provenance.json"}}
        (self.package / "provenance.json").write_text(json.dumps(values))

    def prepare(self, **kwargs):
        return prepare_charmm_ligand(self.source, self.root / "project", net_charge=0,
                                    allow_upload=False, **kwargs)

    def test_two_copies_keep_pose_coordinates_atom_order_and_distinct_residue_ids(self):
        copy_path = self.root / "copy.mol2"
        copy_path.write_text(MOL2.replace("0.0 0.4 -0.1", "10.0 0.4 -0.1"))
        logged = []
        with patch("core.charmm_ligand.parameterize_match", return_value=self.package) as client:
            result = self.prepare(ligand_copies=(str(copy_path),), on_log=logged.append)
        self.assertEqual(result.molecule_count, 2)
        self.assertEqual(len(result.parameter_includes), 1)
        rows = result.ligand_gro.read_text().splitlines()
        self.assertEqual(int(rows[1]), 10)
        self.assertEqual([line[10:15].strip() for line in rows[2:7]], list("ABCDE"))
        self.assertEqual([int(line[:5]) for line in rows[2:12]], [1] * 5 + [2] * 5)
        self.assertAlmostEqual(float(rows[2][20:28]), 0.0)
        self.assertAlmostEqual(float(rows[7][20:28]), 1.0)
        self.assertTrue(all(float(value) > 0 for value in rows[-1].split()))
        manifest = json.loads(result.provenance_path.read_text())
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["molecule_count"], 2)
        self.assertEqual(manifest["inputs"][0]["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertFalse(client.call_args.kwargs["allow_upload"])
        self.assertTrue(any("2 copies" in line for line in logged))

    def test_backend_changed_hydrogens_or_atom_names_is_rejected(self):
        (self.package / "lig.mol2").write_text(MOL2.replace("1 E ", "1 H "))
        self.refresh_manifest()
        with patch("core.charmm_ligand.parameterize_match", return_value=self.package), \
             self.assertRaisesRegex(ValueError, "identit|chemistry|protonation"):
            self.prepare()
        manifests = tuple((self.root / "project").rglob("charmm_ligand_manifest.json"))
        self.assertEqual(len(manifests), 1)
        self.assertEqual(json.loads(manifests[0].read_text())["status"], "failed")

    def test_changed_bond_order_is_rejected(self):
        (self.package / "lig.mol2").write_text(MOL2.replace("1 5 4 1", "1 5 4 2"))
        self.refresh_manifest()
        with patch("core.charmm_ligand.parameterize_match", return_value=self.package), \
             self.assertRaisesRegex(ValueError, "bond|chemistry"):
            self.prepare()

    def test_incompatible_copy_is_rejected_before_network(self):
        second = self.root / "bad.mol2"
        second.write_text(MOL2.replace("1 E ", "1 Q "))
        with patch("core.charmm_ligand.parameterize_match") as client, self.assertRaises(ValueError):
            self.prepare(ligand_copies=(str(second),))
        client.assert_not_called()

    def test_source_change_while_backend_runs_is_rejected(self):
        def change(*args, **kwargs):
            self.source.write_text(MOL2 + "\n")
            return self.package
        with patch("core.charmm_ligand.parameterize_match", side_effect=change), \
             self.assertRaisesRegex(ValueError, "changed"):
            self.prepare()

    def test_failed_upload_has_no_completed_artifacts(self):
        with patch("core.charmm_ligand.parameterize_match", side_effect=RuntimeError("offline")), \
             self.assertRaisesRegex(RuntimeError, "offline"):
            self.prepare()
        self.assertFalse(tuple((self.root / "project").rglob("ligand.itp")))

    def test_package_uses_mol2_with_same_basename_as_rtf(self):
        (self.package / "tautomer.mol2").write_text(MOL2.replace("1 E ", "1 Q "))
        with patch("core.charmm_ligand.parameterize_match", return_value=self.package):
            result = self.prepare()
        self.assertEqual(result.molecule_count, 1)

    def test_invalid_options_do_not_contact_service(self):
        options = ({"net_charge": None}, {"net_charge": True}, {"net_charge": 21},
                   {"allow_upload": "yes"}, {"ligand_copies": "pose.mol2"},
                   {"ligand_copies": (str(self.source),) * 100})
        for extra in options:
            with self.subTest(options=extra), patch("core.charmm_ligand.parameterize_match") as client:
                with self.assertRaises(ValueError):
                    prepare_charmm_ligand(self.source, self.root / "project",
                                         **{"net_charge": 0, "allow_upload": False, **extra})
                client.assert_not_called()

    def test_malformed_mol2_rejected_before_service(self):
        for source in ("", MOL2 + MOL2, MOL2.replace("1 E ", "1 A "),
                       MOL2.replace("1.0 1.0 1.0", "nan 1.0 1.0"), MOL2.replace("C.3", "Du")):
            self.source.write_text(source)
            with self.subTest(mol2=source), patch("core.charmm_ligand.parameterize_match") as client:
                with self.assertRaises(ValueError):
                    self.prepare()
                client.assert_not_called()

    def test_missing_matching_parameter_file_is_rejected(self):
        (self.package / "lig.par").rename(self.package / "other.par")
        with patch("core.charmm_ligand.parameterize_match", return_value=self.package), \
             self.assertRaisesRegex(ValueError, "same basename"):
            self.prepare()

    def test_changed_parameter_bytes_cannot_be_certified_by_old_provenance(self):
        (self.package / "lig.par").write_text(PAR.replace("300.0 1.50", "400.0 1.50"))
        with patch("core.charmm_ligand.parameterize_match", return_value=self.package), \
             self.assertRaisesRegex(ValueError, "hash|integrity|changed"):
            self.prepare()

    def test_conversion_input_change_cannot_be_marked_completed(self):
        def changing_converter(rtf, par, *args, **kwargs):
            par.write_text(par.read_text().replace("300.0 1.50", "400.0 1.50"))
            return convert_charmm_ligand(rtf, par, *args, **kwargs)
        with patch("core.charmm_ligand.parameterize_match", return_value=self.package), \
             patch("core.charmm_ligand.convert_charmm_ligand", side_effect=changing_converter), \
             self.assertRaisesRegex(ValueError, "changed"):
            self.prepare()


if __name__ == "__main__":
    unittest.main()
