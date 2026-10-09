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
    @staticmethod
    def _atom(serial, name, residue, number, *, chain="A", icode="", record="ATOM"):
        return (
            f"{record:<6}{serial:5d} {name:>4} {residue:>3} {chain:1}{number:4d}{icode:1}   "
            "   0.000   0.000   0.000  1.00  0.00           C\n"
        )

    @staticmethod
    def _link(first, second):
        # atom, residue, chain, number, insertion code, at the wwPDB LINK columns.
        def endpoint(values):
            name, residue, chain, number, icode = values
            return f"{name:>4} {residue:>3} {chain:1}{number:4d}{icode:1}"

        return f"LINK        {endpoint(first)}               {endpoint(second)}\n"

    @staticmethod
    def _modres(residue, number, parent, *, chain="A", icode=""):
        return f"MODRES TEST {residue:>3} {chain:1} {number:4d}{icode:1} {parent:>3}\n"

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

    def test_conect_parses_contiguous_five_digit_serials(self):
        path = self._write_pdb(
            self._atom(99998, "OG", "SER", 70)
            + self._atom(99999, "B", "BJI", 500, record="HETATM")
            + "CONECT9999899999\n"
        )
        links = find_covalent_ligand_links(path)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].ligand_residue, "BJI A 500")

    def test_link_and_conect_retain_insertion_codes_and_deduplicate(self):
        path = self._write_pdb(
            self._link(("OG", "SER", "A", 70, "B"), ("B", "BJI", "A", 500, "C"))
            + self._atom(1, "OG", "SER", 70, icode="B")
            + self._atom(2, "B", "BJI", 500, icode="C", record="HETATM")
            + "CONECT    1    2    2\nCONECT    2    1\n"
        )
        links = find_covalent_ligand_links(path)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].protein_residue, "SER A 70B")
        self.assertEqual(links[0].ligand_residue, "BJI A 500C")
        self.assertEqual(links[0].source, "LINK")

    def test_insertion_codes_distinguish_linked_residues(self):
        path = self._write_pdb("".join(
            self._link(("OG", "SER", "A", 70, code), ("B", "BJI", "A", 500, ""))
            for code in ("", "A")
        ))
        self.assertEqual(len(find_covalent_ligand_links(path)), 2)

    def test_conect_does_not_infer_role_from_atom_record_type(self):
        for record in ("ATOM", "HETATM"):
            with self.subTest(record=record):
                path = self._write_pdb(
                    self._atom(1, "OG", "SER", 70, record=record)
                    + self._atom(2, "B", "BJI", 500, record=record)
                    + "CONECT    1    2\n"
                )
                self.assertEqual(len(find_covalent_ligand_links(path)), 1)

    def test_modres_polymer_backbone_is_not_a_ligand_but_ser_bji_is(self):
        path = self._write_pdb(
            self._modres("BHD", 214, "ASP")
            + self._link(("C", "ALA", "A", 213, ""), ("N", "BHD", "A", 214, ""))
            + self._link(("C", "BHD", "A", 214, ""), ("N", "LYS", "A", 215, ""))
            + self._link(("OG", "SER", "A", 70, ""), ("B", "BJI", "A", 500, ""))
            + self._atom(1, "C", "ALA", 213)
            + self._atom(2, "N", "BHD", 214, record="HETATM")
            + "CONECT    1    2\n"
        )
        links = find_covalent_ligand_links(path)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].protein_residue, "SER A 70")
        self.assertEqual(links[0].ligand_residue, "BJI A 500")

    def test_modres_protein_can_itself_have_a_covalent_ligand(self):
        path = self._write_pdb(
            self._modres("BHD", 214, "ASP")
            + self._atom(1, "OB", "BHD", 214, record="HETATM")
            + self._atom(2, "B", "BJI", 500, record="HETATM")
            + "CONECT    1    2\n"
        )
        links = find_covalent_ligand_links(path)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].protein_residue, "BHD A 214")

    def test_modres_mapping_is_scoped_to_chain_number_name_and_insertion(self):
        for residue, number, chain, icode in (
            ("BHD", 214, "B", ""), ("BHD", 215, "A", ""),
            ("BHD", 214, "A", "I"), ("UNK", 214, "A", ""),
        ):
            with self.subTest(residue=residue, number=number, chain=chain, icode=icode):
                path = self._write_pdb(
                    self._modres("BHD", 214, "ASP")
                    + self._link(("C", "ALA", "A", 213, ""), ("N", residue, chain, number, icode))
                )
                self.assertEqual(len(find_covalent_ligand_links(path)), 1)

    def test_global_conect_does_not_lose_earlier_models(self):
        path = self._write_pdb(
            "MODEL        1\n"
            + self._atom(1, "OG", "SER", 70)
            + self._atom(2, "B", "BJI", 500, record="HETATM")
            + "ENDMDL\nMODEL        2\n"
            + self._atom(1, "OG", "SER", 70)
            + self._atom(2, "O", "HOH", 500, record="HETATM")
            + "ENDMDL\nCONECT    1    2\n"
        )
        self.assertEqual(len(find_covalent_ligand_links(path)), 1)

    def test_conect_never_pairs_atoms_from_different_models(self):
        path = self._write_pdb(
            "MODEL        1\n" + self._atom(1, "OG", "SER", 70)
            + "ENDMDL\nMODEL        2\n"
            + self._atom(2, "B", "BJI", 500, record="HETATM")
            + "ENDMDL\nCONECT    1    2\n"
        )
        self.assertEqual(find_covalent_ligand_links(path), ())

    def test_model_local_conect_does_not_apply_to_another_model(self):
        path = self._write_pdb(
            "MODEL        1\n"
            + self._atom(1, "OG", "SER", 70)
            + self._atom(2, "O", "HOH", 500, record="HETATM")
            + "CONECT    1    2\nENDMDL\nMODEL        2\n"
            + self._atom(1, "OG", "SER", 70)
            + self._atom(2, "B", "BJI", 500, record="HETATM")
            + "ENDMDL\n"
        )
        self.assertEqual(find_covalent_ligand_links(path), ())

    def test_covalent_error_explains_parameter_requirement(self):
        path = self._write_pdb(
            self._link(("OG", "SER", "A", 70, ""), ("B", "BJI", "A", 500, ""))
        )
        with self.assertRaisesRegex(CovalentLigandError, "validated custom topology"):
            ensure_noncovalent_complex(path)


if __name__ == "__main__":
    unittest.main()
