#!/usr/bin/env python3
"""MolDynStudio entry point.

The launcher (``SetupWizard``) is shown on every startup as a status
dashboard. When all dependencies are green the user clicks ``Begin`` and the
main GUI opens. When something is missing the user can install it from the
same dialog.
"""

from __future__ import annotations

import sys

def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in {"--self-check", "--bundle-check-worker"}:
        if len(sys.argv) != 3:
            raise SystemExit("Expected exactly one report JSON path.")
        from core.bundle_check import run_self_check, worker_main

        check = run_self_check if sys.argv[1] == "--self-check" else worker_main
        return check(sys.argv[2])
    if len(sys.argv) > 1 and sys.argv[1] == "--structure-repair-worker":
        if len(sys.argv) != 3:
            raise SystemExit("Expected exactly one reconstruction request JSON path.")
        from core.repair_worker import main as run_repair_worker

        return run_repair_worker(sys.argv[2])
    from PyQt5.QtWidgets import QApplication, QDialog

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
