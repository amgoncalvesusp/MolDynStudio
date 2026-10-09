"""Analysis exports must preserve provenance and distinguish partial failures."""

import csv
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from analysis.base import AnalysisResult
from analysis.execution import AnalysisInputs, execute_analyses


@pytest.fixture
def inputs(tmp_path):
    top = tmp_path / "system.pdb"
    traj = tmp_path / "trajectory.xtc"
    top.write_text("input topology")
    traj.write_bytes(b"trajectory")
    return AnalysisInputs(str(top), str(traj), str(tmp_path / "results"), ("RMSD",), prepared=True)


def test_exports_values_with_units_and_hashed_provenance(inputs):
    result = AnalysisResult("RMSD", [[0, 0, 1], [1, 1000, 2]],
                            columns=("frame", "time_ps", "rmsd_angstrom"))
    with patch("analysis.execution.run_backend", return_value=result):
        manifest = execute_analyses(inputs)
    content = json.loads(Path(manifest).read_text())
    assert content["status"] == "complete"
    assert len(content["inputs"]["trajectory"]["sha256"]) == 64
    output = content["results"][0]
    assert len(output["sha256"]) == 64
    with Path(output["path"]).open(newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows[0] == ["time_ns", "rmsd_nm", "frame"]
    assert rows[2] == ["1.0", "0.2", "1"]


def test_repeated_runs_use_new_folders(inputs):
    result = AnalysisResult("RMSD", [[0, 0, 1]], columns=("frame", "time_ps", "rmsd_angstrom"))
    with patch("analysis.execution.run_backend", return_value=result):
        first, second = execute_analyses(inputs), execute_analyses(inputs)
    assert Path(first).parent != Path(second).parent


def test_failed_analysis_is_recorded_without_fake_csv_success(inputs):
    with patch("analysis.execution.run_backend", side_effect=ValueError("empty selection")):
        manifest = execute_analyses(inputs)
    content = json.loads(Path(manifest).read_text())
    assert content["status"] == "failed"
    assert content["results"][0]["error"] == "empty selection"
    assert not list(Path(manifest).parent.glob("*.csv"))


def test_nonfinite_outputs_are_rejected(inputs):
    result = AnalysisResult("RMSD", [[0, 0, float("nan")]], columns=("frame", "time_ps", "rmsd_angstrom"))
    with patch("analysis.execution.run_backend", return_value=result):
        manifest = execute_analyses(inputs)
    assert json.loads(Path(manifest).read_text())["status"] == "failed"


def test_prepared_trajectory_confirmation_required(inputs):
    from dataclasses import replace
    with pytest.raises(ValueError, match="periodic"):
        execute_analyses(replace(inputs, prepared=False))
