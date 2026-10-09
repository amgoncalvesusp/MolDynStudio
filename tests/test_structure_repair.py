import pytest
import json

from core.structure_audit import audit_structure
from core.structure_repair import RepairChoices, apply_repair_plan, build_repair_plan, verify_repair_result
from tests.test_structure_audit import atom


def complete_ala():
    return [atom(index, name, x=float(index) * 1.4) for index, name in enumerate(("N", "CA", "C", "O", "CB"), 1)]


def write_source(tmp_path, rows):
    source = tmp_path / "source.pdb"
    source.write_text("\n".join((*rows, "END")) + "\n")
    return source


def test_ready_candidate_verification_and_hash_guards(tmp_path):
    source = write_source(tmp_path, complete_ala())
    result = apply_repair_plan(build_repair_plan(audit_structure(source), RepairChoices()), tmp_path / "out")
    assert result.ready
    verify_repair_result(result)
    report = json.loads(result.report_path.read_text())
    assert len(report["atom_mapping"]) == 5
    assert all(row["origin"] == "observed" for row in report["atom_mapping"])
    result.candidate_path.write_text(result.candidate_path.read_text() + "REMARK changed\n")
    with pytest.raises(ValueError, match="changed"):
        verify_repair_result(result)


def test_source_change_after_generation_rejected_at_adoption(tmp_path):
    source = write_source(tmp_path, complete_ala())
    result = apply_repair_plan(build_repair_plan(audit_structure(source), RepairChoices()), tmp_path / "out")
    source.write_text(source.read_text() + "REMARK changed\n")
    with pytest.raises(ValueError, match="changed"):
        verify_repair_result(result)


def test_distinct_collision_needs_explicit_atom_line_resolution(tmp_path):
    source = write_source(tmp_path, [atom(1, "CA"), atom(2, "C1", resname="LIG", x=1.1, record="HETATM")])
    audit = audit_structure(source)
    plan = build_repair_plan(audit, RepairChoices())
    assert not plan.can_apply
    assert build_repair_plan(audit, RepairChoices(delete_atom_lines=(2,))).can_apply
    assert len(audit.atoms) == 2


def test_two_models_require_explicit_selection(tmp_path):
    source = write_source(tmp_path, ["MODEL        1", *complete_ala(), "ENDMDL", "MODEL        2", *complete_ala(), "ENDMDL"])
    audit = audit_structure(source)
    assert audit.model_ids == (1, 2)
    assert not build_repair_plan(audit, RepairChoices()).can_apply
    result = apply_repair_plan(build_repair_plan(audit, RepairChoices(model_id=2)), tmp_path / "out")
    assert result.ready
    report = json.loads(result.report_path.read_text())
    assert all(row["source_identity"]["model_id"] == 2 for row in report["atom_mapping"])


def test_failed_backend_saved_not_ready_without_partial_backend_output(tmp_path):
    source = write_source(tmp_path, complete_ala()[:-1])
    def fail(input_path, output_path, **kwargs):
        output_path.write_text("broken output")
        raise ValueError("modeling failed")
    result = apply_repair_plan(build_repair_plan(audit_structure(source), RepairChoices(add_missing_heavy_atoms=True)), tmp_path / "out", backend=fail)
    assert not result.ready
    assert "broken output" not in result.candidate_path.read_text()
    assert any("modeling failed" in reason for reason in result.blocked_reasons)
    with pytest.raises(ValueError, match="not coordinate-ready"):
        verify_repair_result(result)


def test_default_preserves_full_covalent_complex_fixed_width_conect(tmp_path):
    rows = complete_ala()
    rows[0] = atom(99998, "N", x=1.4)
    rows.append(atom(99999, "B1", resname="BJI", resid=2, x=12., record="HETATM"))
    rows.append("CONECT9999899999")
    source = write_source(tmp_path, rows)
    result = apply_repair_plan(build_repair_plan(audit_structure(source), RepairChoices()), tmp_path / "out")
    assert result.ready
    assert result.md_requires_parameters
    assert "BJI" in result.candidate_path.read_text()
    assert "CONECT9999899999" in result.candidate_path.read_text()
    verify_repair_result(result)


