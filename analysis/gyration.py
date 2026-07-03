"""Radius of gyration analysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult


class RadiusOfGyrationAnalysis(AnalysisBase):
    name = "Radius of gyration"
    tool = "mdanalysis"
    tooltip = "Compactness of the selected molecule over time."

    def run(self, topology: str, trajectory: str, selection: str = "protein") -> AnalysisResult:
        import MDAnalysis as mda

        universe = mda.Universe(topology, trajectory)
        atoms = universe.select_atoms(selection)
        rows = []
        for ts in universe.trajectory:
            rows.append((ts.frame, float(ts.time), float(atoms.radius_of_gyration())))
        return AnalysisResult(self.name, rows, "Columns: frame, time, Rg.")

