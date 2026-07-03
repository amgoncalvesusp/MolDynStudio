"""RMSD analysis using MDAnalysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult


class RMSDAnalysis(AnalysisBase):
    name = "RMSD"
    tool = "mdanalysis"
    tooltip = "Root-mean-square deviation relative to a reference frame."

    def run(self, topology: str, trajectory: str, selection: str = "backbone", reference_frame: int = 0) -> AnalysisResult:
        import MDAnalysis as mda
        from MDAnalysis.analysis import rms

        universe = mda.Universe(topology, trajectory)
        atoms = universe.select_atoms(selection)
        analysis = rms.RMSD(atoms, ref_frame=reference_frame)
        analysis.run()
        return AnalysisResult(self.name, analysis.rmsd, "Columns: frame, time, RMSD.")

