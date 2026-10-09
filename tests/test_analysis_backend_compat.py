"""Real trajectory checks for backend units, alignment, and chemical safeguards."""

import numpy as np
import pytest

from analysis.rmsd import RMSDAnalysis
from analysis.rmsf import RMSFAnalysis
from analysis.hbond import HydrogenBondAnalysis
from analysis.gyration import RadiusOfGyrationAnalysis
from analysis.sasa import SASAAnalysis
from analysis.pca import PCAAnalysis
from analysis.contact_map import ContactMapAnalysis
from analysis.dihedral import RamachandranAnalysis
from analysis.dssp import DSSPAnalysis


@pytest.fixture
def trajectory(tmp_path):
    mda = pytest.importorskip("MDAnalysis")
    universe = mda.Universe.empty(4, n_residues=4, atom_resindex=np.arange(4), trajectory=True)
    for name, values in (("names", ["CA"]*4), ("types", ["C"]*4), ("elements", ["C"]*4),
                         ("resnames", ["ALA"]*4), ("resids", [1,2,3,4]), ("masses", [12.011]*4)):
        universe.add_TopologyAttr(name, values)
    base = np.array([[0,0,0], [2,0,0], [0,2,0], [0,0,2]], dtype=np.float32)
    universe.atoms.positions = base
    topology, trajectory = tmp_path/"system.pdb", tmp_path/"rigid.dcd"
    universe.atoms.write(str(topology))
    with mda.Writer(str(trajectory), n_atoms=4, dt=1000) as writer:
        for frame in range(6):
            angle=frame*.25
            rotation=np.array([[np.cos(angle),-np.sin(angle),0], [np.sin(angle),np.cos(angle),0], [0,0,1]])
            universe.atoms.positions = base @ rotation + frame * 2
            writer.write(universe.atoms)
    universe.trajectory.close()
    return str(topology), str(trajectory)


def test_rmsd_and_rmsf_remove_rigid_motion(trajectory):
    rmsd = RMSDAnalysis().run(*trajectory, selection="all")
    rmsf = RMSFAnalysis().run(*trajectory, selection="all")
    assert np.max(np.abs(rmsd.data[:,2])) < 1e-4
    assert np.max(np.abs(rmsf.data)) < 1e-4
    assert rmsd.columns == ("frame", "time_ps", "rmsd_angstrom")
    assert rmsf.metadata["atom_indices_zero_based"] == [0,1,2,3]


def test_gyration_matches_analytical_geometry(trajectory):
    result = RadiusOfGyrationAnalysis().run(*trajectory, selection="all")
    assert np.allclose(np.asarray(result.data)[:,2], 1.5, atol=1e-5)


def test_sasa_reports_positive_area_in_nm_squared(trajectory):
    pytest.importorskip("mdtraj")
    result = SASAAnalysis().run(*trajectory, selection="all")
    assert len(result.data) == 6
    assert result.columns[-1] == "sasa_nm2"
    assert all(row[2] > 0 for row in result.data)


def test_hbond_without_charges_refuses_atom_name_guess(trajectory):
    with pytest.raises(ValueError, match="bonds, charges"):
        HydrogenBondAnalysis().run(*trajectory, selection1="all", selection2="all")


def test_empty_selection_and_invalid_reference_are_explicit(trajectory):
    with pytest.raises(ValueError, match="no atoms"):
        RMSDAnalysis().run(*trajectory, selection="resname MISSING")
    with pytest.raises(ValueError, match="Reference frame"):
        RMSDAnalysis().run(*trajectory, selection="all", reference_frame=99)


def test_pca_has_fitted_projection_and_variance_metadata(trajectory):
    result = PCAAnalysis().run(*trajectory, selection="all")
    assert len(result.data) == 6
    assert result.metadata["alignment"] == "fit_rot_trans"
    assert len(result.metadata["variance_angstrom2"]) == 3


