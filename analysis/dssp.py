"""Secondary structure analysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult


class DSSPAnalysis(AnalysisBase):
    name = "DSSP"
    tool = "mdtraj"
    tooltip = "Secondary structure assignment through MDTraj DSSP."

    def run(self, topology: str, trajectory: str, simplified: bool = True) -> AnalysisResult:
        import mdtraj as md

        traj = md.load(trajectory, top=topology)
        assignments = md.compute_dssp(traj, simplified=simplified)
        return AnalysisResult(self.name, assignments, "Rows are frames; columns are residues.")

