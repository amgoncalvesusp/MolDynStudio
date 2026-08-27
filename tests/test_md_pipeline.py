from __future__ import annotations

import unittest

from core.md_pipeline import (
    PipelineCommand,
    PipelineStage,
    build_minimize_stage,
    build_npt_stage,
    build_nvt_stage,
    build_pipeline,
    build_prepare_stage,
    build_production_stage,
)
from core.run_manifest import StageName


class MDPipelineTests(unittest.TestCase):
    def test_commands_are_tokenized_and_use_project_as_cwd(self):
        stage = build_nvt_stage("/tmp/project")
        self.assertIsInstance(stage, PipelineStage)
        self.assertEqual(stage.commands[0].args, (
            "grompp", "-f", "nvt.mdp", "-c", "em.gro", "-r", "em.gro",
            "-p", "topol.top", "-o", "nvt.tpr",
        ))
        self.assertEqual(stage.commands[0].cwd, "/tmp/project")
        self.assertIsInstance(stage.commands[0], PipelineCommand)

    def test_stage_commands_use_expected_artifacts(self):
        self.assertEqual(build_prepare_stage("p").commands, ())
        self.assertEqual(build_minimize_stage("p").commands[0].args, (
            "grompp", "-f", "em.mdp", "-c", "system.gro", "-p",
            "topol.top", "-o", "em.tpr",
        ))
        self.assertEqual(build_minimize_stage("p").commands[1].args,
                         ("mdrun", "-deffnm", "em"))
        self.assertEqual(build_npt_stage("p").commands[0].args, (
            "grompp", "-f", "npt.mdp", "-c", "nvt.gro", "-r", "nvt.gro",
            "-p", "topol.top", "-o", "npt.tpr",
        ))
        self.assertEqual(build_production_stage("p").commands[0].args, (
            "grompp", "-f", "md.mdp", "-c", "npt.gro", "-p", "topol.top",
            "-o", "md.tpr",
        ))

    def test_pipeline_has_canonical_dependency_order_and_resume_slice(self):
        all_stages = build_pipeline("p", tuple(StageName))
        self.assertEqual(tuple(stage.name for stage in all_stages), tuple(StageName))
        resumed = build_pipeline("p", tuple(StageName), resume_from=StageName.NPT)
        self.assertEqual(tuple(stage.name for stage in resumed),
                         (StageName.NPT, StageName.PRODUCTION))

    def test_pipeline_does_not_reorder_selected_stages_or_add_maxwarn(self):
        stages = build_pipeline("p", (StageName.MINIMIZATION, StageName.NVT,
                                       StageName.NPT, StageName.PRODUCTION))
        self.assertEqual(tuple(stage.name for stage in stages),
                         (StageName.MINIMIZATION, StageName.NVT,
                          StageName.NPT, StageName.PRODUCTION))
        for stage in stages:
            for command in stage.commands:
                self.assertNotIn("-maxwarn", command.args)


if __name__ == "__main__":
    unittest.main()
