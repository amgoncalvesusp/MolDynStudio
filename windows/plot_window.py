"""Detachable matplotlib plot window."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from PyQt5.QtWidgets import QFileDialog, QMainWindow, QToolBar

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
    from matplotlib.figure import Figure
except Exception:  # pragma: no cover
    FigureCanvas = None  # type: ignore[assignment]
    NavigationToolbar2QT = None  # type: ignore[assignment]
    Figure = None  # type: ignore[assignment]


class PlotWindow(QMainWindow):
    def __init__(
        self,
        title: str,
        x: Iterable[float],
        y: Iterable[float],
        xlabel: str,
        ylabel: str,
        plot_type: str = "line",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        if FigureCanvas is None or Figure is None:
            return
        self.figure = Figure(figsize=(7, 5))
        self.canvas = FigureCanvas(self.figure)
        self.setCentralWidget(self.canvas)
        if NavigationToolbar2QT is not None:
            self.addToolBar(NavigationToolbar2QT(self.canvas, self))
        toolbar = QToolBar("Export")
        self.addToolBar(toolbar)
        save_action = toolbar.addAction("Export")
        save_action.triggered.connect(self.export_plot)
        ax = self.figure.add_subplot(111)
        if plot_type == "bar":
            ax.bar(list(x), list(y))
        else:
            ax.plot(list(x), list(y), linewidth=2)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        self.figure.tight_layout()

    def export_plot(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export plot", str(Path.home() / "plot.png"), "Images (*.png *.svg)")
        if path:
            self.figure.savefig(path)