def test_explicit_apo_prunes_connectivity_but_retains_modres_polymer(tmp_path):
    modres = "MODRES 1ERM BHD A  214  ASP  ASPARTATE BETA HYDROXYLATION"
    rows = [modres, *[atom(i, name, 214, resname="BHD", x=i * 1.4, record="HETATM") for i, name in enumerate(("N", "CA", "C", "O", "CB", "CG", "OD1", "OD2", "OB"), 1)],
            atom(10, "B1", resid=300, resname="BJI", x=20., record="HETATM"), "CONECT    1   10"]
    source = write_source(tmp_path, rows)
    audit = audit_structure(source)
    assert audit.residues[0].polymer
    result = apply_repair_plan(build_repair_plan(audit, RepairChoices(remove_heterogens=True)), tmp_path / "out")
    assert "BHD" in result.candidate_path.read_text()
    assert "BJI" not in result.candidate_path.read_text()
    assert "CONECT" not in result.candidate_path.read_text()
    key = audit.modified_residues[0].residue_id
    converted = apply_repair_plan(build_repair_plan(audit, RepairChoices(normalize_modres=(key,))), tmp_path / "out")
    transformed = audit_structure(converted.candidate_path)
    asp = [record for record in transformed.atoms if record.identity.residue_number == "214"]
    assert {record.identity.residue_name for record in asp} == {"ASP"}
    assert len(asp) == 8
    assert all(record.identity.atom_name != "OB" for record in asp)


def test_modeling_preserves_observed_atoms_and_marks_added_atoms(tmp_path):
    source = write_source(tmp_path, complete_ala()[:-1])
    def add_cb(input_path, output_path, **kwargs):
        output_path.write_text("\n".join((*complete_ala(), "END")))
        return {"backend_versions": {"test": "1"}}
    result = apply_repair_plan(build_repair_plan(audit_structure(source), RepairChoices(add_missing_heavy_atoms=True)), tmp_path / "out", backend=add_cb)
    assert result.ready
    mapping = json.loads(result.report_path.read_text())["atom_mapping"]
    assert sum(record["origin"] == "modeled" for record in mapping) == 1


def test_backend_moving_observed_atom_blocks_ready(tmp_path):
    source = write_source(tmp_path, complete_ala())
    def moved(input_path, output_path, **kwargs):
        rows = complete_ala()
        rows[0] = atom(1, "N", x=0.)
        output_path.write_text("\n".join((*rows, "END")))
    result = apply_repair_plan(build_repair_plan(audit_structure(source), RepairChoices(add_missing_heavy_atoms=True)), tmp_path / "out", backend=moved)
    assert not result.ready
    assert any("moved" in reason for reason in result.blocked_reasons)


def test_segment_ter_records_remain_between_same_chain_fragments(tmp_path):
    later = [atom(index + 5, name, resid=10, x=15. + index * 1.4)
             for index, name in enumerate(("N", "CA", "C", "O", "CB"), 1)]
    source = write_source(tmp_path, [*complete_ala(), "TER", *later, "TER"])
    result = apply_repair_plan(build_repair_plan(audit_structure(source), RepairChoices()), tmp_path / "out")
    text = result.candidate_path.read_text()
    assert text.count("TER\n") == 2
    assert text.index("TER\n") < text.index(later[0])


def test_duplicate_serials_block_reviewed_plan(tmp_path):
    rows = complete_ala()
    rows[-1] = atom(1, "CB", x=7.)
    source = write_source(tmp_path, rows)
    plan = build_repair_plan(audit_structure(source), RepairChoices())
    assert not plan.can_apply
    assert any("serial" in reason for reason in plan.blocked_reasons)


def link(first_name, first_res, first_number, second_name, second_res, second_number):
    row = list(" " * 80)
    row[:6] = "LINK  "
    for start, end, text in ((12,16,first_name.rjust(4)), (17,20,first_res), (21,22,"A"),
                             (22,26,str(first_number).rjust(4)), (42,46,second_name.rjust(4)),
                             (47,50,second_res), (51,52,"A"), (52,56,str(second_number).rjust(4))):
        row[start:end] = text
    return "".join(row)


def test_deleted_atom_prunes_link_endpoint(tmp_path):
    bond = link("CB", "ALA", 1, "B1", "BJI", 2)
    source = write_source(tmp_path, [bond, *complete_ala(), atom(6, "B1", resid=2, resname="BJI", x=12., record="HETATM")])
    audit = audit_structure(source)
    result = apply_repair_plan(build_repair_plan(audit, RepairChoices(delete_atom_lines=(7,))), tmp_path / "out")
    assert "LINK" not in result.candidate_path.read_text()


