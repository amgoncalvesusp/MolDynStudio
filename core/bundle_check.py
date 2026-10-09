"""Crash-isolated checks of the installed or frozen scientific/GUI runtime."""
from __future__ import annotations

from importlib.metadata import version
from io import StringIO
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import uuid


def _mdanalysis_probe() -> dict:
    import numpy as np
    from MDAnalysis.lib.distances import distance_array

    value = float(distance_array(np.array([[0., 0., 0.]]), np.array([[3., 4., 0.]]))[0, 0])
    if not math.isclose(value, 5., abs_tol=1e-6):
        raise ValueError(f"MDAnalysis distance mismatch: {value} angstrom.")
    return {"version": version("MDAnalysis"), "distance_angstrom": value}


def _mdtraj_probe() -> dict:
    import mdtraj as md
    import numpy as np

    topology = md.Topology()
    residue = topology.add_residue("GLY", topology.add_chain())
    topology.add_atom("N", md.element.nitrogen, residue)
    topology.add_atom("CA", md.element.carbon, residue)
    trajectory = md.Trajectory(np.array([[[0., 0., 0.], [0.3, 0.4, 0.]]]), topology)
    value = float(md.compute_distances(trajectory, [[0, 1]], opt=True)[0, 0])
    if not math.isclose(value, 0.5, abs_tol=1e-6):
        raise ValueError(f"MDTraj distance mismatch: {value} nm.")
    return {"version": version("mdtraj"), "distance_nm": value}


def _pdbfixer_probe() -> dict:
    from pdbfixer import PDBFixer

    pdb = """ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N
ATOM      2  CA  GLY A   1       1.450   0.000   0.000  1.00  0.00           C
ATOM      3  C   GLY A   1       2.000   1.200   0.000  1.00  0.00           C
ATOM      4  O   GLY A   1       1.500   2.200   0.000  1.00  0.00           O
TER
END
"""
    fixer = PDBFixer(pdbfile=StringIO(pdb))
    atoms = fixer.topology.getNumAtoms()
    if atoms != 4:
        raise ValueError(f"PDBFixer retained {atoms} atoms; expected 4.")
    return {"version": version("pdbfixer"), "retained_atoms": atoms}


def _openmm_probe() -> dict:
    import openmm
    from openmm import unit

    system = openmm.System()
    system.addParticle(12.)
    force = openmm.CustomExternalForce("0.5*k*(x*x+y*y+z*z)")
    force.addGlobalParameter("k", 1.)
    force.addParticle(0, [])
    system.addForce(force)
    integrator = openmm.VerletIntegrator(0.001)
    cpu = openmm.Platform.getPlatformByName("CPU")
    context = openmm.Context(system, integrator, cpu)
    context.setPositions([[1., 0., 0.]])
    before = context.getState(getEnergy=True).getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
    integrator.step(1)
    after = context.getState(getEnergy=True).getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
    if not math.isclose(before, 0.5, abs_tol=1e-8) or not math.isfinite(after):
        raise ValueError(f"OpenMM harmonic energy mismatch: {before}, {after} kJ/mol.")
    return {"version": version("openmm"), "platform": cpu.getName(), "steps": 1,
            "initial_energy_kj_mol": before, "final_energy_kj_mol": after,
            "plugin_load_failures": list(openmm.Platform.getPluginLoadFailures())}


def _parmed_probe() -> dict:
    import parmed
    from parmed.gromacs import GromacsTopologyFile
    from parmed.topologyobjects import AtomType

    structure = parmed.Structure()
    atom = parmed.Atom(name="Ar", type="AR", atomic_number=18, mass=39.948, charge=0.)
    atom_type = AtomType("AR", 1, 39.948, 18)
    atom_type.set_lj_params(0.2, 1.9)
    atom.atom_type = atom_type
    structure.add_atom(atom, "AR", 1)
    output = StringIO()
    GromacsTopologyFile.from_structure(structure).write(output)
    text = output.getvalue()
    if "[ atoms ]" not in text or "[ atomtypes ]" not in text:
        raise ValueError("ParmEd did not write a complete GROMACS topology.")
    return {"version": version("ParmEd"), "gromacs_topology_characters": len(text)}


