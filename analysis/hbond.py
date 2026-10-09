"""Hydrogen bond analysis using MDAnalysis."""

from __future__ import annotations

from analysis.base import AnalysisBase, AnalysisResult
from analysis.trajectory import managed_universe


class HydrogenBondAnalysis(AnalysisBase):
    name = "Hydrogen bonds"
    tool = "mdanalysis"
    tooltip = "Hydrogen bond counts and occupancies over time."

    def run(self, topology: str, trajectory: str, selection1: str = "protein", selection2: str = "resname LIG") -> AnalysisResult:
        from MDAnalysis.analysis.hydrogenbonds.hbond_analysis import HydrogenBondAnalysis as HBA
        from MDAnalysis.exceptions import NoDataError

        with managed_universe(topology, trajectory, selection1) as (universe, atoms):
            if len(universe.select_atoms(selection2)) == 0:
                raise ValueError(f"Second hydrogen-bond selection is empty: {selection2}")
            between = [selection1, selection2]
            try:
                analysis = HBA(universe, between=between)
                analysis.run()
            except NoDataError as exc:
                raise ValueError("Hydrogen-bond analysis requires bonds, charges, and explicit hydrogens. Use the matching TPR topology; chemical donor/acceptor types cannot be inferred safely from atom names.") from exc
            return AnalysisResult(self.name, analysis.results.hbonds, "Geometric H-bonds using topology-based donor/acceptor guesses; D-A <= 3.0 angstrom and D-H-A > 150 degrees. Inspect protonation and atom typing.",
                                  ("frame", "donor_atom_index", "hydrogen_atom_index", "acceptor_atom_index", "distance_angstrom", "angle_degree"),
                                  {"selection1": selection1, "selection2": selection2, "index_base": 0})
