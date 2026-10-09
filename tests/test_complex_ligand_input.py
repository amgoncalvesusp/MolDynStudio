"""Bound ligand copies must preserve the complete reviewed chemical model."""
from pathlib import Path
import json
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from core.complex_ligand_input import prepare_complex_inputs, read_mol2


MOL2 = """@<TRIPOS>MOLECULE
LIG
3 2 1 0 0
SMALL
USER_CHARGES
@<TRIPOS>ATOM
1 C1 0 0 0 C.1 1 LIG 0.1
2 N1 1.16 0 0 N.1 1 LIG -0.2
3 H1 -1.06 0 0 H 1 LIG 0.1
@<TRIPOS>BOND
1 1 2 3
2 1 3 1
@<TRIPOS>SUBSTRUCTURE
1 LIG 1
"""


def atom(serial, name, residue, resid, x, y=0., element=None, record="HETATM"):
    return (f"{record:<6}{serial:5d} {name:>4} {residue:>3} A{resid:4d}    "
            f"{x:8.3f}{y:8.3f}{0.:8.3f}{1.:6.2f}{20.:6.2f}          {element or name[0]:>2}  ")


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / "complex.pdb"
    records = [atom(1, "N", "GLY", 1, -8, record="ATOM"),
               atom(2, "CA", "GLY", 1, -7, record="ATOM", element="C"),
               atom(3, "C", "GLY", 1, -6, record="ATOM"),
               atom(4, "O", "GLY", 1, -5, record="ATOM"), "TER",
               atom(11, "C1", "LIG", 301, 0), atom(12, "N1", "LIG", 301, 1.16),
               atom(21, "C1", "LIG", 302, 0, 5), atom(22, "N1", "LIG", 302, 1.16, 5),
               atom(30, "O", "HOH", 401, 9), "CONECT   11   12", "CONECT   21   22", "END"]
    source.write_text("\n".join(records) + "\n")
    template = tmp_path / "ligand.mol2"
    template.write_text(MOL2)
    return source, template, tmp_path / "outputs"


def hydrogenator(command, **kwargs):
    source = Path(command[command.index("-imol2") + 1])
    destination = Path(command[command.index("-O") + 1])
    heavy = read_mol2(source)
    coords = {a.name: a.coordinates for a in heavy.atoms}
    text = MOL2
    for name, old in (("C1", "0 0 0"), ("N1", "1.16 0 0")):
        text = text.replace(f"{name} {old}", f"{name} {' '.join(map(str, coords[name]))}")
    h = (coords["C1"][0] - 1.06, coords["C1"][1], coords["C1"][2])
    destination.write_text(text.replace("H1 -1.06 0 0", f"H1 {' '.join(map(str, h))}"))
    return CompletedProcess(command, 0, "", "1 molecule converted")


def run(inputs, **kwargs):
    return prepare_complex_inputs(*inputs, residue_name="LIG", **kwargs)


def test_complete_copies_preserve_heavy_coordinates_water_and_sources(inputs):
    original = tuple(path.read_bytes() for path in inputs[:2])
    with patch("core.complex_ligand_input.wsl_bridge.win_to_wsl", side_effect=str), patch(
        "core.complex_ligand_input.wsl_bridge.run", side_effect=hydrogenator
    ) as command:
        result = run(inputs, conda_env="science")
    assert len(result.ligand_copies) == 1
    first, second = (read_mol2(p) for p in (result.ligand_path, *result.ligand_copies))
    assert first.atoms[0].coordinates == (0., 0., 0.)
    assert second.atoms[0].coordinates == (0., 5., 0.)
    assert second.atoms[2].coordinates == (-1.06, 5., 0.)
    protein = result.protein_path.read_text()
    assert "HOH" in protein and "LIG" not in protein
    assert tuple(path.read_bytes() for path in inputs[:2]) == original
    proof = json.loads(result.provenance_path.read_text())
    assert proof["ligand_molecule_count"] == 2
    assert proof["max_heavy_displacement_angstrom"] == 0
    assert all(call.kwargs["env_name"] == "science" for call in command.call_args_list)


