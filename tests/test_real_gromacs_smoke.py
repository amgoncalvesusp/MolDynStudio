"""Opt-in Linux smoke test against a real, pinned GROMACS installation."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from core.artifact_validation import validate_gro, validate_mdp, validate_topology
from core.forcefield_manager import download_charmm36_force_field, stage_charmm36_force_field
from core.gromacs_capabilities import resolve_gromacs_binary
from core.gromacs_runner import CommandSpec, GROMACSRunner, GromacsCommandBuilder
from core.system_prep import (
    SystemPrepParams,
    build_box_solvent_ion_steps,
    build_ions_mdp,
    build_pdb2gmx_step,
)
from utils.file_validators import validate_file
from utils.mdp_generator import MDParameters, generate_minimization_mdp


FIXTURE_DIR = Path(__file__).parent / "data" / "gromacs_smoke"
RUN_REAL_SMOKE = os.environ.get("MOLDYNSTUDIO_REAL_GROMACS_SMOKE") == "1"
EM_MDP = """\
integrator      = steep
nsteps          = 10
emtol           = 100.0
emstep          = 0.01
cutoff-scheme   = Verlet
nstlist         = 1
rlist           = 0.8
coulombtype     = Cut-off
rcoulomb        = 0.8
vdwtype         = Cut-off
rvdw            = 0.8
pbc             = xyz
nstenergy       = 1
nstlog          = 1
"""


def _glycine_dipeptide_pdb() -> str:
    """Original synthetic GLY-GLY coordinates, shared under this repository's license."""
    atoms = (
        (1, "N", 0.000, 0.000, 0.000),
        (1, "CA", 1.460, 0.000, 0.000),
        (1, "C", 2.000, 1.410, 0.000),
        (1, "O", 1.270, 2.380, 0.000),
        (2, "N", 3.310, 1.610, 0.000),
        (2, "CA", 3.900, 2.940, 0.000),
        (2, "C", 5.400, 2.800, 0.000),
        (2, "O", 6.020, 3.850, 0.000),
        (2, "OXT", 5.940, 1.670, 0.000),
    )
    return "".join(
        f"ATOM  {index:5d} {name:^4s} GLY A{residue:4d}    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}{1.0:6.2f}{0.0:6.2f}          {name[0]:>2s}\n"
        for index, (residue, name, x, y, z) in enumerate(atoms, 1)
    ) + "TER\nEND\n"


def _native_command(spec: CommandSpec) -> CommandSpec:
    """Use the activated CI environment without bypassing tokenized specs."""

    return replace(spec, use_conda=False, use_wsl=False)


def _build_smoke_commands(executable: str, work_dir: Path) -> tuple[CommandSpec, ...]:
    builder = GromacsCommandBuilder(executable=executable)
    cwd = str(work_dir)
    return (
        _native_command(
            builder.build(
                "grompp",
                (
                    "-f",
                    "em.mdp",
                    "-c",
                    "argon.gro",
                    "-p",
                    "topol.top",
                    "-o",
                    "em.tpr",
                ),
                cwd=cwd,
                stage="minimization",
                command_name="grompp",
            )
        ),
        _native_command(
            builder.build(
                "mdrun",
                ("-s", "em.tpr", "-deffnm", "em", "-nt", "1"),
                cwd=cwd,
                stage="minimization",
                command_name="mdrun",
            )
        ),
    )


class GromacsSmokeContractTests(unittest.TestCase):
    def test_commands_are_tokenized_and_use_the_configured_executable(self):
        commands = _build_smoke_commands("/opt/gromacs/bin/gmx", Path("smoke dir"))

        self.assertEqual(
            commands[0].args,
            (
                "grompp",
                "-f",
                "em.mdp",
                "-c",
                "argon.gro",
                "-p",
                "topol.top",
                "-o",
                "em.tpr",
            ),
        )
        self.assertEqual(
            commands[1].args,
            ("mdrun", "-s", "em.tpr", "-deffnm", "em", "-nt", "1"),
        )
        self.assertTrue(all(command.executable == "/opt/gromacs/bin/gmx" for command in commands))
        self.assertTrue(all(command.cwd == str(Path("smoke dir")) for command in commands))
        self.assertTrue(all(not command.use_conda and not command.use_wsl for command in commands))
        self.assertNotIn("-maxwarn", commands[0].args)

    def test_tiny_fixture_is_bundled_with_redistribution_terms(self):
        expected = ("argon.gro", "topol.top", "README.md")

        for filename in expected:
            with self.subTest(filename=filename):
                self.assertTrue((FIXTURE_DIR / filename).is_file())

        fixture_checks = (
            validate_gro(FIXTURE_DIR / "argon.gro"),
            validate_topology(FIXTURE_DIR / "topol.top"),
        )
        self.assertTrue(
            all(result.ok for result in fixture_checks),
            msg="; ".join(result.message for result in fixture_checks),
        )

        with tempfile.TemporaryDirectory() as directory:
            mdp_path = Path(directory) / "em.mdp"
            mdp_path.write_text(EM_MDP, encoding="utf-8")
            mdp_check = validate_mdp(mdp_path, require_dt=False)
        self.assertTrue(mdp_check.ok, mdp_check.message)


