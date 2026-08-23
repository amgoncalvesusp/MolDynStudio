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
