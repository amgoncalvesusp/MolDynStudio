"""Regression tests for scientific input/output integrity."""

from pathlib import Path

import pytest

from analysis.mmpbsa import MMPBSAOptions, build_input_file
from analysis.qc import parse_numeric_series, summarize_series
from analysis.qc_pipeline import QCPipelineInputs, build_qc_tasks


def test_pb_uses_pb_ionic_strength_not_gb_keywords():
    result = build_input_file(MMPBSAOptions(method="PBSA"))
    assert "&pb" in result
    assert "istrng=0.150" in result
    assert "igb=" not in result
    assert "saltcon=" not in result


@pytest.mark.parametrize("options", [
    MMPBSAOptions(startframe=5, endframe=1),
    MMPBSAOptions(interval=0),
    MMPBSAOptions(saltcon=float("nan")),
    MMPBSAOptions(entropy="Quasi-harmonic"),
    MMPBSAOptions(method="invalid"),
])
def test_invalid_or_unsupported_mmpbsa_options_fail(options):
    with pytest.raises(ValueError):
        build_input_file(options)


def test_qc_time_commands_all_request_ns():
    tasks = build_qc_tasks(QCPipelineInputs("a.tpr", "a.xtc", "out", "a.edr"))
    for task in tasks:
        if task.name in {"RMSD", "Radius of gyration", "SASA"}:
            assert "-tu ns" in " ".join(task.command)
        elif task.name != "RMSF":
            # gmx energy has no -tu option; its XVG axis is normalized on import.
            assert "-tu" not in " ".join(task.command)


def test_xvg_ps_is_normalized_to_ns(tmp_path: Path):
    source = tmp_path / "rmsd.xvg"
    source.write_text('@ xaxis label "Time (ps)"\n0 1\n1000 2\n2000 3\n')
    result = parse_numeric_series(source)
    assert result.time == (0, 1, 2)
    assert result.x_unit == "ns"


def test_qc_rejects_nonfinite_instead_of_hiding_bad_frames(tmp_path: Path):
    source = tmp_path / "rmsd.xvg"
    source.write_text("0 1\n1 nan\n2 1\n3 1\n")
    with pytest.raises(ValueError, match="finite"):
        parse_numeric_series(source)
    assert summarize_series("RMSD", [0, 1, 2, 3], [1, float("nan"), 1, 1]).status == "FAIL"


def test_qc_rejects_mismatched_or_restarted_series():
    assert summarize_series("RMSD", [0, 1, 2, 3], [1, 1, 1]).status == "FAIL"
    assert summarize_series("RMSD", [0, 2, 1], [1, 1, 1]).status == "FAIL"


@pytest.mark.parametrize("column,unit", [("rmsd_nm", "nm"), ("rg_nm", "nm"), ("sasa_nm2", "nm2")])
def test_python_csv_units_survive_dashboard_import(tmp_path, column, unit):
    source = tmp_path/"rmsd.csv"
    source.write_text(f"time_ns,{column},frame\n0,1,0\n1,2,1\n2,3,2\n")
    result = parse_numeric_series(source)
    assert result.x_unit == "ns"
    assert result.value_unit == unit


def test_gromacs_grace_area_unit_matches_python_unit(tmp_path):
    source=tmp_path/"sasa.xvg"
    source.write_text('@ yaxis label "Area (nm\\S2\\N)"\n0 1\n1 2\n2 3\n')
    assert parse_numeric_series(source).value_unit=="nm2"


def test_short_residue_profile_is_not_rejected_as_short_time_series():
    assert summarize_series("RMSF",[1,2],[.1,.2]).status=="OK"
