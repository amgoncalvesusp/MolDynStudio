"""Principal component analysis using MDAnalysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult


class PCAAnalysis(AnalysisBase):
    name = "PCA"
    tool = "mdanalysis"
    tooltip = "Dominant collective motions from trajectory covariance."

    def run(self, topology: str, trajectory: str, selection: str = "backbone", n_components: int = 3) -> AnalysisResult:
        import MDAnalysis as mda
        from MDAnalysis.analysis import pca

        universe = mda.Universe(topology, trajectory)
        analysis = pca.PCA(universe, select=selection, n_components=n_components).run()
        return AnalysisResult(self.name, analysis, "MDAnalysis PCA object returned.")