def test_unknown_heterogen_requires_named_explicit_exclusion(inputs):
    source = inputs[0]
    source.write_text(source.read_text().replace("END", atom(31, "C1", "GOL", 402, 14) + "\nEND"))
    with pytest.raises(ValueError, match="GOL"):
        run(inputs)
    with patch("core.complex_ligand_input.wsl_bridge.win_to_wsl", side_effect=str), patch(
        "core.complex_ligand_input.wsl_bridge.run", side_effect=hydrogenator
    ):
        result = run(inputs, excluded_residue_names=("GOL",))
    assert "GOL" not in result.protein_path.read_text()
    assert json.loads(result.provenance_path.read_text())["excluded_residue_names"] == ["GOL"]


@pytest.mark.parametrize("excluded", [("LIG",), ("HOH",), ("GLY",), ("ABS",)])
def test_invalid_exclusions_cannot_drop_ligand_water_protein_or_hide_typos(inputs, excluded):
    with pytest.raises(ValueError):
        run(inputs, excluded_residue_names=excluded)


def test_covalent_bond_checked_before_ligand_separation(inputs):
    source = inputs[0]
    source.write_text(source.read_text().replace("END", "CONECT    1   11\nEND"))
    with pytest.raises(ValueError, match="[Cc]ovalent"):
        run(inputs)
    assert not inputs[2].exists()


@pytest.mark.parametrize("edit,pattern", [
    (lambda text: text.replace("  N1 LIG A 302", "  X1 LIG A 302"), "names"),
    (lambda text: text.replace("CONECT   21   22", "CONECT   21   30"), "connectivity|covalent|bond"),
    (lambda text: text.replace("  1.160   5.000", " 11.160   5.000"), "distance|geometry"),
])
def test_bad_second_copy_cannot_be_silently_discarded(inputs, edit, pattern):
    inputs[0].write_text(edit(inputs[0].read_text()))
    with pytest.raises(ValueError, match=pattern):
        run(inputs)


def test_generated_hydrogen_protonation_mismatch_is_fatal(inputs):
    def bad(command, **kwargs):
        hydrogenator(command, **kwargs)
        out = Path(command[command.index("-O") + 1])
        out.write_text(out.read_text().replace("2 1 3 1", "2 2 3 1"))
        return CompletedProcess(command, 0, "", "")
    with patch("core.complex_ligand_input.wsl_bridge.win_to_wsl", side_effect=str), patch(
        "core.complex_ligand_input.wsl_bridge.run", side_effect=bad
    ), pytest.raises(ValueError, match="hydrogen|protonation"):
        run(inputs)


def test_generated_heavy_atom_movement_is_fatal(inputs):
    def bad(command, **kwargs):
        hydrogenator(command, **kwargs)
        out = Path(command[command.index("-O") + 1])
        out.write_text(out.read_text().replace("C1 0.0", "C1 0.2"))
        return CompletedProcess(command, 0, "", "")
    with patch("core.complex_ligand_input.wsl_bridge.win_to_wsl", side_effect=str), patch(
        "core.complex_ligand_input.wsl_bridge.run", side_effect=bad
    ), pytest.raises(ValueError, match="coordinate|moved"):
        run(inputs)


def test_external_failure_propagates_and_preserves_original(inputs):
    original = inputs[0].read_bytes()
    with patch("core.complex_ligand_input.wsl_bridge.run", return_value=CompletedProcess([], 2, "", "missing obabel")), pytest.raises(ValueError, match="Open Babel"):
        run(inputs)
    assert inputs[0].read_bytes() == original


