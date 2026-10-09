"""Preparation boundaries for explicit CHARMM ligand workflows."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.preparation_orchestrator import PreparationError, PreparationOrchestrator
from core.run_manifest import load_manifest
from tests.test_preparation_orchestrator import MaterializingRunner, request
from tests.test_crystal_water import source as water_source, oxygen, fake_builder
from utils.topology_builder import LigandTopologyArtifacts


def charmm_request(root, **changes):
    original = request(root, "ligand.mol2")
    return replace(original, system=replace(original.system, force_field="CHARMM36m"),
                   ligand_backend="swissparam_match", ligand_protonation_confirmed=True, **changes)


def package(root, count=1, charge=0):
    folder = root / "ligand"
    folder.mkdir(exist_ok=True)
    gro, itp, prm, provenance = (folder / name for name in ("ligand.gro", "ligand.itp", "parameters.itp", "provenance.json"))
    atoms = [f"{i:5d}{'TWB':<5}{'C1':>5}{i:5d}{i * .1:8.3f}{.2:8.3f}{.3:8.3f}" for i in range(1, count + 1)]
    gro.write_text("Ligands\n" + str(count) + "\n" + "\n".join(atoms) + "\n1 1 1\n")
    itp.write_text(f"[ moleculetype ]\nTWB 3\n[ atoms ]\n1 CT 1 TWB C1 1 {charge} 12.01\n")
    prm.write_text("[ atomtypes ]\nCT 6 12.01 0 A .3 .2\n")
    provenance.write_text(json.dumps({"method": "SwissParam MATCH", "session": "123"}))
    return LigandTopologyArtifacts(gro, itp, parameter_includes=(prm,), provenance_path=provenance, molecule_count=count)


def run_with_backend(req, backend, runner=None, validator=None):
    with patch("core.preparation_orchestrator.stage_charmm36_force_field"):
        return PreparationOrchestrator(req, runner=runner or MaterializingRunner(),
            structure_validator=validator or (lambda _: None), charmm_ligand_preparer=backend).run()


def test_charmm_uses_explicit_backend_passes_offline_permission_and_persists_evidence(tmp_path):
    req = charmm_request(tmp_path)
    artifacts = package(tmp_path)
    backend, runner = Mock(return_value=artifacts), MaterializingRunner()
    result = run_with_backend(req, backend, runner)
    assert not any(command.argv[0] == "acpype" for command in runner.commands)
    assert backend.call_args.kwargs["allow_upload"] is False
    assert backend.call_args.kwargs["ligand_copies"] == ()
    metadata = load_manifest(result.manifest).metadata["ligand_parameterization"]
    assert metadata["backend"] == "swissparam_match"
    assert metadata["molecule_count"] == 1
    assert metadata["provenance_sha256"]
    assert str(artifacts.ligand_itp.resolve()) in metadata["output_sha256"]
    assert "parameters.itp" in result.topology.read_text()


@pytest.mark.parametrize("changes,match", [
    ({"ligand_protonation_confirmed": False}, "protonation"),
    ({"ligand_backend": "unknown"}, "backend"),
])
def test_invalid_charmm_choices_fail_before_any_external_call(tmp_path, changes, match):
    req = replace(charmm_request(tmp_path), **changes)
    backend, runner = Mock(), MaterializingRunner()
    with pytest.raises(PreparationError, match=match):
        run_with_backend(req, backend, runner)
    backend.assert_not_called()
    assert not runner.commands


def test_match_cannot_be_combined_with_amber(tmp_path):
    req = charmm_request(tmp_path)
    req = replace(req, system=replace(req.system, force_field="AMBER99SB-ILDN"))
    backend, runner = Mock(), MaterializingRunner()
    with pytest.raises(PreparationError, match="CHARMM"):
        run_with_backend(req, backend, runner)
    backend.assert_not_called()
    assert not runner.commands


def test_covalent_gate_precedes_backend(tmp_path):
    backend, runner = Mock(), MaterializingRunner()
    def reject(_):
        raise ValueError("Covalent complex requires custom topology")
    with pytest.raises(PreparationError, match="Covalent"):
        run_with_backend(charmm_request(tmp_path), backend, runner, reject)
    backend.assert_not_called()
    assert not runner.commands


def test_multiple_copies_have_exact_topology_count(tmp_path):
    req = charmm_request(tmp_path)
    second = tmp_path / "copy2.mol2"
    second.write_text("second pose")
    req = replace(req, ligand_copies=(str(second),))
    backend = Mock(return_value=package(tmp_path, count=2))
    result = run_with_backend(req, backend)
    assert backend.call_args.kwargs["ligand_copies"] == (str(second),)
    assert "TWB                2" in result.topology.read_text()


@pytest.mark.parametrize("count,charge,match", [(2, 0, "count"), (1, 1, "charge")])
def test_parameter_package_mismatch_blocks_solvent_commands(tmp_path, count, charge, match):
    req = charmm_request(tmp_path)
    runner = MaterializingRunner()
    with pytest.raises(PreparationError, match=match):
        run_with_backend(req, Mock(return_value=package(tmp_path, count, charge)), runner)
    assert not any(command.argv[1] in {"editconf", "solvate", "grompp", "genion"} for command in runner.commands)


def test_backend_failure_never_falls_back_to_acpype(tmp_path):
    runner = MaterializingRunner()
    with pytest.raises(PreparationError, match="MATCH failed"):
        run_with_backend(charmm_request(tmp_path), Mock(side_effect=ValueError("MATCH failed")), runner)
    assert not any(command.argv[0] == "acpype" for command in runner.commands)


def test_parameter_artifact_modified_during_preparation_is_rejected(tmp_path):
    req = charmm_request(tmp_path)
    artifacts = package(tmp_path)
    class CorruptingRunner(MaterializingRunner):
        def run(self, command, on_output=None):
            result = super().run(command, on_output)
            if command.argv[1] == "genion":
                artifacts.ligand_itp.write_text(artifacts.ligand_itp.read_text() + "; altered\n")
            return result
    with pytest.raises(PreparationError, match="changed"):
        run_with_backend(req, Mock(return_value=artifacts), CorruptingRunner())


def test_protein_repreparation_removes_old_ligand_evidence(tmp_path):
    req = charmm_request(tmp_path)
    result = run_with_backend(req, Mock(return_value=package(tmp_path)))
    run_with_backend(replace(req, ligand_path=None), Mock())
    assert "ligand_parameterization" not in load_manifest(result.manifest).metadata


def test_complex_extraction_uses_whole_complex_gate_then_partitioned_coordinates(tmp_path):
    req = replace(charmm_request(tmp_path), source_residue_name="TWB", excluded_residue_names=("GOL",))
    protein, first, second, provenance = (tmp_path/name for name in ("receptor.pdb", "pose1.mol2", "pose2.mol2", "inputs.json"))
    for path in (protein,first,second):
        path.write_text(path.name)
    provenance.write_text('{}')
    events = []
    def gate(path):
        events.append(("gate",path))
    def partition(*args, **kwargs):
        events.append(("partition",args[0]))
        return SimpleNamespace(protein_path=protein,ligand_path=first,ligand_copies=(second,),provenance_path=provenance)
    backend = Mock(return_value=package(tmp_path, count=2))
    runner = MaterializingRunner()
    with patch("core.preparation_orchestrator.stage_charmm36_force_field"):
        result = PreparationOrchestrator(req,runner=runner,structure_validator=gate,
            charmm_ligand_preparer=backend,complex_input_preparer=partition).run()
    assert events == [("gate",req.system.pdb_path),("partition",req.system.pdb_path)]
    assert backend.call_args.args[0] == str(first)
    assert backend.call_args.kwargs["ligand_copies"] == (str(second),)
    assert runner.commands[0].argv[runner.commands[0].argv.index("-f")+1].endswith("receptor.pdb")
    assert load_manifest(result.manifest).protein_source == req.system.pdb_path
    assert "complex_inputs" in load_manifest(result.manifest).metadata


def test_parameter_change_during_pdb2gmx_is_rejected_before_boxing(tmp_path):
    req = charmm_request(tmp_path)
    artifacts = package(tmp_path)
    class MutatingRunner(MaterializingRunner):
        def run(self, command, on_output=None):
            result = super().run(command,on_output)
            if command.argv[1] == "pdb2gmx":
                artifacts.ligand_itp.write_text(artifacts.ligand_itp.read_text() + '; altered\n')
            return result
    runner = MutatingRunner()
    with pytest.raises(PreparationError,match="changed"):
        run_with_backend(req,Mock(return_value=artifacts),runner)
    assert not any(command.argv[1] == "editconf" for command in runner.commands)


def test_protein_change_while_parameterizing_is_rejected_before_pdb2gmx(tmp_path):
    req = charmm_request(tmp_path)
    artifacts = package(tmp_path)
    def mutate(*args,**kwargs):
        Path(req.system.pdb_path).write_text('different protein')
        return artifacts
    runner = MaterializingRunner()
    with pytest.raises(PreparationError,match="changed"):
        run_with_backend(req,mutate,runner)
    assert not runner.commands


def test_charmm_protein_only_keeps_crystal_water_out_of_pdb2gmx_and_in_final_input(tmp_path):
    req = charmm_request(tmp_path)
    pdb = water_source(tmp_path,[oxygen()])
    req = replace(req,ligand_path=None,system=replace(req.system,pdb_path=str(pdb)))
    class WaterRunner(MaterializingRunner):
        def run(self,command,on_output=None):
            if command.argv[0] == 'obabel':
                self.commands.append(command)
                # Command paths are Linux/WSL paths; this fake exercises the local helper contract.
                sdf=next(Path(command.cwd).glob('*.sdf'))
                fake_builder(sdf,Path(command.cwd)/'modeled_waters.mol2')
                from core.preparation_orchestrator import CommandResult
                return CommandResult(0,'1 molecule converted')
            result=super().run(command,on_output)
            if command.argv[1] == 'pdb2gmx':
                top=Path(command.cwd)/'topol.top'
                top.write_text('#include "charmm.ff/tip3p.itp"\n'+top.read_text())
            return result
    runner=WaterRunner()
    result=run_with_backend(req,Mock(),runner)
    manifest=load_manifest(result.manifest)
    assert manifest.metadata['crystal_waters']['water_count'] == 1
    assert manifest.metadata['crystal_waters']['provenance_sha256']
    assert runner.commands[0].argv[0] == 'obabel'
    pdb2gmx=next(command for command in runner.commands if command.argv[1] == 'pdb2gmx')
    assert 'without_crystal_waters' in pdb2gmx.argv[pdb2gmx.argv.index('-f')+1]
    assert 'SOL                1' in result.topology.read_text()
    assert int((tmp_path/'protein.gro').read_text().splitlines()[1]) == 4


@pytest.mark.parametrize('protein_only',(False,True))
def test_source_change_before_water_helper_never_becomes_new_baseline(tmp_path,protein_only):
    req=charmm_request(tmp_path)
    pdb=water_source(tmp_path,[oxygen()])
    req=replace(req,system=replace(req.system,pdb_path=str(pdb)),ligand_path=None if protein_only else req.ligand_path)
    def mutate(_root):
        pdb.write_text(pdb.read_text()+'REMARK changed after validation\n')
    with patch('core.preparation_orchestrator.stage_charmm36_force_field',side_effect=mutate), \
         patch('core.preparation_orchestrator.prepare_crystal_waters',side_effect=AssertionError('stale source reached helper')) as helper:
        with pytest.raises(PreparationError,match='changed'):
            PreparationOrchestrator(req,runner=MaterializingRunner(),structure_validator=lambda _:None,
                charmm_ligand_preparer=Mock()).run()
        helper.assert_not_called()


def test_solvation_merges_water_rows_before_ions_tpr_is_generated(tmp_path):
    req=replace(charmm_request(tmp_path),ligand_path=None)
    snapshots=[]
    class SolvationRunner(MaterializingRunner):
        def run(self,command,on_output=None):
            if command.argv[1] in {'grompp','genion'}:
                rows=[line.split() for line in (tmp_path/'topol.top').read_text().splitlines() if line.strip().startswith('SOL')]
                snapshots.append(rows)
            result=super().run(command,on_output)
            if command.argv[1] == 'solvate':
                top=tmp_path/'topol.top'
                top.write_text(top.read_text()+'SOL 269\nSOL 13992\n')
            return result
    run_with_backend(req,Mock(),SolvationRunner())
    assert snapshots == [[['SOL','14261']],[['SOL','14261']]]
