from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.system_prep import (
    FORCE_FIELD_MAP,
    SystemPrepParams,
    SystemPrepWorker,
    build_ions_mdp,
)


class SystemPrepTests(unittest.TestCase):
    def test_charmm36m_maps_to_current_official_release(self):
        self.assertEqual(
            FORCE_FIELD_MAP["CHARMM36m"],
            "charmm36-feb2026_cgenff-5.0",
        )
        self.assertEqual(
            FORCE_FIELD_MAP["CHARMM36"],
            FORCE_FIELD_MAP["CHARMM36m"],
        )

    def test_charmm_ions_mdp_uses_recommended_nonbonded_settings(self):
        content = build_ions_mdp("CHARMM36m")

        self.assertIn("vdw-modifier  = force-switch", content)
        self.assertIn("rvdw-switch   = 1.0", content)
        self.assertIn("rvdw          = 1.2", content)
        self.assertIn("rcoulomb      = 1.2", content)
        self.assertIn("DispCorr      = no", content)

    def test_charmm_system_prep_uses_charmm_ion_residue_names(self):
        params = SystemPrepParams(
            pdb_path="protein.pdb",
            work_dir="project",
            force_field="CHARMM36m",
        )

        genion = SystemPrepWorker(params)._steps()[-1][1]

        self.assertEqual(genion[genion.index("-pname") + 1], "SOD")
        self.assertEqual(genion[genion.index("-nname") + 1], "CLA")

    def test_ion_grompp_does_not_suppress_warnings(self):
        worker = SystemPrepWorker(
            SystemPrepParams(pdb_path="protein.pdb", work_dir="project")
        )

        grompp = next(args for description, args in worker._steps() if args[0] == "grompp")

        self.assertNotIn("-maxwarn", grompp)
        self.assertNotIn("5", grompp)

    def test_grompp_failure_includes_recent_output(self):
        worker = SystemPrepWorker(
            SystemPrepParams(pdb_path="protein.pdb", work_dir="project")
        )
        result: list[tuple[bool, str]] = []
        worker.done.connect(lambda ok, message: result.append((ok, message)))
        process = mock.Mock()
        process.stdout = iter(
            [
                "irrelevant startup output\n",
                "WARNING 1 [file ions.mdp, line 4]: suspicious setting\n",
                "Fatal error: cannot continue\n",
            ]
        )
        process.wait.return_value = 1

        with (
            mock.patch("core.system_prep.ensure_noncovalent_complex"),
            mock.patch.object(worker, "_ensure_force_field"),
            mock.patch.object(worker, "_ensure_ions_mdp"),
            mock.patch.object(
                worker,
                "_steps",
                return_value=[("Preparing ion addition (grompp)...", ["grompp"])],
            ),
            mock.patch("core.system_prep.wsl_bridge.win_to_wsl", return_value="/tmp/protein.pdb"),
            mock.patch("core.system_prep.wsl_bridge.gmx_popen", return_value=process),
        ):
            worker.run()

        self.assertFalse(result[-1][0])
        self.assertIn("WARNING 1", result[-1][1])
        self.assertIn("Fatal error", result[-1][1])

    def test_current_charmm_force_field_is_staged_from_local_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = SystemPrepWorker(
                SystemPrepParams(
                    pdb_path="protein.pdb",
                    work_dir=tmp,
                    force_field="CHARMM36m",
                )
            )
            expected = Path(tmp, "charmm36-feb2026_cgenff-5.0.ff")
            with mock.patch(
                "core.system_prep.stage_charmm36_force_field",
                return_value=expected,
            ) as stage:
                worker._ensure_force_field()

            stage.assert_called_once_with(Path(tmp))

    def test_replaces_stale_ions_mdp_when_force_field_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ions_mdp = Path(tmp, "ions.mdp")
            ions_mdp.write_text("stale AMBER settings", encoding="utf-8")
            worker = SystemPrepWorker(
                SystemPrepParams(
                    pdb_path="protein.pdb",
                    work_dir=tmp,
                    force_field="CHARMM36m",
                )
            )

            worker._ensure_ions_mdp()

            content = ions_mdp.read_text(encoding="utf-8")
            self.assertNotIn("stale", content)
            self.assertIn("vdw-modifier  = force-switch", content)

    def test_rejects_unc_input_before_reading_the_remote_file(self):
        worker = SystemPrepWorker(
            SystemPrepParams(
                pdb_path=r"\\server\share\protein.pdb",
                work_dir="project",
                force_field="CHARMM36m",
            )
        )
        result: list[tuple[bool, str]] = []
        worker.done.connect(lambda ok, message: result.append((ok, message)))

        with mock.patch(
            "core.system_prep.ensure_noncovalent_complex",
        ) as validate_structure:
            worker.run()

        validate_structure.assert_not_called()
        self.assertFalse(result[-1][0])
        self.assertIn("UNC", result[-1][1])


if __name__ == "__main__":
    unittest.main()
