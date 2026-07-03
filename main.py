#!/usr/bin/env python3
"""MolDynStudio entry point.

The launcher (``SetupWizard``) is shown on every startup as a status
dashboard. When all dependencies are green the user clicks ``Begin`` and the
main GUI opens. When something is missing the user can install it from the
same dialog.
"""

from __future__ import annotations

import sys

from PyQt5.QtWidgets import QApplication, QDialog


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("MolDynStudio")

    from setup_wizard import SetupWizard

    launcher = SetupWizard()
    if launcher.exec_() != QDialog.Accepted:
        return 0

    from gromacs_analysis_studio_v11 import main as run_studio

    return run_studio()


if __name__ == "__main__":
    raise SystemExit(main())
