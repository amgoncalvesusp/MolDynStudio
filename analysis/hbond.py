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
        from MDAnalysis.exceptions import NoDataError

        universe = mda.Universe(topology, trajectory)
        between = [selection1, selection2]
        try:
            analysis = HBA(universe, between=between)
            analysis.run()
        except NoDataError:
            scope = f"(({selection1}) or ({selection2}))"
            analysis = HBA(
                universe,
                donors_sel=f"{scope} and ((name N* O* S*) or (type N* O* S*))",
                hydrogens_sel=f"{scope} and ((name H* [123]H*) or (type H*))",
                acceptors_sel=f"{scope} and ((name N* O* S*) or (type N* O* S*))",
                between=between,
            )
            analysis.run()
        return AnalysisResult(self.name, analysis.results.hbonds, "Columns follow MDAnalysis HBA output.")
