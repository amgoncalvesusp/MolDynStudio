"""Shared atom selections and topology conversion for local analysis backends."""

from pathlib import Path
from contextlib import contextmanager


def load_universe(topology: str, trajectory: str, selection: str):
    import MDAnalysis as mda
    import numpy as np

    try:
        universe = mda.Universe(topology, trajectory)
    except (ValueError, OSError, NotImplementedError) as exc:
        if Path(topology).suffix.lower() == ".tpr" and ("tpx" in str(exc).lower() or "version" in str(exc).lower()):
            raise ValueError("This MDAnalysis version cannot read the TPR format. The validated environment uses GROMACS 2025.4. For an existing newer run, provide a matching PDB/GRO with the same atom order for structural analyses; PDB/GRO does not supply the bonds/charges needed for hydrogen-bond analysis. Original error: " + str(exc)) from exc
        raise
    try:
        atoms = universe.select_atoms(selection)
        if len(atoms) == 0:
            raise ValueError(f"Selection contains no atoms: {selection}")
        if len(universe.trajectory) == 0:
            raise ValueError("Trajectory contains no frames.")
        if not np.isfinite(atoms.positions).all():
            raise ValueError("Selected coordinates contain non-finite values.")
    except Exception:
        universe.trajectory.close()
        raise
    return universe, atoms


@contextmanager
def managed_universe(topology: str, trajectory: str, selection: str):
    universe, atoms = load_universe(topology, trajectory, selection)
    try:
        yield universe, atoms
    finally:
        universe.trajectory.close()


def atom_metadata(atoms) -> dict:
    return {
        "atom_indices_zero_based": [int(value) for value in atoms.indices],
        "residue_ids": [int(value) for value in atoms.resids],
        "residue_indices_zero_based": [int(value) for value in atoms.resindices],
        "segment_indices_zero_based": [int(value) for value in atoms.segindices],
        "atom_names": [str(value) for value in atoms.names],
    }


def _mdtraj_topology(universe):
    """Build a topology directly, preserving source chain/segment and bonds."""
    import mdtraj as md
    from MDAnalysis.exceptions import NoDataError

    topology = md.Topology()
    try:
        chain_ids = tuple(str(value) for value in universe.atoms.chainIDs)
    except NoDataError:
        chain_ids = tuple(str(value) for value in universe.atoms.segids)
    try:
        molecule_ids = tuple(map(int, universe.atoms.molnums))
    except NoDataError:
        molecule_ids = (None,) * len(universe.atoms)
    identities = tuple(zip(map(int, universe.atoms.segindices), chain_ids, molecule_ids))
    chains = {identity: topology.add_chain(identity[1]) for identity in dict.fromkeys(identities)}
    residues = {
        int(residue.ix): topology.add_residue(
            str(residue.resname), chains[identities[int(residue.atoms[0].index)]],
            resSeq=int(residue.resid), segment_id=str(residue.segid))
        for residue in universe.residues
    }
    try:
        atoms = tuple(
            topology.add_atom(str(atom.name), md.element.get_by_symbol(str(atom.element)),
                              residues[int(atom.resindex)], serial=int(atom.index) + 1)
            for atom in universe.atoms
        )
    except (NoDataError, KeyError) as exc:
        raise ValueError("TPR conversion requires explicit element identities; atom names cannot safely determine chemistry.") from exc
    try:
        bonds = universe.bonds.indices
    except NoDataError as exc:
        raise ValueError("TPR conversion requires source bond connectivity.") from exc
    for left, right in bonds:
        topology.add_bond(atoms[int(left)], atoms[int(right)])
    return topology


def load_mdtraj(topology: str, trajectory: str, selection: str = "protein"):
    """Preserve source atom order when converting a GROMACS TPR topology."""
    import mdtraj as md

    with managed_universe(topology, trajectory, selection) as (universe, atoms):
    # MDTraj does not read TPR files. Direct topology construction avoids PDB
    # field truncation and unintended bond guessing across molecular chains.
        if Path(topology).suffix.lower() == ".tpr":
            traj = md.load(trajectory, top=_mdtraj_topology(universe), atom_indices=atoms.indices)
        else:
            traj = md.load(trajectory, top=topology, atom_indices=atoms.indices)
        if traj.n_frames == 0:
            raise ValueError("Trajectory contains no frames.")
        return traj, atom_metadata(atoms)
