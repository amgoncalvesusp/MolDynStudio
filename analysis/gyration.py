"""Radius of gyration analysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult
from analysis.trajectory import managed_universe, atom_metadata


class RadiusOfGyrationAnalysis(AnalysisBase):
    name = "Radius of gyration"
    tool = "mdanalysis"
    tooltip = "Compactness of the selected molecule over time."

    def run(self, topology: str, trajectory: str, selection: str = "protein") -> AnalysisResult:
        with managed_universe(topology, trajectory, selection) as (universe, atoms):
            rows = []
            for ts in universe.trajectory:
                rows.append((ts.frame, float(ts.time), float(atoms.radius_of_gyration())))
            return AnalysisResult(self.name, rows, "Mass-weighted radius of gyration; molecules must be whole.",
                                  ("frame", "time_ps", "rg_angstrom"), atom_metadata(atoms))

