"""Solvent-accessible surface area helpers."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult
from analysis.trajectory import load_mdtraj


class SASAAnalysis(AnalysisBase):
    name = "SASA"
    tool = "mdtraj"
    tooltip = "Solvent accessible surface area using MDTraj shrake_rupley."

    def run(self, topology: str, trajectory: str, selection: str = "protein") -> AnalysisResult:
        import mdtraj as md

        traj, metadata = load_mdtraj(topology, trajectory, selection)
        sasa = md.shrake_rupley(traj)
        rows = [(i, float(time), float(area)) for i, (time, area) in enumerate(zip(traj.time, sasa.sum(axis=1)))]
        return AnalysisResult(self.name, rows, "Total SASA of isolated selected atoms; probe radius 0.14 nm, 960 sphere points.",
                              ("frame", "time_ps", "sasa_nm2"), metadata)

