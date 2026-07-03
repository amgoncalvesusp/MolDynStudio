"""Hydrogen bond analysis using MDAnalysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult


class HydrogenBondAnalysis(AnalysisBase):
    name = "Hydrogen bonds"
    tool = "mdanalysis"
    tooltip = "Hydrogen bond counts and occupancies over time."

    def run(self, topology: str, trajectory: str, selection1: str = "protein", selection2: str = "resname LIG") -> AnalysisResult:
        import MDAnalysis as mda
        from MDAnalysis.analysis.hydrogenbonds.hbond_analysis import HydrogenBondAnalysis as HBA

        universe = mda.Universe(topology, trajectory)
        analysis = HBA(universe, between=[selection1, selection2])
        analysis.run()
        return AnalysisResult(self.name, analysis.results.hbonds, "Columns follow MDAnalysis HBA output.")

