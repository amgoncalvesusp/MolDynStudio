import unittest

from windows.help_dialog import HELP_HTML


class HelpCopyTests(unittest.TestCase):
    def test_md_run_help_describes_real_pipeline(self):
        self.assertIn("launch the selected GROMACS build", HELP_HTML)
        self.assertIn("-cpi", HELP_HTML)
        self.assertIn("GPU driver alone", HELP_HTML)
        self.assertNotIn("monitor minimization, equilibration, and production stages", HELP_HTML)
        self.assertNotIn("conda run -n moldynstudio", HELP_HTML)


if __name__ == "__main__":
    unittest.main()