@unittest.skipUnless(RUN_REAL_SMOKE, "set MOLDYNSTUDIO_REAL_GROMACS_SMOKE=1")
class RealGromacsSmokeTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith("linux"), "real GROMACS smoke is Linux-only")
    def test_real_charmm_download_preparation_and_minimization(self):
        executable = resolve_gromacs_binary(
            os.environ.get("MOLDYNSTUDIO_GROMACS_EXECUTABLE", "auto")
        )
        with tempfile.TemporaryDirectory(prefix="moldynstudio-charmm-smoke-") as directory:
            root = Path(directory)
            work_dir = root / "project"
            cache = root / "forcefields"
            download_charmm36_force_field(cache_root=cache)
            staged = stage_charmm36_force_field(work_dir, cache_root=cache)
            self.assertTrue((staged / "forcefield.itp").is_file())
            protein = work_dir / "gly-gly.pdb"
            protein.write_text(_glycine_dipeptide_pdb(), encoding="utf-8")
            params = SystemPrepParams(
                pdb_path=str(protein), work_dir=str(work_dir), force_field="CHARMM36m",
            )
            (work_dir / "ions.mdp").write_text(build_ions_mdp(params.force_field), encoding="utf-8")
            em_mdp = generate_minimization_mdp(MDParameters(force_field=params.force_field))
            # Bound CI runtime; retain the actual generated CHARMM nonbonded settings.
            em_mdp = em_mdp.replace("nsteps                  = 50000", "nsteps                  = 100")
            self.assertIn("vdw-modifier            = force-switch", em_mdp)
            (work_dir / "em.mdp").write_text(em_mdp, encoding="utf-8")
            steps = [build_pdb2gmx_step(params), *build_box_solvent_ion_steps(params)]
            steps.extend([
                ("CHARMM minimization preprocessing", "grompp",
                 ["-f", "em.mdp", "-c", "system.gro", "-p", "topol.top", "-o", "em.tpr"], None),
                ("CHARMM minimization", "mdrun", ["-deffnm", "em", "-nt", "1"], None),
            ])
            builder = GromacsCommandBuilder(executable=executable)
            commands = tuple(
                _native_command(builder.build(
                    subcommand, args, cwd=str(work_dir), stdin_text=stdin,
                    stage="preparation", command_name=description,
                ))
                for description, subcommand, args, stdin in steps
            )
            status: list[tuple[bool, int]] = []
            output: list[str] = []
            runner = GROMACSRunner(commands)
            runner.finished_with_status.connect(lambda success, code: status.append((success, code)))
            runner.log_line.connect(output.append)
            runner.run()
            self.assertEqual(status, [(True, 0)], "\n".join(output[-150:]))
            result = validate_gro(work_dir / "em.gro")
            self.assertTrue(result.ok, result.message)
            for name in ("em.tpr", "em.edr", "em.log"):
                self.assertGreater((work_dir / name).stat().st_size, 0, name)

    @unittest.skipUnless(sys.platform.startswith("linux"), "real GROMACS smoke is Linux-only")
    def test_real_grompp_and_mdrun_produce_valid_minimization_outputs(self):
        configured = os.environ.get("MOLDYNSTUDIO_GROMACS_EXECUTABLE", "auto")
        try:
            executable = resolve_gromacs_binary(configured)
        except (FileNotFoundError, PermissionError, ValueError) as exc:
            self.skipTest(f"configured GROMACS executable is unavailable: {exc}")

        with tempfile.TemporaryDirectory(prefix="moldynstudio-gmx-smoke-") as directory:
            work_dir = Path(directory)
            shutil.copy2(FIXTURE_DIR / "argon.gro", work_dir / "argon.gro")
            shutil.copy2(FIXTURE_DIR / "topol.top", work_dir / "topol.top")
            (work_dir / "em.mdp").write_text(EM_MDP, encoding="utf-8")

            input_checks = (
                validate_gro(work_dir / "argon.gro"),
                validate_topology(work_dir / "topol.top"),
                validate_mdp(work_dir / "em.mdp", require_dt=False),
            )
            self.assertTrue(
                all(result.ok for result in input_checks),
                msg="; ".join(result.message for result in input_checks),
            )

            for command in _build_smoke_commands(executable, work_dir):
                status: list[tuple[bool, int]] = []
                output: list[str] = []
                runner = GROMACSRunner((command,))
                runner.finished_with_status.connect(
                    lambda success, code: status.append((success, code))
                )
                runner.log_line.connect(output.append)
                runner.run()
                self.assertEqual(
                    status,
                    [(True, 0)],
                    msg=(
                        f"{command.command_label} failed\n"
                        + "\n".join(output[-100:])
                    ),
                )

            artifact_checks = {
                "em.gro": validate_gro(work_dir / "em.gro"),
                "em.edr": validate_file(str(work_dir / "em.edr"), (".edr",), "Energy output"),
                "em.log": validate_file(str(work_dir / "em.log"), (".log",), "Run log"),
            }
            for filename, result in artifact_checks.items():
                with self.subTest(filename=filename):
                    path = work_dir / filename
                    self.assertTrue(result.ok, result.message)
                    self.assertGreater(path.stat().st_size, 0, f"{filename} is empty")


if __name__ == "__main__":
    unittest.main()
