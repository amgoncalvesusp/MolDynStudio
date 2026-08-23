from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from utils.topology_builder import (
    UnsupportedLigandChemistryError,
    validate_acpype_input,
)


class TopologyBuilderTests(unittest.TestCase):
    def test_rejects_boron_in_sdf_before_running_acpype(self):
        sdf = """C6S
  MolDynStudio

  2  1  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 B   0  0  0  0  0  0  0  0  0  0  0  0
    1.4000    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0  0  0  0
M  END
$$$$
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c6s.sdf"
            path.write_text(sdf, encoding="utf-8")

            with self.assertRaisesRegex(
                UnsupportedLigandChemistryError,
                "boron.*CGenFF.*covalent",
            ):
                validate_acpype_input(path)

    def test_accepts_common_organic_elements_in_mol2(self):
        mol2 = """@<TRIPOS>MOLECULE
ligand
2 1 0 0 0
SMALL
USER_CHARGES
@<TRIPOS>ATOM
1 C1 0.0 0.0 0.0 C.3 1 LIG 0.0
2 O1 1.4 0.0 0.0 O.2 1 LIG 0.0
@<TRIPOS>BOND
1 1 2 1
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ligand.mol2"
            path.write_text(mol2, encoding="utf-8")

            self.assertEqual(validate_acpype_input(path), frozenset({"C", "O"}))

    def test_rejects_boron_in_v3000_sdf(self):
        sdf = """C6S
  MolDynStudio

  0  0  0     0  0            999 V3000
M  V30 BEGIN CTAB
M  V30 COUNTS 2 1 0 0 0
M  V30 BEGIN ATOM
M  V30 1 B 0.0 0.0 0.0 0
M  V30 2 O 1.4 0.0 0.0 0
M  V30 END ATOM
M  V30 BEGIN BOND
M  V30 1 1 1 2
M  V30 END BOND
M  V30 END CTAB
M  END
$$$$
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c6s-v3000.sdf"
            path.write_text(sdf, encoding="utf-8")

            with self.assertRaises(UnsupportedLigandChemistryError):
                validate_acpype_input(path)

    def test_pdb_fallback_preserves_two_letter_elements(self):
        pdb = (
            "HETATM    1 BE   BE  A   1       0.000   0.000   0.000\n"
            "HETATM    2 NA   NA  A   2       1.000   0.000   0.000\n"
            "HETATM    3 SI   SIL A   3       2.000   0.000   0.000\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "elements.pdb"
            path.write_text(pdb, encoding="utf-8")

            self.assertEqual(
                validate_acpype_input(path),
                frozenset({"Be", "Na", "Si"}),
            )


if __name__ == "__main__":
    unittest.main()
