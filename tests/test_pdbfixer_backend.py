"""Real reconstruction tests and scientific boundary checks for PDBFixer."""
from pathlib import Path
import importlib.util
import tempfile
import unittest
from unittest import mock

from core.pdbfixer_backend import backend_available, build_missing_structure
from tests.test_real_gromacs_smoke import _glycine_dipeptide_pdb


def atoms(text):
    return {
        (line[21], line[22:26].strip(), line[26].strip(), line[17:20].strip(), line[12:16].strip()): line
        for line in text.splitlines() if line.startswith(("ATOM  ", "HETATM"))
    }


class BackendBoundaryTests(unittest.TestCase):
    def test_availability_is_offline_and_reports_missing_dependencies(self):
        with mock.patch("core.pdbfixer_backend.importlib.util.find_spec", return_value=None):
            self.assertFalse(backend_available())

    def test_same_path_and_existing_output_cannot_replace_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.pdb"
            source.write_text(_glycine_dipeptide_pdb())
            with self.assertRaisesRegex(ValueError, "different|exists"):
                build_missing_structure(source, source)
            self.assertEqual(source.read_text(), _glycine_dipeptide_pdb())

    def test_altloc_and_duplicate_atom_sites_are_rejected_before_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.pdb"
            lines = _glycine_dipeptide_pdb().splitlines(True)
            for content in (lines[0][:16] + "A" + lines[0][17:] + "".join(lines[1:]), lines[0] + "".join(lines)):
                source.write_text(content)
                with self.assertRaisesRegex(ValueError, "alternate|Duplicate"):
                    build_missing_structure(source, Path(directory) / "result.pdb")


