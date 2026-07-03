#!/usr/bin/env python3
"""Launch a MolDynStudio dependency check."""

from __future__ import annotations

import argparse
import sys

from PyQt5.QtWidgets import QApplication

from core.environment_manager import DependencyChecker
from windows.splash_screen import SplashScreen


def run_gui(auto_install: bool) -> int:
    app = QApplication(sys.argv)
    splash = SplashScreen()
    checker = DependencyChecker(auto_install=auto_install)
    checker.status_update.connect(splash.add_status)
    checker.command_preview.connect(lambda command: splash.status.append("Install command: " + command))
    checker.all_done.connect(splash.finish)
    checker.finished.connect(lambda: None)
    splash.show()
    checker.start()
    return app.exec_()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check MolDynStudio dependencies.")
    parser.add_argument("--auto-install", action="store_true", help="Install missing conda/pip packages.")
    args = parser.parse_args()
    return run_gui(args.auto_install)


if __name__ == "__main__":
    raise SystemExit(main())

