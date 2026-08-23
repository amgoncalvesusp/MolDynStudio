from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from analysis.hbond import HydrogenBondAnalysis
from analysis.rmsd import RMSDAnalysis
from analysis.rmsf import RMSFAnalysis


class _FakeUniverse:
    def __init__(self, *_args):
        pass

    def select_atoms(self, selection):
        return f"atoms:{selection}"


def _module(name: str, **attributes):
    result = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(result, key, value)
    return result


class AnalysisBackendCompatibilityTests(unittest.TestCase):
    def test_rmsd_uses_current_results_api(self):
        class FakeRMSD:
            def __init__(self, atoms, ref_frame):
                self.results = types.SimpleNamespace(rmsd=[[0, 0.0, 0.1]])

            @property
            def rmsd(self):
                raise AssertionError("deprecated RMSD.rmsd was accessed")

            def run(self):
                return self

        modules = {
            "MDAnalysis": _module("MDAnalysis", Universe=_FakeUniverse),
            "MDAnalysis.analysis": _module("MDAnalysis.analysis"),
            "MDAnalysis.analysis.rms": _module("MDAnalysis.analysis.rms", RMSD=FakeRMSD),
        }
        modules["MDAnalysis.analysis"].rms = modules["MDAnalysis.analysis.rms"]

        with patch.dict(sys.modules, modules):
            result = RMSDAnalysis().run("top.pdb", "traj.xtc")

        self.assertEqual(result.data, [[0, 0.0, 0.1]])

    def test_rmsf_uses_current_results_api(self):
        class FakeRMSF:
            def __init__(self, atoms):
                self.results = types.SimpleNamespace(rmsf=[0.1, 0.2])

            @property
            def rmsf(self):
                raise AssertionError("deprecated RMSF.rmsf was accessed")

            def run(self):
                return self

        modules = {
            "MDAnalysis": _module("MDAnalysis", Universe=_FakeUniverse),
            "MDAnalysis.analysis": _module("MDAnalysis.analysis"),
            "MDAnalysis.analysis.rms": _module("MDAnalysis.analysis.rms", RMSF=FakeRMSF),
        }
        modules["MDAnalysis.analysis"].rms = modules["MDAnalysis.analysis.rms"]

        with patch.dict(sys.modules, modules):
            result = RMSFAnalysis().run("top.pdb", "traj.xtc")

        self.assertEqual(result.data, [0.1, 0.2])

    def test_hbond_falls_back_to_explicit_atom_selections_without_charges(self):
        class FakeNoDataError(Exception):
            pass

        calls = []

        class FakeHBA:
            def __init__(self, _universe, **kwargs):
                calls.append(kwargs)
                if "donors_sel" not in kwargs:
                    raise FakeNoDataError("charge information is absent")
                self.results = types.SimpleNamespace(hbonds=[[0, 1, 2, 3, 2.8, 170.0]])

            def run(self):
                return self

        hbond_module = _module(
            "MDAnalysis.analysis.hydrogenbonds.hbond_analysis",
            HydrogenBondAnalysis=FakeHBA,
        )
        modules = {
            "MDAnalysis": _module("MDAnalysis", Universe=_FakeUniverse),
            "MDAnalysis.exceptions": _module("MDAnalysis.exceptions", NoDataError=FakeNoDataError),
            "MDAnalysis.analysis": _module("MDAnalysis.analysis"),
            "MDAnalysis.analysis.hydrogenbonds": _module("MDAnalysis.analysis.hydrogenbonds"),
            "MDAnalysis.analysis.hydrogenbonds.hbond_analysis": hbond_module,
        }

        with patch.dict(sys.modules, modules):
            result = HydrogenBondAnalysis().run(
                "top.pdb",
                "traj.xtc",
                selection1="protein",
                selection2="protein",
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["between"], ["protein", "protein"])
        self.assertIn("name H*", calls[1]["hydrogens_sel"])
        self.assertIn("[123]H*", calls[1]["hydrogens_sel"])
        self.assertIn("type H*", calls[1]["hydrogens_sel"])
        self.assertIn("name N* O* S*", calls[1]["donors_sel"])
        self.assertIn("type N* O* S*", calls[1]["donors_sel"])
        self.assertEqual(result.data[0][-2:], [2.8, 170.0])


if __name__ == "__main__":
    unittest.main()
