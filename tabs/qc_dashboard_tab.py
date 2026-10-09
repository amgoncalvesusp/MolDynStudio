"""QC and convergence dashboard widget."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFileDialog,
    QAbstractItemView,
    QComboBox,
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

from core.matplotlib_qt import configure_pyqt5_backend, is_pyqt5_widget_type
from analysis.qc import (
    QCSummary,
    compare_replicates,
    parse_numeric_series,
    recommended_analysis_start,
    summarize_series,
)

try:
    configure_pyqt5_backend()
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    if not is_pyqt5_widget_type(FigureCanvas):
        raise ImportError("Matplotlib selected a non-PyQt5 Qt binding")
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
        header = QLabel("QC / Stability Screening")
        header.setObjectName("PageTitle")
        outer.addWidget(header)

        buttons = QHBoxLayout()
        load = QPushButton("Add XVG/CSV Files")
        load.clicked.connect(self.add_files)
        buttons.addWidget(load)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        buttons.addWidget(clear)
        self.plot_metric = QComboBox()
        self.plot_metric.setAccessibleName("Metric to plot")
        self.plot_metric.currentIndexChanged.connect(self._draw_plot)
        buttons.addWidget(self.plot_metric)
        buttons.addStretch(1)
        outer.addLayout(buttons)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.table = QTableWidget(0, 9)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setHorizontalHeaderLabels(
            [
                "Metric",
                "Replica",
                "Points",
                "Final mean",
                "Final SD",
                "Drift",
                "Slope",
                "Heuristic start (ns)",
                "Status",
            ]
        )
        left_layout.addWidget(self.table, 2)

        summary_group = QGroupBox("Screening Summary")
        summary_layout = QVBoxLayout(summary_group)
        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMinimumHeight(160)
        self.summary.setPlainText("Load GROMACS .xvg or numeric .csv files to generate QC summaries.")
        summary_layout.addWidget(self.summary)
        left_layout.addWidget(summary_group, 1)
        splitter.addWidget(left)

        right = QGroupBox("Measured Series")
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
        by_source = {str(Path(item.source).resolve()): item for item in (*self.series, *loaded)}
        self.series = list(by_source.values())
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
                x_unit=item.x_unit,
            )
            for item in self.series
        )
        self._populate_table()
        self._write_summary()
        selected = self.plot_metric.currentText()
        self.plot_metric.blockSignals(True)
        self.plot_metric.clear()
        self.plot_metric.addItems(sorted({item.metric for item in self.series}))
        if selected:
            self.plot_metric.setCurrentText(selected)
        self.plot_metric.blockSignals(False)
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
        worst = max((s.status for s in self.summaries), key={"OK": 0, "WARN": 1, "FAIL": 2}.get)
        lines = [
            f"Overall status: {worst}",
            "Heuristic analysis start: "
            + ("not determined" if start is None else f"{start:.4g} ns"),
            "",
            "Metric checks:",
        ]
        for summary in self.summaries:
            lines.append(
                f"- {summary.metric} / {summary.replicate}: {summary.status} - {summary.message}"
            )

        grouped: dict[tuple[str, str, str], list[QCSummary]] = defaultdict(list)
        for series, summary in zip(self.series, self.summaries):
            if summary.status != "FAIL" and series.value_unit != "unknown":
                grouped[(summary.metric, series.x_unit, series.value_unit)].append(summary)
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
                "Heuristic screening is not evidence of convergence or binding affinity. "
                "Inspect topology, periodic boundaries, the expected ensemble and independent replicas. "
                "Unknown units prevent a shared start recommendation. Distinct files do not establish independent replicas.",
            ]
        )
        self.summary.setPlainText("\n".join(lines))

    def _draw_plot(self) -> None:
        if self.figure is None or self.canvas is None:
            return
        self.figure.clear()
        selected = self.plot_metric.currentText()
        # Keep residue profiles separate even when a time metric is selected.
        visible = [item for item in self.series if item.metric == selected or item.x_unit == "residue"]
        groups = defaultdict(list)
        for item in visible:
            groups[(item.metric, item.x_unit, item.value_unit)].append(item)
        if not groups:
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, "Load measured XVG or CSV data", ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()
        for index, ((metric, x_unit, value_unit), items) in enumerate(groups.items(), 1):
            ax = self.figure.add_subplot(len(groups), 1, index)
            for item in items:
                ax.plot(item.time, item.values, linewidth=1.5, label=item.replicate)
            ax.set_xlabel("Residue" if x_unit == "residue" else f"Time ({x_unit})")
            ax.set_ylabel(metric if value_unit == "unknown" else f"{metric} ({value_unit})")
            ax.grid(True, alpha=0.3)
            ax.legend(fontsize=8)
        self.figure.tight_layout()
        self.canvas.draw_idle()