@unittest.skipUnless(importlib.util.find_spec("pdbfixer"), "PDBFixer optional dependency")
class RealBackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "input.pdb"
        self.output = self.root / "result.pdb"

    def tearDown(self):
        self.directory.cleanup()

    def test_missing_heavy_atom_is_modeled_and_observed_coordinates_are_exact(self):
        text = "".join(line for line in _glycine_dipeptide_pdb().splitlines(True)
                       if not (line.startswith("ATOM") and line[22:26].strip() == "1" and line[12:16].strip() == "O"))
        # A longer deposited sequence must not cause unapproved terminal rebuilding.
        self.source.write_text("SEQRES   1 A    4  GLY GLY GLY GLY\n" + text)
        result = build_missing_structure(self.source, self.output, seed=42)
        before, after = atoms(text), atoms(self.output.read_text())
        self.assertEqual(len(after), 9)
        self.assertEqual(result.preserved_atom_count, 8)
        self.assertEqual(result.max_observed_displacement_angstrom, 0.0)
        self.assertEqual([(a["atom_name"], a["reason"]) for a in result.modeled_atoms], [("O", "missing_heavy_atom")])
        self.assertTrue(all(after[key][30:66] == line[30:66] for key, line in before.items()))
        self.assertTrue(all(line[76:78].strip() != "H" for line in after.values()))

    def test_disabled_heavy_repair_does_not_invent_terminal_atoms(self):
        self.source.write_text(_glycine_dipeptide_pdb())
        result = build_missing_structure(self.source, self.output, add_heavy_atoms=False)
        self.assertEqual(result.modeled_atoms, ())
        self.assertEqual(atoms(self.source.read_text()), atoms(self.output.read_text()))

    def test_numbering_collision_is_rejected_without_output(self):
        self.source.write_text(_glycine_dipeptide_pdb())
        gap = {"chain_id": "A", "insert_before": 1, "resnames": ("GLY",)}
        with self.assertRaisesRegex(ValueError, "number|identity"):
            build_missing_structure(self.source, self.output, missing_residues=(gap,))
        self.assertFalse(self.output.exists())

    def test_unknown_ligand_context_and_covalent_records_remain_intact(self):
        text = _glycine_dipeptide_pdb().replace("END\n", "")
        ligand = "HETATM   20  B   BJI A 500       2.000  10.000   0.000  0.75 18.00           B  \n"
        link = "LINK         N   GLY A   1                 B   BJI A 500     1555   1555  1.40\n"
        self.source.write_text(link + text + ligand + "CONECT    1   20\nCONECT   20    1\nEND\n")
        result = build_missing_structure(self.source, self.output, add_heavy_atoms=False)
        output = self.output.read_text()
        self.assertIn(link, output)
        self.assertEqual(atoms(output)[("A", "500", "", "BJI", "B")][30:], ligand.rstrip("\n")[30:])
        self.assertEqual(result.preserved_atom_count, 10)
        serials = {key: int(line[6:11]) for key, line in atoms(output).items()}
        self.assertIn(f"CONECT{serials[('A','1','','GLY','N')]:5d}{serials[('A','500','','BJI','B')]:5d}", output)

    def test_internal_gap_uses_approved_sequence_and_free_author_ids(self):
        first = [line for line in _glycine_dipeptide_pdb().splitlines(True)
                 if line.startswith("ATOM") and line[22:26].strip() == "1"]
        third = ((3, "N", 6., 1.63), (3, "CA", 7.45, 1.47), (3, "C", 8.2, 2.8),
                 (3, "O", 7.63, 3.88), (4, "N", 9.5, 2.75), (4, "CA", 10.23, 4.01),
                 (4, "C", 11.7, 3.83), (4, "O", 12.38, 4.86), (4, "OXT", 12.19, 2.62))
        text = "".join(first) + "".join(
            f"ATOM  {i:5d} {name:^4s} GLY A{resid:4d}    {x:8.3f}{y:8.3f}{0.:8.3f}{1.:6.2f}{0.:6.2f}          {name[0]:>2s}\n"
            for i, (resid, name, x, y) in enumerate(third, 5)) + "TER\nEND\n"
        self.source.write_text(text)
        result = build_missing_structure(self.source, self.output,
                                         missing_residues=({"chain_id": "A", "insert_before": 1, "resnames": ("GLY",)},), seed=42)
        self.assertEqual([r["residue_id"] for r in result.residue_mapping], ["1", "2", "3", "4"])
        self.assertEqual(len(result.modeled_atoms), 4)
        self.assertTrue(all(a["reason"] == "missing_residue" for a in result.modeled_atoms))
        self.assertEqual(result.max_observed_displacement_angstrom, 0.)

    def test_modeled_heavy_atoms_preserve_unknown_ligand_and_linked_atoms(self):
        text = "".join(line for line in _glycine_dipeptide_pdb().splitlines(True)
                       if not (line.startswith("ATOM") and line[22:26].strip() == "1" and line[12:16].strip() == "O"))
        ligand = "HETATM   20  B   BJI A 500      -1.400   0.000   0.000  0.75 18.00           B  \n"
        self.source.write_text(text.replace("END\n", "") + ligand + "CONECT    1   20\nCONECT   20    1\nEND\n")
        result = build_missing_structure(self.source, self.output)
        self.assertEqual(result.preserved_atom_count, 9)
        self.assertEqual(len(result.modeled_atoms), 1)
        self.assertIn("BJI", " ".join(result.warnings))
        before, after = atoms(self.source.read_text()), atoms(self.output.read_text())
        self.assertTrue(all(after[key][30:] == line[30:] for key, line in before.items()))

    def test_native_worker_failure_returns_an_error_and_retains_log(self):
        self.source.write_text(_glycine_dipeptide_pdb())
        with mock.patch("core.pdbfixer_backend.subprocess.run", return_value=mock.Mock(returncode=-1073741819)):
            with self.assertRaisesRegex(RuntimeError, "Native engine.*Evidence"):
                build_missing_structure(self.source, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(len(tuple(self.root.glob("reconstruction_*/engine.log"))), 1)

    def test_gap_cannot_use_polymer_index_in_a_segment_mixed_with_ligand(self):
        original = _glycine_dipeptide_pdb().splitlines(True)
        ligand = "HETATM   20  B   BJI A 500      -1.400   0.000   0.000  0.75 18.00           B  \n"
        text = "".join(original[:4]) + ligand + "".join(original[4:])
        self.source.write_text(text)
        with self.assertRaisesRegex(ValueError, "non-polymer components"):
            build_missing_structure(self.source, self.output,
                                    missing_residues=({"chain_id": "A", "insert_before": 1, "resnames": ("GLY",)},))
        self.assertFalse(self.output.exists())

    def test_dangling_link_is_rejected_even_without_heavy_atom_addition(self):
        link = "LINK         SG  CYS A  77                 SG  CYS A 123     1555   1555  2.03\n"
        self.source.write_text(link + _glycine_dipeptide_pdb())
        with self.assertRaisesRegex(ValueError, "LINK.*absent atom"):
            build_missing_structure(self.source, self.output, add_heavy_atoms=False)
        self.assertFalse(self.output.exists())

    def test_blank_chain_identity_matches_core_mapping_convention(self):
        text = "".join(line[:21] + " " + line[22:] if line.startswith("ATOM") else line
                       for line in _glycine_dipeptide_pdb().splitlines(True))
        self.source.write_text(text)
        result = build_missing_structure(self.source, self.output, add_heavy_atoms=False)
        self.assertTrue(all(record["chain_id"] == "" for record in result.residue_mapping))
        self.assertEqual(result.preserved_atom_count, 9)


if __name__ == "__main__":
    unittest.main()
