"""RMSF analysis using MDAnalysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult


class RMSFAnalysis(AnalysisBase):
    name = "RMSF"
    tool = "mdanalysis"
    tooltip = "Per-atom or per-residue positional fluctuation."

    def run(self, topology: str, trajectory: str, selection: str = "protein and name CA") -> AnalysisResult:
        import MDAnalysis as mda
        from MDAnalysis.analysis import rms

        universe = mda.Universe(topology, trajectory)
        atoms = universe.select_atoms(selection)
        analysis = rms.RMSF(atoms)
        analysis.run()
        return AnalysisResult(self.name, analysis.rmsf, "RMSF values match the selected atom order.")

