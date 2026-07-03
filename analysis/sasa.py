"""Solvent-accessible surface area helpers."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult


class SASAAnalysis(AnalysisBase):
    name = "SASA"
    tool = "mdtraj"
    tooltip = "Solvent accessible surface area using MDTraj shrake_rupley."

    def run(self, topology: str, trajectory: str) -> AnalysisResult:
        import mdtraj as md

        traj = md.load(trajectory, top=topology)
        sasa = md.shrake_rupley(traj)
        return AnalysisResult(self.name, sasa, "Rows are frames; columns are atoms.")