@pytest.mark.parametrize("bad", [MOL2.replace("3 2 1", "4 2 1"), MOL2.replace("N1 1.16", "C1 1.16"),
                                 MOL2.replace("0.1", "nan"), MOL2.replace("2 1 3 1", "2 1 4 1")])
def test_invalid_template_rejected(inputs, bad):
    inputs[1].write_text(bad)
    with pytest.raises(ValueError):
        run(inputs)


def test_openbabel_generated_hydrogens_can_share_display_name_only(inputs):
    water = MOL2.replace("C1 0 0 0 C.1", "O1 0 0 0 O.3").replace(
        "N1 1.16 0 0 N.1", "H 0.96 0 0 H").replace("H1 -1.06", "H -0.3").replace("1 1 2 3", "1 1 2 1")
    inputs[1].write_text(water)
    with pytest.raises(ValueError, match="duplicate"):
        read_mol2(inputs[1])
    generated = read_mol2(inputs[1], allow_unnamed_hydrogens=True)
    assert len({a.name for a in generated.atoms}) == 3


def test_modified_polymer_cannot_be_excluded_as_heterogen(inputs):
    source = inputs[0]
    modified = atom(40, "CA", "BHD", 214, 20, element="C")
    modres = list(" " * 80)
    for start, value in ((0, "MODRES"), (12, "BHD"), (16, "A"), (18, " 214"), (24, "ASP")):
        modres[start:start + len(value)] = value
    source.write_text("".join(modres) + "\n" + source.read_text().replace("END", modified + "\nEND"))
    with pytest.raises(ValueError, match="protein|polymer"):
        run(inputs, excluded_residue_names=("BHD",))
    with pytest.raises(ValueError, match="protein|polymer"):
        prepare_complex_inputs(*inputs, residue_name="BHD", excluded_residue_names=("LIG",))


def test_retained_protein_link_survives_partition(inputs):
    source = inputs[0]
    link = list(" " * 80)
    for start, value in ((0, "LINK"), (12, " N  "), (17, "GLY"), (21, "A"), (22, "   1"),
                         (42, " C  "), (47, "GLY"), (51, "A"), (52, "   1")):
        link[start:start + len(value)] = value
    line = "".join(link)
    source.write_text(line + "\n" + source.read_text())
    with patch("core.complex_ligand_input.wsl_bridge.win_to_wsl", side_effect=str), patch(
        "core.complex_ligand_input.wsl_bridge.run", side_effect=hydrogenator
    ):
        result = run(inputs)
    assert line in result.protein_path.read_text()


@pytest.mark.parametrize("residue,resid,excluded", [("GOL", 402, ("GOL",)), ("LIG", 302, ())])
def test_link_between_selected_ligand_and_other_residue_cannot_be_lost(inputs, residue, resid, excluded):
    source = inputs[0]
    text = source.read_text()
    if residue == "GOL":
        text = text.replace("END", atom(31, "C1", "GOL", resid, -1.5) + "\nEND")
    link = list(" " * 80)
    for start, value in ((0, "LINK"), (12, " C1 "), (17, "LIG"), (21, "A"), (22, " 301"),
                         (42, " C1 "), (47, residue), (51, "A"), (52, f"{resid:4d}")):
        link[start:start + len(value)] = value
    source.write_text("".join(link) + "\n" + text)
    with pytest.raises(ValueError, match="covalent|bond|connectivity"):
        run(inputs, excluded_residue_names=excluded)


def test_duplicate_atom_identity_with_distinct_serial_is_ambiguous(inputs):
    inputs[0].write_text(inputs[0].read_text().replace("END", atom(50, "N", "GLY", 1, -20, record="ATOM") + "\nEND"))
    with patch("core.complex_ligand_input.wsl_bridge.win_to_wsl", side_effect=str), patch(
        "core.complex_ligand_input.wsl_bridge.run", side_effect=hydrogenator
    ), pytest.raises(ValueError, match="identity|identities|ambiguous"):
        run(inputs)