def test_parent_conversion_updates_surviving_link_identity(tmp_path):
    rows = ["MODRES 1ERM BHD A  214  ASP  ASPARTATE BETA HYDROXYLATION",
            link("CG", "BHD", 214, "B1", "BJI", 300),
            *[atom(i, name, 214, resname="BHD", x=i * 1.4, record="HETATM")
              for i, name in enumerate(("N", "CA", "C", "O", "CB", "CG", "OD1", "OD2", "OB"), 1)],
            atom(10, "B1", resid=300, resname="BJI", x=20., record="HETATM")]
    source = write_source(tmp_path, rows)
    audit = audit_structure(source)
    result = apply_repair_plan(build_repair_plan(audit, RepairChoices(normalize_modres=(audit.modified_residues[0].residue_id,))), tmp_path / "out")
    recorded = next(row for row in result.candidate_path.read_text().splitlines() if row.startswith("LINK"))
    assert recorded[17:20] == "ASP"
    assert result.ready


def test_connectivity_from_unselected_model_is_not_imported(tmp_path):
    atoms = [*complete_ala(), atom(6, "B1", resid=2, resname="BJI", x=12., record="HETATM")]
    source = write_source(tmp_path, ["MODEL        1", *atoms, "ENDMDL", "MODEL        2", *atoms, "CONECT    1    6", "ENDMDL"])
    audit = audit_structure(source)
    result = apply_repair_plan(build_repair_plan(audit, RepairChoices(model_id=1)), tmp_path / "out")
    assert result.ready
    assert "CONECT" not in result.candidate_path.read_text()
    assert not json.loads(result.report_path.read_text())["remaining_covalent_links"]


def test_unknown_source_conect_endpoint_is_not_silently_removed(tmp_path):
    source = write_source(tmp_path, [*complete_ala(), "CONECT    1 9999"])
    result = apply_repair_plan(build_repair_plan(audit_structure(source), RepairChoices()), tmp_path / "out")
    assert not result.ready
    assert "CONECT" in result.candidate_path.read_text()
    assert any("Dangling CONECT" in reason for reason in result.blocked_reasons)


def test_changed_repair_report_is_rejected_at_adoption(tmp_path):
    source = write_source(tmp_path, complete_ala())
    result = apply_repair_plan(build_repair_plan(audit_structure(source), RepairChoices()), tmp_path / "out")
    report = json.loads(result.report_path.read_text())
    report["changes"] = ["altered scientific provenance"]
    result.report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="report.*changed"):
        verify_repair_result(result)


def test_report_change_during_candidate_verification_is_rejected(tmp_path, monkeypatch):
    source = write_source(tmp_path, complete_ala())
    result = apply_repair_plan(build_repair_plan(audit_structure(source), RepairChoices()), tmp_path / "out")
    def audit_and_replace_report(path):
        audited = audit_structure(path)
        report = json.loads(result.report_path.read_text())
        result.report_path.write_text(json.dumps({**report, "changes": ["concurrent replacement"]}))
        return audited
    monkeypatch.setattr("core.structure_repair.audit_structure", audit_and_replace_report)
    with pytest.raises(ValueError, match="report.*changed"):
        verify_repair_result(result)


def test_unknown_source_link_residue_is_preserved_and_blocks_ready(tmp_path):
    source = write_source(tmp_path, [link("CB", "ALA", 1, "B1", "BJI", 99), *complete_ala()])
    result = apply_repair_plan(build_repair_plan(audit_structure(source), RepairChoices()), tmp_path / "out")
    assert not result.ready
    assert "LINK" in result.candidate_path.read_text()
    assert any("Dangling LINK" in reason for reason in result.blocked_reasons)


