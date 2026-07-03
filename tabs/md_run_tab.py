"""MD run control and live-monitoring tab."""

from __future__ import annotations

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from tabs.base import MolDynBasePage

try:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
except Exception:  # pragma: no cover
    FigureCanvas = None  # type: ignore[assignment]
    Figure = None  # type: ignore[assignment]


class MDRunTab(MolDynBasePage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._sample = 0
        self._plots: list[tuple[object, object, list[float], list[float]]] = []

        outer = QVBoxLayout(self)
        title = QLabel("MD Run")
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        control = QGroupBox("Pipeline Control")
        control_layout = QVBoxLayout(control)
        row = QHBoxLayout()
        run = QPushButton("Run All")
        run.clicked.connect(self.start_mock_run)
        row.addWidget(run)
        pause = QPushButton("Pause")
        pause.clicked.connect(lambda: self._timer.stop())
        row.addWidget(pause)
        stop = QPushButton("Stop")
        stop.clicked.connect(self.stop_run)
        row.addWidget(stop)
        self.resume = QComboBox()
        self.resume.addItems(["Minimization", "NVT Equilibration", "NPT Equilibration", "Production MD"])
        row.addWidget(self.resume)
        row.addStretch(1)
        control_layout.addLayout(row)

        self.steps: list[tuple[str, QProgressBar, QLabel]] = []
        for label in ["Minimization", "NVT Equilibration", "NPT Equilibration", "Production MD"]:
            step_row = QHBoxLayout()
            step_row.addWidget(QLabel(label), 1)
            bar = QProgressBar()
            bar.setValue(0)
            step_row.addWidget(bar, 3)
            status = QLabel("Waiting")
            step_row.addWidget(status, 1)
            control_layout.addLayout(step_row)
            self.steps.append((label, bar, status))
        outer.addWidget(control)

        monitor = QGroupBox("Real-Time Monitoring")
        monitor_layout = QGridLayout(monitor)
        self._add_plot(monitor_layout, "Temperature", "K", 0, 0)
        self._add_plot(monitor_layout, "Pressure", "bar", 0, 1)
        self._add_plot(monitor_layout, "Energy", "kJ/mol", 0, 2)
        outer.addWidget(monitor, 1)

        terminal = QGroupBox("Terminal Output")
        terminal_layout = QVBoxLayout(terminal)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(160)
        terminal_layout.addWidget(self.log)
        buttons = QHBoxLayout()
        clear = QPushButton("Clear Log")
        clear.clicked.connect(self.log.clear)
        buttons.addWidget(clear)
        copy = QPushButton("Copy Last Line")
        copy.clicked.connect(self.copy_last_line)
        buttons.addWidget(copy)
        buttons.addStretch(1)
        terminal_layout.addLayout(buttons)
        outer.addWidget(terminal)

    def _add_plot(self, layout: QGridLayout, title: str, ylabel: str, row: int, column: int) -> None:
        if FigureCanvas is None or Figure is None:
            layout.addWidget(QLabel(f"{title} plot requires matplotlib."), row, column)
            return
        figure = Figure(figsize=(3.8, 2.4))
        canvas = FigureCanvas(figure)
        ax = figure.add_subplot(111)
        ax.set_title(title)
        ax.set_xlabel("Step")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.25)
        figure.tight_layout()
        layout.addWidget(canvas, row, column)
        self._plots.append((canvas, ax, [], []))

    def start_mock_run(self) -> None:
        self._sample = 0
        self.log.append("Starting MolDynStudio MD pipeline preview.")
        for _, bar, status in self.steps:
            bar.setValue(0)
            status.setText("Waiting")
        self._timer.start(350)

    def stop_run(self) -> None:
        self._timer.stop()
        self.log.append("Run stopped.")
        for _, _, status in self.steps:
            if status.text() == "Running":
                status.setText("Stopped")

    def _tick(self) -> None:
        self._sample += 1
        active_index = min((self._sample - 1) // 25, len(self.steps) - 1)
        for index, (_, bar, status) in enumerate(self.steps):
            if index < active_index:
                bar.setValue(100)
                status.setText("Done")
            elif index == active_index:
                bar.setValue(min(100, (self._sample % 25) * 4))
                status.setText("Running")
            else:
                bar.setValue(0)
                status.setText("Waiting")
        self.log.append(
            f"Step={self._sample * 500}, t={self._sample:.1f} ps, "
            f"Epot={-420000 + self._sample * 20:.1f}, Temp={299.7 + (self._sample % 5) * 0.1:.1f} K"
        )
        self._update_plots()
        if self._sample >= 100:
            self._timer.stop()
            self.log.append("Pipeline preview completed.")

    def _update_plots(self) -> None:
        values = [299.5 + (self._sample % 7) * 0.15, 1.0 + (self._sample % 5) * 0.02, -420000 + self._sample * 20]
        for idx, (canvas, ax, xs, ys) in enumerate(self._plots):
            xs.append(float(self._sample))
            ys.append(float(values[idx]))
            del xs[:-50]
            del ys[:-50]
            ax.clear()
            ax.plot(xs, ys, color="#E87722", linewidth=2)
            ax.grid(True, alpha=0.25)
            canvas.draw_idle()

    def copy_last_line(self) -> None:
        text = self.log.toPlainText().strip().splitlines()
        if text:
            self.log.copy()
            self.request_log.emit(text[-1])

    def preview_text(self) -> str:
        return "[MD Run Preview]\nPipeline: Minimization -> NVT -> NPT -> Production MD\nRuntime commands will use conda run -n moldynstudio."

