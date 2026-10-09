"""Native, disk-backed trajectory frame inspection and DockLens handoff."""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QPushButton, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from core.matplotlib_qt import configure_pyqt5_backend
from core.settings import SettingsStore
from core.trajectory_frames import FrameExport, FrameSnapshot, TrajectorySession, launch_docklens
from tabs.base import PathSelector

configure_pyqt5_backend()
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

LOGGER = logging.getLogger(__name__)


class _FrameWorker(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.succeeded.emit(self.operation())
        except Exception as exc:  # Qt boundary: retain the traceback in logs.
            LOGGER.exception("Trajectory frame operation failed")
            self.failed.emit(str(exc) or type(exc).__name__)


class TrajectoryViewerWindow(QMainWindow):
    def __init__(self, topology: str, trajectory: str, parent=None, *, settings=None):
        super().__init__(parent)
        self.setWindowTitle("Trajectory frames")
        self.resize(1120, 780)
        self.topology, self.trajectory = topology, trajectory
        self.settings = settings or (getattr(parent.window(), "settings", None) if parent else None)
        self.settings = self.settings or SettingsStore()
        self._session = None
        self._worker = None
        self._export = None
        self._operation = ""
        self._build_ui()
        self._set_busy(False)
        QTimer.singleShot(0, self.load_trajectory)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        title = QLabel("Inspect a frame, then review its interactions")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        source = QLabel(f"Topology: {self.topology}\nTrajectory: {self.trajectory}")
        source.setWordWrap(True)
        source.setTextFormat(Qt.PlainText)
        layout.addWidget(source)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)
        controls = QWidget()
        left = QVBoxLayout(controls)
        left.setContentsMargins(0, 0, 8, 0)
        frame_box = QGroupBox("1. Inspect coordinates")
        form = QFormLayout(frame_box)
        self.frame_spin = QSpinBox()
        self.frame_spin.setRange(0, 0)
        form.addRow("Frame index (0-based)", self.frame_spin)
        self.receptor_edit = QLineEdit("protein")
        self.ligand_edit = QLineEdit("resname LIG")
        for edit in (self.receptor_edit, self.ligand_edit):
            edit.setToolTip("MDAnalysis atom selection. Update selections, then choose Show frame.")
        form.addRow("Receptor selection", self.receptor_edit)
        form.addRow("Ligand selection", self.ligand_edit)
        row = QHBoxLayout()
        self.reload_button = QPushButton("Reload trajectory")
        self.reload_button.clicked.connect(self.load_trajectory)
        self.show_button = QPushButton("Show frame")
        self.show_button.clicked.connect(self.show_frame)
        row.addWidget(self.reload_button)
        row.addWidget(self.show_button)
        form.addRow(row)
        self.frame_label = QLabel("No frame loaded.")
        self.frame_label.setWordWrap(True)
        self.frame_label.setMinimumHeight(self.frame_label.fontMetrics().lineSpacing() * 3)
        form.addRow(self.frame_label)
        left.addWidget(frame_box)
        left.addWidget(self._export_controls())
        left.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(controls)
        scroll.setMinimumWidth(360)
        splitter.addWidget(scroll)
        figure = Figure(figsize=(6, 5), constrained_layout=True)
        self.canvas = FigureCanvasQTAgg(figure)
        self.axes = figure.add_subplot(111, projection="3d")
        self.axes.set_axis_off()
        splitter.addWidget(self.canvas)
        splitter.setSizes([420, 680])
        self.status = QLabel("Loading trajectory metadata…")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.frame_spin.valueChanged.connect(self._invalidate_export)
        self.receptor_edit.textChanged.connect(self._invalidate_export)
        self.ligand_edit.textChanged.connect(self._invalidate_export)

    def _export_controls(self):
        box = QGroupBox("2. Export and open in DockLens")
        form = QFormLayout(box)
        note = QLabel(
            "Coordinates stay unchanged. Review PBC/centering before export. "
            "PDB loses bond orders and may lack formal charges; check chemical typing in DockLens."
        )
        note.setWordWrap(True)
        form.addRow(note)
        self.pbc_check = QCheckBox("I have reviewed periodic boundaries and kept the\nreceptor and ligand in the same image.")
        self.pbc_check.toggled.connect(lambda: self._set_busy(self.is_busy()))
        form.addRow(self.pbc_check)
        self.output = PathSelector("Frame export folder", directory=True)
        self.output.setText(str(Path(self.trajectory).expanduser().parent / "frame_exports"))
        form.addRow("Output folder", self.output)
        self.profile = QComboBox()
        for label, value in (("PLIP (legacy default)", "plip"), ("LUNA 0.14 defaults", "luna"),
                             ("DSV-like", "dsv"), ("LUNA × DSV conservative", "luna_dsv")):
            self.profile.addItem(label, value)
        form.addRow("Scientific profile", self.profile)
        self.export_button = QPushButton("Export current frame")
        self.export_button.clicked.connect(self.export_frame)
        form.addRow(self.export_button)
        self.binary = PathSelector("Installed DockLens executable", "Executables (*.exe);;All files (*)")
        self.binary.setText(str(self.settings.value("docklens_binary", "")))
        form.addRow("DockLens executable", self.binary)
        self.launch_button = QPushButton("Open exported frame in DockLens")
        self.launch_button.clicked.connect(self.open_docklens)
        form.addRow(self.launch_button)
        hint = QLabel("Requires DockLens with --manifest support. In DockLens, choose Evidence → Molecular dynamics frames.")
        hint.setWordWrap(True)
        form.addRow(hint)
        self.profile.currentIndexChanged.connect(self._invalidate_export)
        return box

    def is_busy(self) -> bool:
        # Pending delivery of a worker result also keeps this window alive.
        return self._worker is not None

    def _set_busy(self, busy):
        loaded = self._session is not None
        for widget in (self.frame_spin, self.receptor_edit, self.ligand_edit,
                       self.reload_button, self.profile, self.pbc_check, self.output):
            widget.setEnabled(not busy)
        self.show_button.setEnabled(not busy and loaded)
        self.export_button.setEnabled(not busy and loaded and self.pbc_check.isChecked())
        self.launch_button.setEnabled(not busy and self._export is not None)

    def _invalidate_export(self, *_):
        self._export = None
        self.pbc_check.setChecked(False)
        self.launch_button.setEnabled(False)

    def _start(self, name, operation):
        if self.is_busy():
            return
        self._operation = name
        self._set_busy(True)
        self.status.setText(f"{name.capitalize()} in progress…")
        self._worker = _FrameWorker(operation, self)
        self._worker.succeeded.connect(self._succeeded, Qt.QueuedConnection)
        self._worker.failed.connect(self._failed, Qt.QueuedConnection)
        self._worker.finished.connect(self._finished, Qt.QueuedConnection)
        self._worker.start()

    def load_trajectory(self):
        if self.is_busy():
            return
        self._invalidate_export()
        old_session = self._session
        self._session = None
        receptor, ligand = self.receptor_edit.text(), self.ligand_edit.text()

        def load():
            if old_session is not None:
                old_session.close()
            session = TrajectorySession.open(self.topology, self.trajectory)
            try:
                return session, session.read_frame(0, receptor, ligand)
            except Exception:
                session.close()
                raise

        self._start("Loading trajectory", load)

    def show_frame(self):
        if self._session is None:
            return
        index, receptor, ligand = self.frame_spin.value(), self.receptor_edit.text(), self.ligand_edit.text()
        self._start("Reading frame", lambda: self._session.read_frame(index, receptor, ligand))

    def export_frame(self):
        if self._session is None or not self.pbc_check.isChecked():
            return
        args = dict(
            output_dir=self.output.text(), frame_index=self.frame_spin.value(),
            receptor_selection=self.receptor_edit.text(), ligand_selection=self.ligand_edit.text(),
            pbc_reviewed=self.pbc_check.isChecked(), profile=self.profile.currentData(),
        )
        self._start("Exporting frame and hashing sources", lambda: self._session.export_frame(**args))

    @pyqtSlot(object)
    def _succeeded(self, result):
        if isinstance(result, tuple):
            self._session, snapshot = result
            self._show_snapshot(snapshot)
        elif isinstance(result, FrameSnapshot):
            self._show_snapshot(result)
        elif isinstance(result, FrameExport):
            self._export = result
            self.status.setText(f"Exported frame {result.frame_index}: {result.directory}\nSource hashes and atom mapping: provenance.json")

    @pyqtSlot(str)
    def _failed(self, message):
        self.status.setText(f"{self._operation} failed: {message}")

    @pyqtSlot()
    def _finished(self):
        worker = self._worker
        self._worker = None
        self._set_busy(False)
        if worker is not None:
            worker.deleteLater()

    def _show_snapshot(self, snapshot):
        self.frame_spin.blockSignals(True)
        self.frame_spin.setMaximum(snapshot.n_frames - 1)
        self.frame_spin.setValue(snapshot.frame_index)
        self.frame_spin.blockSignals(False)
        time = f"{snapshot.time_ps:g} ps" if snapshot.time_ps is not None else "unavailable"
        self.frame_label.setText(
            f"Frame {snapshot.frame_index} of {snapshot.n_frames} · Time: {time}\n"
            f"Receptor: {snapshot.receptor_count} atoms · Ligand: {snapshot.ligand_count} atoms\n"
            "Time comes from the trajectory reader; verify timestep and origin."
        )
        self.axes.clear()
        for coordinates, color, label in (
            (snapshot.receptor_coordinates, "#247BA0", "Receptor"),
            (snapshot.ligand_coordinates, "#D55E00", "Ligand"),
        ):
            if coordinates:
                x, y, z = zip(*coordinates)
                self.axes.scatter(x, y, z, c=color, s=8, label=label, alpha=0.8)
        self.axes.set(xlabel="X (Å)", ylabel="Y (Å)", zlabel="Z (Å)", title=f"Frame {snapshot.frame_index}")
        if snapshot.receptor_coordinates or snapshot.ligand_coordinates:
            self.axes.legend(loc="upper right")
            limits = [self.axes.get_xlim3d(), self.axes.get_ylim3d(), self.axes.get_zlim3d()]
            radius = max(max(high - low, 1.0) for low, high in limits) / 2
            centers = [(low + high) / 2 for low, high in limits]
            self.axes.set_xlim3d(centers[0] - radius, centers[0] + radius)
            self.axes.set_ylim3d(centers[1] - radius, centers[1] + radius)
            self.axes.set_zlim3d(centers[2] - radius, centers[2] + radius)
            self.axes.set_box_aspect((1, 1, 1))
        self.canvas.draw_idle()
        self.status.setText(
            "Frame loaded. Rotate the point view with the mouse. "
            + ("Display reduced to about 6,000 atoms; export keeps every selected atom." if snapshot.display_reduced else "Every selected atom is shown.")
        )

    def open_docklens(self):
        if self._export is None:
            return
        try:
            launch_docklens(self.binary.text(), self._export.manifest_path)
            self.settings.set_value("docklens_binary", self.binary.text())
            self.status.setText("DockLens launch requested. Choose Evidence → Molecular dynamics frames; inspect Input QC and chemical typing.")
        except (ValueError, OSError) as exc:
            self.status.setText(f"DockLens could not start: {exc}")

    def closeEvent(self, event):
        if self.is_busy():
            self.status.setText("Wait for the current frame operation before closing this window.")
            event.ignore()
            return
        if self._session is not None:
            self._session.close()
            self._session = None
        super().closeEvent(event)
