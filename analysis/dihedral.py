"""Backbone dihedral analysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult


class RamachandranAnalysis(AnalysisBase):
    name = "Ramachandran"
    tool = "mdtraj"
    tooltip = "Backbone phi/psi angle distributions."

    def run(self, topology: str, trajectory: str) -> AnalysisResult:
        import mdtraj as md

        traj = md.load(trajectory, top=topology)
        phi_indices, phi = md.compute_phi(traj)
        psi_indices, psi = md.compute_psi(traj)
        return AnalysisResult(self.name, {"phi": phi, "psi": psi, "phi_indices": phi_indices, "psi_indices": psi_indices})