@pytest.mark.parametrize("selected_label", ("A", "B"))
def test_altloc_specific_link_never_migrates_to_other_conformer(tmp_path, selected_label):
    bond = link("CB", "ALA", 1, "B1", "BJI", 2)
    bond = bond[:16] + "A" + bond[17:]
    rows = [bond, *complete_ala()[:-1], atom(5, "CB", x=7., altloc="A", occupancy=.5),
            atom(6, "CB", x=7.2, altloc="B", occupancy=.5),
            atom(7, "B1", resid=2, resname="BJI", x=12., record="HETATM")]
    source = write_source(tmp_path, rows)
    audit = audit_structure(source)
    assert len(audit.covalent_links) == 1
    assert audit.covalent_links[0].polymer_atom.altloc == "A"
    choices = RepairChoices(selected_altlocs=((audit.altloc_groups[0].residue_id, selected_label),))
    result = apply_repair_plan(build_repair_plan(audit, choices), tmp_path / "out")
    assert result.ready
    candidate_links = [row for row in result.candidate_path.read_text().splitlines() if row.startswith("LINK")]
    assert bool(candidate_links) is (selected_label == "A")
    if candidate_links:
        assert candidate_links[0][16] == " "


@pytest.mark.parametrize("endpoint_label,ready", (("C", False), ("", True)))
def test_link_unknown_altloc_blocks_but_unspecified_altloc_tracks_selected_atom(tmp_path, endpoint_label, ready):
    bond = link("CB", "ALA", 1, "B1", "BJI", 2)
    bond = bond[:16] + (endpoint_label or " ") + bond[17:]
    source = write_source(tmp_path, [bond, *complete_ala()[:-1], atom(5, "CB", x=7., altloc="A"),
                                   atom(6, "CB", x=7.2, altloc="B"),
                                   atom(7, "B1", resid=2, resname="BJI", x=12., record="HETATM")])
    audit = audit_structure(source)
    assert len(audit.covalent_links) == (0 if endpoint_label else 2)
    choices = RepairChoices(selected_altlocs=((audit.altloc_groups[0].residue_id, "B"),))
    result = apply_repair_plan(build_repair_plan(audit, choices), tmp_path / "out")
    assert result.ready is ready
    assert "LINK" in result.candidate_path.read_text()
    if not ready:
        assert any("Dangling LINK" in reason for reason in result.blocked_reasons)


def test_explicit_gap_is_reindexed_after_prior_residue_deletion(tmp_path):
    sequence = ("ALA", "CYS", "ASP", "PHE", "GLY", "HIS", "ILE", "LYS")
    source = write_source(tmp_path, [atom(i, "CA", resid=i, resname=name, x=i*3.) for i,name in enumerate(sequence,1)])
    fasta = tmp_path / "sequence.fasta"
    fasta.write_text(">chain_A\nACDEFGHIK\n")
    audit = audit_structure(source, fasta, chain_id="A")
    gap = audit.sequence_gaps[0]
    choices = RepairChoices(fill_gap_ids=(gap.gap_id,), delete_atom_lines=(1,))
    captured = []
    def record(input_path, output_path, **kwargs):
        captured.append(kwargs)
        output_path.write_bytes(input_path.read_bytes())
    apply_repair_plan(build_repair_plan(audit, choices), tmp_path / "out", backend=record)
    assert captured[0]["missing_residues"][0]["insert_before"] == 2
    assert captured[0]["missing_residues"][0]["resnames"] == ("GLU",)
    blocked = build_repair_plan(audit, RepairChoices(fill_gap_ids=(gap.gap_id,), delete_atom_lines=(3,)))
    assert any("anchors" in reason for reason in blocked.blocked_reasons)


def test_original_preserved_duplicate_removed_transactionally(tmp_path):
    source = tmp_path / "source.pdb"
    row = atom(1, "CA")
    source.write_text(row + "\n" + row + "\nEND\n")
    original = source.read_bytes()
    plan = build_repair_plan(audit_structure(source), RepairChoices())
    result = apply_repair_plan(plan, tmp_path / "repairs")
    assert source.read_bytes() == original
    assert result.candidate_path.is_file()
    assert result.report_path.is_file()
    assert result.candidate_path.read_text().count(row) == 1


def test_source_changed_after_plan_blocks_apply(tmp_path):
    source = tmp_path / "source.pdb"
    source.write_text(atom(1, "CA") + "\nEND\n")
    plan = build_repair_plan(audit_structure(source), RepairChoices())
    source.write_text(atom(1, "CA", x=2.) + "\nEND\n")
    with pytest.raises(ValueError, match="changed"):
        apply_repair_plan(plan, tmp_path / "repairs")
