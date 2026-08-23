from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.structure_validation import (
    CovalentLigandError,
    ensure_noncovalent_complex,
    find_covalent_ligand_links,
)


class StructureValidationTests(unittest.TestCase):
    def _write_pdb(self, content: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "complex.pdb"
        path.write_text(content, encoding="utf-8")
        return path

    def test_rejects_protein_ligand_link_record(self):
        path = self._write_pdb(
            "LINK         OG  SER A  73                 B02 C6S A 300     "
            "1555   1555  1.39\n"
        )

        links = find_covalent_ligand_links(path)

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].protein_residue, "SER A 73")
        self.assertEqual(links[0].ligand_residue, "C6S A 300")
        with self.assertRaisesRegex(CovalentLigandError, "C6S.*SER"):
            ensure_noncovalent_complex(path)

    def test_rejects_cross_atom_conect_without_link_record(self):
        path = self._write_pdb(
            "ATOM      1  OG  SER A  73       0.000   0.000   0.000  1.00  0.00           O\n"
            "HETATM    2  B02 C6S A 300       1.390   0.000   0.000  1.00  0.00           B\n"
            "CONECT    1    2\n"
        )

        with self.assertRaises(CovalentLigandError):
            ensure_noncovalent_complex(path)

    def test_accepts_nonbonded_ligand_and_disulfide_link(self):
        path = self._write_pdb(
            "LINK         SG  CYS A  10                 SG  CYS A  20     "
            "1555   1555  2.03\n"
            "ATOM      1  SG  CYS A  10       0.000   0.000   0.000  1.00  0.00           S\n"
            "ATOM      2  SG  CYS A  20       2.030   0.000   0.000  1.00  0.00           S\n"
            "HETATM    3  C1  LIG A 300       5.000   0.000   0.000  1.00  0.00           C\n"
        )

        ensure_noncovalent_complex(path)
        self.assertEqual(find_covalent_ligand_links(path), ())

    def test_does_not_misclassify_common_n_linked_glycan_as_ligand(self):
        path = self._write_pdb(
            "LINK         ND2 ASN A  45                 C1  NAG A 301     "
            "1555   1555  1.45\n"
        )

        ensure_noncovalent_complex(path)
        self.assertEqual(find_covalent_ligand_links(path), ())


if __name__ == "__main__":
    unittest.main()
