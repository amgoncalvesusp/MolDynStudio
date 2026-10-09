"""RMSD analysis using MDAnalysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult
from analysis.trajectory import managed_universe, atom_metadata


class RMSDAnalysis(AnalysisBase):
    name = "RMSD"
    tool = "mdanalysis"
    tooltip = "Root-mean-square deviation relative to a reference frame."

    def run(self, topology: str, trajectory: str, selection: str = "backbone", reference_frame: int = 0) -> AnalysisResult:
        from MDAnalysis.analysis import rms

        with managed_universe(topology, trajectory, selection) as (universe, atoms):
            if not 0 <= reference_frame < len(universe.trajectory):
                raise ValueError("Reference frame is outside the trajectory (zero-based index).")
            analysis = rms.RMSD(atoms, ref_frame=reference_frame)
            analysis.run()
            return AnalysisResult(self.name, analysis.results.rmsd, "Fitted RMSD against the selected reference; molecules must be whole.",
                                  ("frame", "time_ps", "rmsd_angstrom"),
                                  {**atom_metadata(atoms), "reference_frame": reference_frame, "alignment": "QCP rigid-body fit"})