def _gui_probe() -> dict:
    from PyQt5.QtCore import QSettings, Qt, QT_VERSION_STR, PYQT_VERSION_STR
    from PyQt5.QtWidgets import QApplication

    with tempfile.TemporaryDirectory(prefix="moldyn-bundle-settings-") as directory:
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope, directory)
        if QApplication.instance() is None:
            QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
        app = QApplication.instance() or QApplication([])
        from gromacs_analysis_studio_v11 import APP_VERSION, MainWindow

        window = MainWindow()
        try:
            window.show()
            for index in range(window.stack.count()):
                window.stack.setCurrentIndex(index)
                app.processEvents()
            if window.stack.count() < 5 or window.centralWidget() is None:
                raise ValueError("The main window did not construct its required pages/layout.")
            result = {"application_version": APP_VERSION, "qt_version": QT_VERSION_STR,
                      "pyqt_version": PYQT_VERSION_STR, "pages": list(window.pages),
                      "page_count": window.stack.count(), "platform": app.platformName()}
        finally:
            window.close()
            app.processEvents()
        return result


PROBES = (("mdanalysis", _mdanalysis_probe), ("mdtraj", _mdtraj_probe),
          ("pdbfixer", _pdbfixer_probe), ("openmm", _openmm_probe),
          ("parmed", _parmed_probe), ("gui", _gui_probe))


def _write_report(path: Path, report: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def worker_main(report_path: str | Path) -> int:
    path = Path(report_path)
    report = {"schema": "moldynstudio.bundle-check.v1", "status": "running", "checks": [],
              "frozen": bool(getattr(sys, "frozen", False)), "python": platform.python_version(),
              "system": platform.platform()}
    for name, probe in PROBES:
        report = {**report, "active_probe": name}
        _write_report(path, report)
        try:
            result = {"name": name, "status": "passed", "details": probe()}
        except Exception as error:
            result = {"name": name, "status": "failed", "error_type": type(error).__name__, "error": str(error)}
        report = {**report, "checks": [*report["checks"], result]}
    passed = bool(report["checks"]) and all(item["status"] == "passed" for item in report["checks"])
    _write_report(path, {**report, "status": "passed" if passed else "failed", "active_probe": None})
    return 0 if passed else 1


def run_self_check(report_path: str | Path) -> int:
    """Keep native crashes and incomplete child results from becoming false passes."""
    destination = Path(report_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    log_path = destination.with_suffix(".log")
    report = {"schema": "moldynstudio.bundle-check.v1", "status": "failed", "checks": []}
    with tempfile.TemporaryDirectory(prefix="moldyn-bundle-check-", dir=destination.parent) as directory:
        child_report = Path(directory) / "worker.json"
        route = ["--bundle-check-worker"] if getattr(sys, "frozen", False) else ["-m", "core.bundle_check", "--worker"]
        command = [sys.executable, *route, str(child_report)]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        with log_path.open("w", encoding="utf-8") as log:
            try:
                completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180,
                                           cwd=Path(__file__).resolve().parent.parent,
                                           env={**os.environ, "OPENMM_CPU_THREADS": "1", "QT_QPA_PLATFORM": "offscreen",
                                                "QTWEBENGINE_DISABLE_SANDBOX": "1"}, **options)
                if child_report.is_file():
                    child_result = json.loads(child_report.read_text(encoding="utf-8"))
                    if not isinstance(child_result, dict) or not isinstance(child_result.get("checks"), list):
                        raise ValueError("The child self-check report has an invalid structure.")
                    report = child_result
                checks = report["checks"]
                expected = {name for name, _ in PROBES}
                passed = (completed.returncode == 0 and report.get("status") == "passed"
                          and len(checks) == len(expected)
                          and all(isinstance(item, dict) and isinstance(item.get("name"), str)
                                  and item.get("status") == "passed" for item in checks)
                          and {item.get("name") for item in checks} == expected)
                report = {**report, "status": "passed" if passed else "failed", "native_exit_code": completed.returncode}
                if not passed:
                    report = {**report, "error": "The native self-check failed or did not complete every required probe."}
            except (OSError, subprocess.TimeoutExpired, ValueError) as error:
                report = {**report, "status": "failed", "error_type": type(error).__name__, "error": str(error)}
    _write_report(destination, {**report, "log_path": str(log_path)})
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("Expected --worker and a report JSON path.")
    raise SystemExit(worker_main(sys.argv[2]))
