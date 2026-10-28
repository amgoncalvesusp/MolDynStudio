from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from unittest import mock

import pytest

from core.preparation_orchestrator import PreparationRequest
from core.run_manifest import new_manifest
from core.run_review import (
    build_preparation_review,
    build_run_review,
    setup_review_snapshot,
)
from core.system_prep import SystemPrepParams
from utils.mdp_generator import MDParameters, generate_all_mdp


def request(root: Path, *, ligand: bool = False) -> PreparationRequest:
    return PreparationRequest(
        system=SystemPrepParams(str(root / "protein.pdb"), str(root)),
        ligand_path=str(root / "ligand.mol2") if ligand else None,
        md_parameters=MDParameters(duration_ns=0.002, timestep_fs=1.0,
                                   save_every_ps=0.2, nvt_steps=2000, npt_steps=2000),
    )


def state(preparation: PreparationRequest) -> dict:
    params = preparation.md_parameters
    return {
        "project_dir": preparation.system.work_dir,
        "protein": preparation.system.pdb_path,
        "ligand": preparation.ligand_path or "",
        "force_field": preparation.system.force_field,
        "water_model": preparation.system.water_model,
        "box_type": preparation.system.box_type,
        "box_padding": preparation.system.box_padding_nm,
        "ion_concentration": preparation.system.ion_concentration_m,
        "duration": params.duration_ns, "timestep": params.timestep_fs,
        "temperature": params.temperature_k, "pressure": params.pressure_bar,
        "save_every": params.save_every_ps, "nvt_steps": params.nvt_steps,
        "npt_steps": params.npt_steps, "charge": preparation.net_charge,
        "charge_method": preparation.charge_method,
        "ligand_from_complex": bool(preparation.source_residue_name),
        "source_residue_name": preparation.source_residue_name,
        "excluded_residue_names": ", ".join(preparation.excluded_residue_names),
        "structure_repair_report": preparation.structure_repair_report,
        "structure_repair_report_sha256": preparation.structure_repair_report_sha256,
    }


def manifest(root: Path, *, recorded: bool = True):
    preparation = request(root)
    result = new_manifest(root, "protein", preparation.system.pdb_path, None,
                          "AMBER99SB-ILDN", "TIP3P", "gmx", "moldynstudio", 4, "Auto")
    if recorded:
        result = replace(result, metadata={"preparation_review": build_preparation_review(preparation)})
    for filename, content in generate_all_mdp(preparation.md_parameters).items():
        (root / filename).write_text(content, encoding="utf-8")
    return result


@pytest.mark.parametrize("ligand", [False, True])
def test_submitted_snapshot_matches_visible_scientific_choices(tmp_path, ligand):
    preparation = request(tmp_path, ligand=ligand)
    review = build_preparation_review(preparation)
    assert review["setup_snapshot"] == setup_review_snapshot(state(preparation))
    assert review["system"]["box_padding_nm"] == 1.2
    assert review["md_parameters"]["duration_ns"] == 0.002
    assert "allow_ligand_upload" not in repr(review)
    assert "ligand_protonation_confirmed" not in repr(review)


def test_snapshot_detects_actual_edits_but_ignores_transient_consent_and_hidden_controls(tmp_path):
    initial = state(request(tmp_path))
    scientific = setup_review_snapshot(initial)
    assert setup_review_snapshot({**initial, "allow_ligand_upload": True,
                                  "ligand_protonation_confirmed": True,
                                  "run_production": False, "charge": 7}) == scientific
    assert setup_review_snapshot({**initial, "temperature": 310}) != scientific
    assert setup_review_snapshot({**initial, "protein": str(tmp_path / "other.pdb")}) != scientific
    assert initial == state(request(tmp_path))


def test_charmm_complex_choices_canonicalize_and_preserve_charge(tmp_path):
    preparation = replace(request(tmp_path, ligand=True),
                          system=replace(request(tmp_path).system, force_field="CHARMM36m"),
                          ligand_backend="swissparam_match", source_residue_name="TWB",
                          excluded_residue_names=("GOL", "EDO"), net_charge=-2,
                          allow_ligand_upload=True, ligand_protonation_confirmed=True)
    visible = {**state(preparation), "source_residue_name": "twb",
               "excluded_residue_names": "gol, EDO, gol", "charge_method": "Gasteiger"}
    assert build_preparation_review(preparation)["setup_snapshot"] == setup_review_snapshot(visible)
    assert setup_review_snapshot({**visible, "charge": -1}) != setup_review_snapshot(visible)
    assert setup_review_snapshot({**visible, "source_residue_name": "ABC"}) != setup_review_snapshot(visible)


def test_snapshot_handles_incomplete_drafts_without_inventing_defaults():
    result = setup_review_snapshot({})
    assert result["protein"] == ""
    assert result["duration"] == ""
    assert setup_review_snapshot({"temperature": "invalid"})["temperature"] == "invalid"
    with pytest.raises(TypeError, match="mapping"):
        setup_review_snapshot(None)


def test_draft_paths_preserve_invalid_text_without_raising_from_ui_callbacks():
    invalid = "protein\x00.pdb"
    assert setup_review_snapshot({"protein": invalid})["protein"] == invalid


