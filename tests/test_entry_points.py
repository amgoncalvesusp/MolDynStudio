from __future__ import annotations

import unittest
from unittest import mock

import gromacs_analysis_studio_v11 as studio


class StudioMainTests(unittest.TestCase):
    def test_release_metadata_includes_inventor(self):
        self.assertEqual(
            studio.APP_INVENTOR,
            "Adriano Marques Gonçalves (UNIARA)",
        )

    def test_main_reuses_existing_qapplication_and_returns_exec_code(self):
        fake_app = mock.Mock()
        fake_app.exec_.return_value = 7

        class FakeQApplication:
            constructed = 0

            @staticmethod
            def instance():
                return fake_app

            def __new__(cls, *_args, **_kwargs):
                cls.constructed += 1
                return fake_app

        fake_window = mock.Mock()

        with (
            mock.patch.object(studio, "QApplication", FakeQApplication),
            mock.patch.object(studio, "MainWindow", return_value=fake_window),
        ):
            result = studio.main()

        self.assertEqual(result, 7)
        self.assertEqual(FakeQApplication.constructed, 0)
        fake_app.setApplicationName.assert_called_once_with(studio.APP_NAME)
        fake_window.show.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
