"""Matplotlib/Qt compatibility helpers for the PyQt5 application."""

from __future__ import annotations

import os

from PyQt5.QtWidgets import QWidget


def configure_pyqt5_backend() -> None:
    """Select Matplotlib's PyQt5 adapter before importing a Qt backend.

    Matplotlib can inherit ``QT_API=PySide6`` from a user's environment. That
    creates widgets belonging to a different Qt binding and makes PyQt5
    layouts reject Matplotlib canvases at runtime. MolDynStudio is a PyQt5
    application, so its backend must be explicit and consistent.
    """

    os.environ["QT_API"] = "pyqt5"


def is_pyqt5_widget_type(widget_type: type) -> bool:
    """Return whether a Matplotlib canvas belongs to the PyQt5 widget tree."""

    try:
        return issubclass(widget_type, QWidget)
    except TypeError:
        return False


configure_pyqt5_backend()