@pytest.mark.parametrize("error", [ValueError("invalid path"), OSError("unavailable path"), RuntimeError("link loop")])
def test_draft_paths_keep_original_text_when_resolution_is_unavailable(error):
    with mock.patch.object(Path, "resolve", side_effect=error):
        result = setup_review_snapshot({"protein": "unresolved/protein.pdb"})
    assert result["protein"] == "unresolved/protein.pdb"


def test_review_is_immutable_and_reads_all_actual_mdp_files_without_modifying_inputs(tmp_path):
    prepared = manifest(tmp_path)
    before = {name: (tmp_path / name).read_bytes() for name in generate_all_mdp(request(tmp_path).md_parameters)}
    result = build_run_review(prepared, tmp_path)
    rows = dict(result.rows)
    assert rows["Force field"] == "AMBER99SB-ILDN"
    assert rows["Box"] == "Dodecahedron; padding 1.2 nm"
    assert rows["Salt concentration"] == "0.15 M"
    assert "2 ps" in rows["Production"]
    assert "1 fs" in rows["Production"]
    assert rows["Trajectory output interval"] == "0.2 ps"
    assert "execution test" in result.protocol_label.casefold()
    assert "equilibration" in result.protocol_label.casefold()
    assert {name for name, _text in result.mdp_files} == set(before)
    assert {name: text.encode() for name, text in result.mdp_files} == before
    assert before == {name: (tmp_path / name).read_bytes() for name in before}
    with pytest.raises(FrozenInstanceError):
        result.protocol_label = "changed"


def test_actual_mdp_values_take_priority_over_recorded_requested_protocol(tmp_path):
    prepared = manifest(tmp_path)
    updated = replace(request(tmp_path).md_parameters, duration_ns=0.1, timestep_fs=2.0)
    (tmp_path / "md.mdp").write_text(generate_all_mdp(updated)["md.mdp"], encoding="utf-8")
    result = build_run_review(prepared, tmp_path)
    assert "100 ps" in dict(result.rows)["Production"]
    assert "2 fs" in dict(result.rows)["Production"]
    assert "execution test" not in result.protocol_label.casefold()


def test_legacy_preparation_reports_unrecorded_fields_and_retains_actual_mdp(tmp_path):
    result = build_run_review(manifest(tmp_path, recorded=False), tmp_path)
    rows = dict(result.rows)
    assert rows["Box"] == "Not recorded"
    assert rows["Salt concentration"] == "Not recorded"
    assert "2 ps" in rows["Production"]
    assert any("legacy" in issue.casefold() for issue in result.issues)


@pytest.mark.parametrize("content", ["placeholder\n", "nsteps = inf\ndt = 0.002\n", "nsteps = -5\ndt = 0.002\n"])
def test_incomplete_mdp_does_not_fabricate_duration_or_block_legacy_reading(tmp_path, content):
    prepared = manifest(tmp_path)
    (tmp_path / "md.mdp").write_bytes(content.encode("utf-8"))
    result = build_run_review(prepared, tmp_path)
    assert dict(result.rows)["Production"] == "Not recorded"
    assert dict(result.mdp_files)["md.mdp"] == content


def test_missing_and_invalid_mdp_files_are_actionable_issues(tmp_path):
    prepared = manifest(tmp_path)
    (tmp_path / "nvt.mdp").unlink()
    (tmp_path / "md.mdp").write_bytes(b"\xff\xfe\xff")
    result = build_run_review(prepared, tmp_path)
    assert any("nvt.mdp" in issue for issue in result.issues)
    assert any("md.mdp" in issue for issue in result.issues)
    assert {name for name, _ in result.mdp_files} == {"em.mdp", "npt.mdp"}


def test_review_rejects_cross_project_path_and_bounds_large_mdp_files(tmp_path):
    prepared = manifest(tmp_path)
    with pytest.raises(ValueError, match="project"):
        build_run_review(prepared, tmp_path / "different")
    (tmp_path / "md.mdp").write_bytes(b";" * (1024 * 1024 + 1))
    result = build_run_review(prepared, tmp_path)
    assert any("md.mdp" in issue and "1 MiB" in issue for issue in result.issues)


def test_review_does_not_follow_mdp_links_or_mutate_nested_metadata(tmp_path):
    prepared = manifest(tmp_path)
    original = build_preparation_review(request(tmp_path))
    prepared.metadata["preparation_review"] = original
    with mock.patch.object(Path, "is_symlink", return_value=True):
        result = build_run_review(prepared, tmp_path)
    assert result.mdp_files == ()
    assert len(result.issues) == 4
    assert prepared.metadata["preparation_review"] == original


def test_malformed_optional_metadata_is_unknown_and_never_uses_defaults(tmp_path):
    prepared = manifest(tmp_path)
    result = build_run_review(replace(prepared, metadata={"preparation_review": ["wrong"]}), tmp_path)
    assert dict(result.rows)["Box"] == "Not recorded"
    result = build_run_review(replace(prepared, metadata={"preparation_review": {"system": {"box_padding_nm": "bad"}}}), tmp_path)
    assert dict(result.rows)["Box"] == "Not recorded"