@pytest.fixture
def backbone(tmp_path):
    mda = pytest.importorskip("MDAnalysis")
    pytest.importorskip("mdtraj")
    universe = mda.Universe.empty(20, n_residues=5, atom_resindex=np.repeat(np.arange(5),4), trajectory=True)
    for name, values in (("names", ["N","CA","C","O"]*5), ("types", ["N","C","C","O"]*5),
                         ("elements", ["N","C","C","O"]*5), ("resnames", ["ALA"]*5), ("resids", list(range(1,6)))):
        universe.add_TopologyAttr(name, values)
    motif = np.array([[-1,.3,.2],[0,1,0],[1,0,.3],[1.7,-.8,.4]])
    base=np.concatenate([motif+[r*3.8,np.sin(r),0] for r in range(5)])
    universe.atoms.positions=base
    top,traj=tmp_path/"backbone.pdb",tmp_path/"backbone.dcd"
    universe.atoms.write(str(top))
    random=np.random.default_rng(19)
    with mda.Writer(str(traj),n_atoms=20,dt=1000) as writer:
        for _ in range(5):
            universe.atoms.positions=base+random.normal(0,.05,base.shape)
            writer.write(universe.atoms)
    universe.trajectory.close()
    return str(top),str(traj)


def test_mdtraj_residue_identity_is_preserved(backbone):
    contacts=ContactMapAnalysis().run(*backbone)
    rama=RamachandranAnalysis().run(*backbone)
    dssp=DSSPAnalysis().run(*backbone)
    assert len(contacts.data)==3
    assert all(row[1]-row[0]>=3 and 0<=row[2]<=1 for row in contacts.data)
    assert {row[2] for row in rama.data} == {1,2,3}
    assert len(rama.data)==15
    assert len(dssp.data)==25
    assert {row[3] for row in dssp.data} <= {"H","E","C"}


def test_real_all_structural_backends_complete_auditable_worker(backbone, tmp_path):
    import json
    from pathlib import Path
    from analysis.execution import ANALYSES, AnalysisInputs, AnalysisWorker
    inputs=AnalysisInputs(*backbone,str(tmp_path/"results"),tuple(name for name in ANALYSES if name!="Hydrogen bonds"),prepared=True)
    results=[]
    worker=AnalysisWorker(inputs)
    worker.completed.connect(lambda *args: results.append(args))
    worker.run()
    assert len(results)==1
    assert results[0][0], results[0][2]
    manifest=json.loads(Path(results[0][1]).read_text())
    assert manifest["status"]=="complete"
    assert len(manifest["results"])==8
    assert all(Path(item["path"]).is_file() for item in manifest["results"])


def test_tpr_conversion_preserves_long_chain_names_and_source_bonds():
    mda=pytest.importorskip("MDAnalysis")
    pytest.importorskip("mdtraj")
    from analysis.trajectory import _mdtraj_topology
    universe=mda.Universe.empty(6,n_residues=2,n_segments=2,atom_resindex=[0,0,0,1,1,1],residue_segindex=[0,1],trajectory=True)
    for name,values in (("names",["N","CA","C"]*2),("elements",["N","C","C"]*2),
                        ("resids",[1,1]),("resnames",["GLY","GLY"]),
                        ("segids",["Protein_A","Protein_B"]),("chainIDs",["Protein_A"]*3+["Protein_B"]*3),
                        ("bonds",[(0,1),(1,2),(3,4),(4,5)])):
        universe.add_TopologyAttr(name,values)
    topology=_mdtraj_topology(universe)
    assert topology.n_chains==2
    assert [(a.index,b.index) for a,b in topology.bonds]==[(0,1),(1,2),(3,4),(4,5)]
    assert [chain.chain_id for chain in topology.chains]==["Protein_A","Protein_B"]
    universe.trajectory.close()


def test_tpr_repeated_molecule_copies_remain_separate_chains():
    mda=pytest.importorskip("MDAnalysis")
    pytest.importorskip("mdtraj")
    from analysis.trajectory import _mdtraj_topology
    universe=mda.Universe.empty(6,n_residues=2,atom_resindex=[0,0,0,1,1,1],trajectory=True)
    for name,values in (("names",["N","CA","C"]*2),("elements",["N","C","C"]*2),
                        ("resids",[1,1]),("resnames",["GLY","GLY"]),
                        ("segids",["Protein"]),("chainIDs",["Protein_A"]*6),
                        ("molnums",[0,1]),("bonds",[(0,1),(1,2),(3,4),(4,5)])):
        universe.add_TopologyAttr(name,values)
    topology=_mdtraj_topology(universe)
    assert topology.n_chains==2
    assert topology.n_bonds==4
    universe.trajectory.close()
