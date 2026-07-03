"""Contact map generation."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult


class ContactMapAnalysis(AnalysisBase):
    name = "Contact map"
    tool = "mdtraj"
    tooltip = "Residue-residue or atom-pair distances transformed into contacts."

    def run(self, topology: str, trajectory: str, cutoff_nm: float = 0.45) -> AnalysisResult:
        import mdtraj as md
        import numpy as np

        traj = md.load(trajectory, top=topology)
        contacts, pairs = md.compute_contacts(traj)
        frequency = np.mean(contacts < cutoff_nm, axis=0)
        return AnalysisResult(self.name, {"pairs": pairs, "frequency": frequency}, "Contact frequencies by residue pair.")

