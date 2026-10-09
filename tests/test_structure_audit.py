from core.structure_audit import audit_structure
import hashlib
from pathlib import Path
from unittest.mock import patch


def atom(serial, name, resid=1, *, resname="ALA", chain="A", altloc="", icode="", x=1., occupancy=1., record="ATOM"):
    return f"{record:<6}{serial:5d} {name:>4}{altloc:1}{resname:>3} {chain:1}{resid:4d}{icode:1}   {x:8.3f}{2.:8.3f}{3.:8.3f}{occupancy:6.2f}{20.:6.2f}          {name[0]:>2}  "


def test_exact_duplicate_keeps_identity_and_insertion_code(tmp_path):
    source = tmp_path / "source.pdb"
    row = atom(1, "CA", 52, icode="A")
    source.write_text(row + "\n" + row + "\nEND\n")
    result = audit_structure(source)
    assert result.exact_duplicate_lines == (2,)
    assert result.atoms[0].identity.insertion_code == "A"
    assert result.atoms[0].identity.residue_number == "52"


def test_distinct_close_atoms_are_reported_not_duplicates(tmp_path):
    source = tmp_path / "source.pdb"
    source.write_text(atom(1, "CA") + "\n" + atom(2, "CB", x=1.1) + "\nEND\n")
    result = audit_structure(source)
    assert result.exact_duplicate_lines == ()
    assert len(result.collisions) == 1


def test_altloc_selects_coherent_most_complete_residue(tmp_path):
    source = tmp_path / "source.pdb"
    source.write_text("\n".join((atom(1, "N", x=0.), atom(2, "CA", altloc="A", occupancy=.9, x=2.),
                                 atom(3, "CA", altloc="B", occupancy=.6, x=2.1), atom(4, "CB", altloc="B", occupancy=.6, x=4.), "END")))
    audit = audit_structure(source)
    assert audit.altloc_groups[0].recommended == "B"
    assert audit.altloc_groups[0].labels == ("A", "B")


def test_model_local_conect_does_not_leak_to_other_models(tmp_path):
    source = tmp_path / "source.pdb"
    atoms = (atom(1, "N"), atom(2, "B1", resname="BJI", resid=2, x=10., record="HETATM"))
    source.write_text("\n".join(("MODEL        1", *atoms, "CONECT    1    2", "ENDMDL", "MODEL        2", *atoms, "ENDMDL", "END")))
    assert len(audit_structure(source).covalent_links) == 1


def test_audit_hash_describes_the_exact_coordinate_bytes_parsed(tmp_path):
    source = tmp_path / "source.pdb"
    original = (atom(1, "CA") + "\nEND\n").encode()
    source.write_bytes(original)
    read_text = Path.read_text
    def changing_read(path, *args, **kwargs):
        value = read_text(path, *args, **kwargs)
        if path == source:
            path.write_bytes(original + b"REMARK concurrently replaced\n")
        return value
    with patch.object(Path, "read_text", changing_read):
        audit = audit_structure(source)
    assert audit.source_sha256 == hashlib.sha256(original).hexdigest()


def test_repeated_model_labels_block_identity_ambiguity(tmp_path):
    source = tmp_path / "source.pdb"
    source.write_text("\n".join(("MODEL        1", atom(1, "CA"), "ENDMDL", "MODEL        1", atom(1, "CA", x=5.), "ENDMDL")))
    assert any("Repeated MODEL" in reason for reason in audit_structure(source).blocked_reasons)
