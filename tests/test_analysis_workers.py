"""Behavioral checks for real job lifecycles and fresh artifact validation."""

import json
import os
import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication

from analysis.qc_pipeline import QCGromacsWorker, QCPipelineInputs
from analysis.mmpbsa import MMPBSAAnalysis, MMPBSAOptions
from tabs.analysis_tab import AnalysisTab


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def qc_inputs(tmp_path):
    top, traj = tmp_path/"system.tpr", tmp_path/"run.xtc"
    top.touch()
    traj.touch()
    return QCPipelineInputs(str(top), str(traj), str(tmp_path/"results"), conda_env="chosen-env")


def test_qc_requires_every_fresh_numeric_output(tmp_path, app):
    inputs = qc_inputs(tmp_path)
    results = []
    worker = QCGromacsWorker(inputs)
    worker.completed.connect(lambda *args: results.append(args))
    def popen(command, cwd, env_name):
        assert env_name == "chosen-env"
        args = shlex.split(command[2]) if command[0] == "bash" else command
        filename = args[args.index("-o")+1]
        if filename != "rmsf.xvg":
            (Path(cwd)/filename).write_text('@ xaxis label "Time (ps)"\n0 1\n1 2\n2 3\n')
        return SimpleNamespace(stdout=iter(["done"]), wait=lambda: 0)
    with patch("analysis.qc_pipeline.wsl_bridge.popen", side_effect=popen):
        worker.run()
    assert results and results[0][0] is False
    assert "validation failed" in results[0][2]
    assert len(list(Path(inputs.output_dir).glob("qc_*"))) == 1


def test_pending_qt_results_hold_busy_and_new_input_clears_preparation(app):
    tab = AnalysisTab()
    tab.fields["prepared"].setChecked(True)
    tab.fields["trajectory"].setText("new trajectory.xtc")
    assert not tab.fields["prepared"].isChecked()
    tab._analysis_busy = True
    tab._analysis_worker = SimpleNamespace(isRunning=lambda: False)
    assert tab.is_busy()
    tab._analysis_thread_finished()
    assert not tab.is_busy()
    tab._analysis_worker = None
    tab.close()


def test_mmpbsa_spawn_failure_finalizes_manifest(tmp_path):
    for filename in ("system.tpr", "run.xtc", "topol.top", "index.ndx"):
        (tmp_path/filename).write_text("fixture")
    with patch("analysis.mmpbsa.wsl_bridge.run", side_effect=OSError("missing backend")):
        with pytest.raises(OSError, match="missing backend"):
            MMPBSAAnalysis().run(str(tmp_path/"system.tpr"), str(tmp_path/"run.xtc"), "1", "2", MMPBSAOptions(), str(tmp_path))
    paths = list(tmp_path.glob("mmpbsa_*/mmpbsa_manifest.json"))
    assert len(paths) == 1
    result = json.loads(paths[0].read_text())
    assert result["status"] == "failed"
    assert result["error"] == "missing backend"


def test_mmpbsa_requires_fresh_results_even_when_exit_zero(tmp_path):
    from core import wsl_bridge
    for filename in ("system.tpr", "run.xtc", "topol.top", "index.ndx"):
        (tmp_path/filename).write_text("fixture")
    args=(str(tmp_path/"system.tpr"),str(tmp_path/"run.xtc"),"1","2",MMPBSAOptions(),str(tmp_path))
    completed=SimpleNamespace(returncode=0,stdout="finished",stderr="")
    with patch("analysis.mmpbsa.wsl_bridge.run",return_value=completed):
        result=MMPBSAAnalysis().run(*args)
    assert result.data["success"] is False
    def run(command, **kwargs):
        path=Path(wsl_bridge.wsl_to_win(command[command.index("-o")+1]))
        path.write_text("DELTA TOTAL -1.0")
        return completed
    with patch("analysis.mmpbsa.wsl_bridge.run",side_effect=run):
        result=MMPBSAAnalysis().run(*args)
    assert result.data["success"] is True


@pytest.mark.parametrize("missing_term", [True, False])
def test_optional_energy_absence_is_explicit_partial_but_other_errors_fail(tmp_path, app, missing_term):
    from dataclasses import replace
    inputs=qc_inputs(tmp_path)
    energy=tmp_path/"md.edr"
    energy.touch()
    inputs=replace(inputs,energy=str(energy))
    results=[]
    worker=QCGromacsWorker(inputs)
    worker.completed.connect(lambda *args: results.append(args))
    def popen(command,cwd,**kwargs):
        args=shlex.split(command[2]) if command[0]=="bash" else command
        filename=args[args.index("-o")+1]
        if filename=="density.xvg":
            output=["String 'Density' does not match anything", "No energy terms selected"] if missing_term else ["Fatal error: corrupted EDR"]
            return SimpleNamespace(stdout=iter(output),wait=lambda:1)
        (Path(cwd)/filename).write_text('@ xaxis label "Time (ps)"\n0 1\n1 2\n2 3\n')
        return SimpleNamespace(stdout=iter(["done"]),wait=lambda:0)
    with patch("analysis.qc_pipeline.wsl_bridge.popen",side_effect=popen):
        worker.run()
    assert results[0][0] is missing_term
    if missing_term:
        assert len(results[0][1])==8
        assert "Partial energy coverage" in results[0][2]
        manifest=json.loads(next(Path(inputs.output_dir).glob("qc_*/qc_commands.json")).read_text())
        assert manifest["status"]=="partial"
        assert manifest["unavailable_energy_terms"][0]["name"]=="Density"
