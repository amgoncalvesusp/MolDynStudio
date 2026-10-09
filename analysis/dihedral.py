"""Backbone dihedral analysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult
from analysis.trajectory import load_mdtraj


class RamachandranAnalysis(AnalysisBase):
    name = "Ramachandran"
    tool = "mdtraj"
    tooltip = "Backbone phi/psi angle distributions."

    def run(self, topology: str, trajectory: str, selection: str = "protein") -> AnalysisResult:
        import mdtraj as md
        import numpy as np

        traj, metadata = load_mdtraj(topology, trajectory, selection)
        phi_indices, phi = md.compute_phi(traj)
        psi_indices, psi = md.compute_psi(traj)
        phi_residues = {traj.topology.atom(int(indices[1])).residue.index: i for i, indices in enumerate(phi_indices)}
        psi_residues = {traj.topology.atom(int(indices[1])).residue.index: i for i, indices in enumerate(psi_indices)}
        shared = sorted(set(phi_residues) & set(psi_residues))
        if not shared:
            raise ValueError("No residues with both phi and psi angles; select complete protein residues.")
        rows = [(frame, float(time), residue, float(np.degrees(phi[frame, phi_residues[residue]])),
                 float(np.degrees(psi[frame, psi_residues[residue]])))
                for frame, time in enumerate(traj.time) for residue in shared]
        return AnalysisResult(self.name, rows, "Phi/psi paired by residue identity, excluding termini with missing angles.",
                              ("frame", "time_ps", "residue_index", "phi_degree", "psi_degree"),
                              {**metadata, "residue_labels": [str(r) for r in traj.topology.residues]})

