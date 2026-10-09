"""Contact map generation."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult
from analysis.trajectory import load_mdtraj


class ContactMapAnalysis(AnalysisBase):
    name = "Contact map"
    tool = "mdtraj"
    tooltip = "Residue-residue or atom-pair distances transformed into contacts."

    def run(self, topology: str, trajectory: str, cutoff_nm: float = 0.45, selection: str = "protein") -> AnalysisResult:
        import mdtraj as md
        import numpy as np

        if not np.isfinite(cutoff_nm) or cutoff_nm <= 0:
            raise ValueError("Contact cutoff must be positive and finite (nm).")
        traj, metadata = load_mdtraj(topology, trajectory, selection)
        contacts, pairs = md.compute_contacts(traj)
        frequency = np.mean(contacts < cutoff_nm, axis=0)
        rows = [(*map(int, pair), float(value)) for pair, value in zip(pairs, frequency)]
        return AnalysisResult(self.name, rows, "Closest-heavy-atom contacts; MDTraj excludes residue pairs separated by fewer than three residues. Selected-topology residue indices are zero-based.",
                              ("residue_index1", "residue_index2", "frequency"),
                              {**metadata, "cutoff_nm": cutoff_nm, "residue_labels": [str(r) for r in traj.topology.residues]})

