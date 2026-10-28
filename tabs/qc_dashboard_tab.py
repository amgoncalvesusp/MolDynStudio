"""QC and convergence dashboard widget."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
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
from analysis.result_links import load_result_rows
from core.matplotlib_qt import configure_pyqt5_backend, is_pyqt5_widget_type

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

    analysis_point_requested = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.series = []
        self.summaries: tuple[QCSummary, ...] = ()
        self._plot_sources = {}
        self._selected_point = None
        self._point_marker = None
        self._point_linked = False
        self._point_data = None
        self._imported_points = {}

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
        self.points_series = QComboBox()
        self.points_series.setAccessibleName("Measured series source file")
        self.points_series.currentIndexChanged.connect(self._select_points_series)
        right_layout.addWidget(self.points_series)
        self.measurement_tabs = QTabWidget()
        right_layout.addWidget(self.measurement_tabs, 1)
        if FigureCanvas is not None and Figure is not None:
            self.figure = Figure(figsize=(6, 4))
            self.canvas = FigureCanvas(self.figure)
            self.canvas.mpl_connect("pick_event", self._on_pick)
            self.measurement_tabs.addTab(self.canvas, "Plot")
        else:
            self.figure = None
            self.canvas = None
            self.measurement_tabs.addTab(QLabel("Matplotlib is not available."), "Plot")
        self.points_table = QTableWidget(0, 0)
        self.points_table.setAccessibleName("Measured points in source file order")
        self.points_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.points_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.points_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.points_table.cellDoubleClicked.connect(self._activate_table_point)
        self.points_table.currentCellChanged.connect(self._point_row_changed)
        self.measurement_tabs.addTab(self.points_table, "Measured points")
        self.point_button = QPushButton("Show selected point in 3D")
        self.point_button.setEnabled(False)
        self.point_button.clicked.connect(lambda: self._activate_table_point(self.points_table.currentRow()))
        right_layout.addWidget(self.point_button)
        self.link_status = QLabel("Choose a measured point to inspect its frame or RMSF residue in 3D.")
        self.link_status.setTextFormat(Qt.PlainText)
        self.link_status.setWordWrap(True)
        right_layout.addWidget(self.link_status)
        self.table.currentCellChanged.connect(self._select_summary_series)
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
                try:
                    points = self._linked_rows(series)
                except (OSError, ValueError):
                    points = None
                self._imported_points = {**self._imported_points, series.source: points}
            except (OSError, ValueError) as exc:
                errors.append(f"{Path(path).name}: {exc}")
        by_source = {str(Path(item.source).resolve()): item for item in (*self.series, *loaded)}
        self.series = list(by_source.values())
        self.recalculate()
        if errors:
            self.summary.append("\nSkipped files:\n" + "\n".join(errors))

    def clear(self) -> None:
        self.series = []
        self._imported_points = {}
        self.summaries = ()
        self.table.setRowCount(0)
        self.points_series.clear()
        self._select_points_series(-1)
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
        selected_source = self.points_series.currentData()
        self.points_series.blockSignals(True)
        self.points_series.clear()
        for item in self.series:
            self.points_series.addItem(f"{item.metric} / {item.replicate} — {Path(item.source).name}", item.source)
            self.points_series.setItemData(self.points_series.count() - 1, item.source, Qt.ToolTipRole)
        index = self.points_series.findData(selected_source)
        self.points_series.setCurrentIndex(max(0, index) if self.series else -1)
        self.points_series.blockSignals(False)
        self._select_points_series(self.points_series.currentIndex())

    def _select_summary_series(self, row, *_):
        if 0 <= row < self.points_series.count():
            self.points_series.setCurrentIndex(row)

    def _select_points_series(self, index):
        self._selected_point = None
        self._point_linked = False
        self._point_data = None
        self.point_button.setEnabled(False)
        self.points_table.setRowCount(0)
        if not 0 <= index < len(self.series):
            self.points_table.setColumnCount(0)
            self.link_status.setText("Load a measured series to inspect its points.")
            self._draw_plot()
            return
        series = self.series[index]
        try:
            columns, rows = self._linked_rows(series)
            if (columns, rows) != self._imported_points.get(series.source):
                raise ValueError("Result mapping changed. Add the file again before 3D inspection.")
            self._point_data = (columns, rows)
            self._point_linked = True
            self.link_status.setText("Select a row or click one plotted point. Source hashes are checked before 3D inspection.")
        except (OSError, ValueError) as exc:
            columns = ("Residue" if series.x_unit == "residue" else f"Time ({series.x_unit})",
                       series.metric if series.value_unit == "unknown" else f"{series.metric} ({series.value_unit})")
            rows = tuple(zip(series.time, series.values))
            self.link_status.setText(f"3D link unavailable: {exc} Data remain readable.")
        self.points_table.setColumnCount(len(columns) + 1)
        self.points_table.setHorizontalHeaderLabels(["Row (0-based)", *columns])
        self.points_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate((row, *values)):
                self.points_table.setItem(row, column, QTableWidgetItem(format(value, ".10g")))
        self.points_table.resizeColumnsToContents()
        self._draw_plot()

    def _point_row_changed(self, row, *_):
        self.point_button.setEnabled(self._point_linked and 0 <= row < self.points_table.rowCount())

    @staticmethod
    def _linked_rows(series):
        columns, rows, _, _ = load_result_rows(series.source)
        if tuple((row[0], row[1]) for row in rows) != tuple(zip(series.time, series.values)):
            raise ValueError("Result changed since plotting. Add the file again to refresh its measured points.")
        return columns, rows

    def _activate_table_point(self, row, *_):
        self._activate_point(self.points_series.currentIndex(), row)

    def _activate_point(self, series_index, row):
        if not 0 <= series_index < len(self.series) or not 0 <= row < len(self.series[series_index].time):
            return
        self.points_series.setCurrentIndex(series_index)
        series = self.series[series_index]
        self.points_table.setCurrentCell(row, 0)
        self.points_table.selectRow(row)
        self._selected_point = (series.source, row)
        self.plot_metric.setCurrentText(series.metric)
        self._draw_plot()
        if self._point_linked:
            try:
                if self._linked_rows(series) != self._point_data:
                    raise ValueError("Result mapping changed. Add the file again before 3D inspection.")
            except (OSError, ValueError) as exc:
                self._point_linked = False
                self.point_button.setEnabled(False)
                self.link_status.setText(f"3D link unavailable: {exc}")
                return
            self.link_status.setText(f"Inspecting {Path(series.source).name}, numeric row {row}; validating source files…")
            self.analysis_point_requested.emit(series.source, row)

    def _on_pick(self, event):
        index = self._plot_sources.get(event.artist)
        if index is None or not len(event.ind):
            return
        mouse = getattr(event, "mouseevent", None)
        if mouse is not None and mouse.button != 1:
            return
        other_series = mouse is not None and any(
            line is not event.artist and line.axes is event.artist.axes
            and line.get_visible() and line.contains(mouse)[0] for line in self._plot_sources
        )
        if len(event.ind) != 1 or other_series:
            if not other_series:
                self.points_series.setCurrentIndex(index)
            self.link_status.setText("Several measured points overlap. Select the source file and exact row in Measured points.")
            self.measurement_tabs.setCurrentWidget(self.points_table)
            return
        self._activate_point(index, int(event.ind[0]))

    def _populate_table(self) -> None:
        self.table.blockSignals(True)
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
        self.table.blockSignals(False)

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
        self._plot_sources = {}
        self._point_marker = None
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
                line, = ax.plot(item.time, item.values, linewidth=1.5, label=item.replicate, picker=5)
                self._plot_sources[line] = next(i for i, series in enumerate(self.series) if series is item)
                if self._selected_point is not None and self._selected_point[0] == item.source:
                    row = self._selected_point[1]
                    self._point_marker, = ax.plot([item.time[row]], [item.values[row]], "o", color="crimson", markersize=8)
            ax.set_xlabel("Residue" if x_unit == "residue" else f"Time ({x_unit})")
            ax.set_ylabel(metric if value_unit == "unknown" else f"{metric} ({value_unit})")
            ax.grid(True, alpha=0.3)
            ax.legend(fontsize=8)
        # Docking can temporarily collapse a hidden canvas to zero height.
        if self.figure.bbox.width > 0 and self.figure.bbox.height > 0:
            self.figure.tight_layout()
        self.canvas.draw_idle()
