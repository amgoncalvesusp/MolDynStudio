"""QC and convergence dashboard widget."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from analysis.qc import (
    QCSummary,
    compare_replicates,
    parse_numeric_series,
    recommended_analysis_start,
    summarize_series,
)

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
except Exception:  # pragma: no cover
    FigureCanvas = None  # type: ignore[assignment]
    Figure = None  # type: ignore[assignment]


class QCDashboardWidget(QWidget):
    """Load XVG/CSV time series and summarize MD stability."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.series = []
        self.summaries: tuple[QCSummary, ...] = ()

        outer = QVBoxLayout(self)
        header = QLabel("QC / Convergence Dashboard")
        header.setObjectName("PageTitle")
        outer.addWidget(header)

        buttons = QHBoxLayout()
        load = QPushButton("Add XVG/CSV Files")
        load.clicked.connect(self.add_files)
        buttons.addWidget(load)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Metric",
                "Replica",
                "Points",
                "Final mean",
                "Final SD",
                "Drift",
                "Slope",
                "Burn-in",
                "Status",
            ]
        )
        left_layout.addWidget(self.table, 2)

        summary_group = QGroupBox("Decision Summary")
        summary_layout = QVBoxLayout(summary_group)
        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMinimumHeight(160)
        self.summary.setPlainText("Load GROMACS .xvg or numeric .csv files to generate QC summaries.")
        summary_layout.addWidget(self.summary)
        left_layout.addWidget(summary_group, 1)
        splitter.addWidget(left)

        right = QGroupBox("Time Series")
        right_layout = QVBoxLayout(right)
        if FigureCanvas is not None and Figure is not None:
            self.figure = Figure(figsize=(6, 4))
            self.canvas = FigureCanvas(self.figure)
            right_layout.addWidget(self.canvas)
        else:
            self.figure = None
            self.canvas = None
            right_layout.addWidget(QLabel("Matplotlib is not available."))
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Load QC time series",
            str(Path.home()),
            "Time series (*.xvg *.csv *.dat *.txt);;All Files (*)",
        )
        if paths:
            self.load_files(paths)

    def load_files(self, paths: list[str] | tuple[str, ...]) -> None:
        loaded = []
        errors = []
        for path in paths:
            try:
                series = parse_numeric_series(path)
                loaded.append(series)
            except (OSError, ValueError) as exc:
                errors.append(f"{Path(path).name}: {exc}")
        self.series = [*self.series, *loaded]
        self.recalculate()
        if errors:
            self.summary.append("\nSkipped files:\n" + "\n".join(errors))

    def clear(self) -> None:
        self.series = []
        self.summaries = ()
        self.table.setRowCount(0)
        self.summary.setPlainText("Load GROMACS .xvg or numeric .csv files to generate QC summaries.")
        self._draw_plot()

    def recalculate(self) -> None:
        self.summaries = tuple(
            summarize_series(
                item.metric,
                item.time,
                item.values,
                source=item.source,
                replicate=item.replicate,
            )
            for item in self.series
        )
        self._populate_table()
        self._write_summary()
        self._draw_plot()

    def _populate_table(self) -> None:
        self.table.setRowCount(0)
        for summary in self.summaries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                summary.metric,
                summary.replicate,
                str(summary.n_points),
                f"{summary.final_mean:.4g}",
                f"{summary.final_std:.4g}",
                f"{summary.drift:.4g}",
                f"{summary.slope:.4g}",
                "-" if summary.burn_in_time is None else f"{summary.burn_in_time:.4g}",
                summary.status,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 8:
                    item.setToolTip(summary.message)
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()

    def _write_summary(self) -> None:
        if not self.summaries:
            self.summary.setPlainText("Load GROMACS .xvg or numeric .csv files to generate QC summaries.")
            return

        start = recommended_analysis_start(self.summaries)
        worst = "OK" if all(s.status == "OK" for s in self.summaries) else "WARN"
        lines = [
            f"Overall status: {worst}",
            "Recommended analysis start: "
            + ("not determined" if start is None else f"{start:.4g} time units"),
            "",
            "Metric checks:",
        ]
        for summary in self.summaries:
            lines.append(
                f"- {summary.metric} / {summary.replicate}: {summary.status} - {summary.message}"
            )

        grouped: dict[str, list[QCSummary]] = defaultdict(list)
        for summary in self.summaries:
            grouped[summary.metric].append(summary)
        comparisons = tuple(compare_replicates(items) for items in grouped.values() if len(items) > 1)
        if comparisons:
            lines.extend(["", "Replicate comparison:"])
            for comparison in comparisons:
                lines.append(
                    f"- {comparison.metric}: {comparison.status} - {comparison.message} "
                    f"(relative spread {comparison.relative_spread:.2%})"
                )
        lines.extend(
            [
                "",
                "Interpretation caveat: this dashboard checks numeric stability, not physical correctness. "
                "Always inspect trajectory quality, topology, PBC handling, and expected ensemble behavior.",
            ]
        )
        self.summary.setPlainText("\n".join(lines))

    def _draw_plot(self) -> None:
        if self.figure is None or self.canvas is None:
            return
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        for item in self.series[:8]:
            ax.plot(item.time, item.values, linewidth=1.5, label=f"{item.metric} / {item.replicate}")
        ax.set_xlabel("Time")
        ax.set_ylabel("Value")
        ax.set_title("Loaded QC time series")
        ax.grid(True, alpha=0.3)
        if self.series:
            ax.legend(fontsize=8)
        self.figure.tight_layout()
        self.canvas.draw_idle()
