"""Native, disk-backed trajectory frame inspection and DockLens handoff."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.matplotlib_qt import configure_pyqt5_backend
from core.settings import SettingsStore
from core.trajectory_frames import (
    FrameExport,
    FrameSnapshot,
    TrajectorySession,
    launch_docklens,
)
from tabs.base import PathSelector

configure_pyqt5_backend()
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from mpl_toolkits.mplot3d import art3d  # type: ignore[import-untyped]  # noqa: E402

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


class TrajectoryViewerWindow(QWidget):
    sources_changed = pyqtSignal(str, str)

    def __init__(self, topology: str, trajectory: str = "", parent=None, *, settings=None,
                 embedded=False, autoload=True):
        super().__init__(parent, Qt.Widget if embedded else Qt.Window)  # type: ignore[attr-defined]
        self.setWindowTitle("Structure and trajectory 3D")
        self.resize(1120, 780)
        self.topology, self.trajectory = topology, trajectory
        self.operation_blocked = lambda: False
        self.settings = settings or (getattr(parent.window(), "settings", None) if parent else None)
        self.settings = self.settings or SettingsStore()
        self._session = None
        self._worker = None
        self._export = None
        self._operation = ""
        self._snapshot = None
        self._display_selection = None
        self._requested_frame = 0
        self._reset_camera = True
        self._play_timer = QTimer(self)
        self._play_timer.setInterval(200)
        self._play_timer.timeout.connect(self._advance_frame)
        self._build_ui()
        self._set_busy(False)
        if autoload:
            QTimer.singleShot(0, self.load_trajectory)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        title = QLabel("Inspect a frame, then review its interactions")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        self.source_label.setTextFormat(Qt.PlainText)
        self._show_sources()
        layout.addWidget(self.source_label)
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
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.setAccessibleName("Trajectory frame")
        form.addRow(self.frame_slider)
        navigation = QHBoxLayout()
        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        self.play_button = QPushButton("Play")
        self.previous_button.clicked.connect(lambda: self.frame_spin.setValue(self.frame_spin.value() - 1))
        self.next_button.clicked.connect(lambda: self.frame_spin.setValue(self.frame_spin.value() + 1))
        self.play_button.clicked.connect(self.toggle_playback)
        for button in (self.previous_button, self.play_button, self.next_button):
            navigation.addWidget(button)
        form.addRow(navigation)
        self.receptor_edit = QLineEdit("protein" if self.trajectory else "all")
        self.ligand_edit = QLineEdit("resname LIG" if self.trajectory else "")
        for edit in (self.receptor_edit, self.ligand_edit):
            edit.setToolTip("MDAnalysis selection: all; protein; chainID A; resid 10:20; resname LIG. "
                            "Combine with 'and'. Apply with Show frame.")
        form.addRow("Receptor selection", self.receptor_edit)
        form.addRow("Ligand selection", self.ligand_edit)
        row = QHBoxLayout()
        self.reload_button = QPushButton("Reload trajectory")
        self.reload_button.clicked.connect(self.load_trajectory)
        self.show_button = QPushButton("Show frame")
        self.show_button.clicked.connect(self.show_frame)
        self.reset_button = QPushButton("Reset view")
        self.reset_button.clicked.connect(self.reset_view)
        row.addWidget(self.reload_button)
        row.addWidget(self.show_button)
        row.addWidget(self.reset_button)
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
        self.status = QLabel("Choose a structure or topology and trajectory, then load the viewer.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.frame_spin.valueChanged.connect(self._frame_changed)
        self.frame_slider.valueChanged.connect(self.frame_spin.setValue)
        self.receptor_edit.textChanged.connect(self._selection_changed)
        self.ligand_edit.textChanged.connect(self._selection_changed)

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
        return self._worker is not None or self._play_timer.isActive()

    def _set_busy(self, busy):
        loaded = self._session is not None
        playing = self._play_timer.isActive()
        for widget in (self.frame_spin, self.receptor_edit, self.ligand_edit,
                       self.reload_button, self.profile, self.pbc_check, self.output, self.frame_slider):
            widget.setEnabled(not busy and not playing)
        self.show_button.setEnabled(not busy and loaded and not playing)
        self.export_button.setEnabled(not busy and not playing and loaded and bool(self.trajectory)
                                      and self.pbc_check.isChecked() and self._display_matches())
        self.launch_button.setEnabled(not busy and not playing and self._export is not None)
        self.previous_button.setEnabled(not busy and not playing and loaded and self.frame_spin.value() > 0)
        self.next_button.setEnabled(not busy and not playing and loaded
                                    and self.frame_spin.value() < self.frame_spin.maximum())
        self.play_button.setEnabled(playing or (not busy and loaded and self.frame_spin.maximum() > 0))
        self.play_button.setText("Pause" if playing else "Play")

    def _invalidate_export(self, *_):
        self._export = None
        self.pbc_check.setChecked(False)
        self.launch_button.setEnabled(False)

    def _start(self, name, operation):
        if self._worker is not None:
            return
        self._operation = name
        self._set_busy(True)
        self.status.setText(f"{name.capitalize()} in progress…")
        self._worker = _FrameWorker(operation, self)
        self._worker.succeeded.connect(self._succeeded, Qt.QueuedConnection)
        self._worker.failed.connect(self._failed, Qt.QueuedConnection)
        self._worker.finished.connect(self._finished, Qt.QueuedConnection)
        try:
            self._worker.start()
        except RuntimeError as exc:
            self._failed(str(exc))
            self._finished()

    def load_trajectory(self):
        if self.is_busy():
            return
        if not self._can_operate():
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
                return session, session.read_frame(self._requested_frame, receptor, ligand)
            except Exception:
                session.close()
                raise

        self._start("Loading trajectory", load)

    def show_frame(self):
        if self._session is None or self._worker is not None or not self._can_operate():
            return
        index, receptor, ligand = self.frame_spin.value(), self.receptor_edit.text(), self.ligand_edit.text()
        self._start("Reading frame", lambda: self._session.read_frame(index, receptor, ligand))

    def inspect_analysis_point(self, source: str, row_index: int):
        self.pause()
        if self.is_busy() or not self._can_operate():
            return
        self._invalidate_export()
        old_session = self._session
        receptor, ligand = self.receptor_edit.text(), self.ligand_edit.text()
        current_sources = (self.topology, self.trajectory)

        def inspect():
            from analysis.result_links import load_result_rows

            _, _, manifest, _ = load_result_rows(source)
            sources = tuple(str(Path(manifest["inputs"][key]["path"]).resolve())
                            for key in ("topology", "trajectory"))
            same_sources = all(value and Path(value).resolve() == Path(expected)
                               for value, expected in zip(current_sources, sources))
            selections = (receptor, ligand) if same_sources else ("protein", "resname LIG")
            session = old_session if same_sources and old_session is not None else TrajectorySession.open(*sources)
            try:
                snapshot = session.read_analysis_point(source, row_index, *selections)
                if old_session is not None and session is not old_session:
                    old_session.close()
                return session, snapshot
            except Exception:
                if session is not old_session:
                    session.close()
                raise

        self._start("Inspecting analysis point", inspect)

    def export_frame(self):
        if self._session is None or not self.pbc_check.isChecked() or not self._can_operate():
            return
        if not self._display_matches():
            self.status.setText("Apply the current frame and selections with Show frame before exporting.")
            return
        if not self.trajectory:
            self.status.setText("DockLens frame export requires an explicit trajectory.")
            return
        args = {
            "output_dir": self.output.text(), "frame_index": self.frame_spin.value(),
            "receptor_selection": self.receptor_edit.text(), "ligand_selection": self.ligand_edit.text(),
            "pbc_reviewed": self.pbc_check.isChecked(), "profile": self.profile.currentData(),
        }
        self._start("Exporting frame and hashing sources", lambda: self._session.export_frame(**args))

    @pyqtSlot(object)
    def _succeeded(self, result):
        if isinstance(result, tuple):
            self._session, snapshot = result
            sources = (str(self._session.topology), str(self._session.trajectory))
            if self._operation == "Inspecting analysis point":
                changed = not all(value and Path(value).resolve() == Path(expected)
                                  for value, expected in zip((self.topology, self.trajectory), sources))
                self.topology, self.trajectory = sources
                if changed:
                    self._reset_camera = True
                    self.receptor_edit.setText("protein")
                    self.ligand_edit.setText("resname LIG")
                    self.output.setText(str(Path(self.trajectory).parent / "frame_exports"))
                self._show_sources()
                self.sources_changed.emit(*sources)
            self._show_snapshot(snapshot)
        elif isinstance(result, FrameSnapshot):
            self._show_snapshot(result)
        elif isinstance(result, FrameExport):
            self._export = result
            self.status.setText(f"Exported frame {result.frame_index}: {result.directory}\nSource hashes and atom mapping: provenance.json")

    @pyqtSlot(str)
    def _failed(self, message):
        self.pause()
        self._invalidate_export()
        self._display_selection = None
        self._requested_frame = self._snapshot.frame_index if self._snapshot is not None else 0
        for control in (self.frame_spin, self.frame_slider):
            control.blockSignals(True)
            control.setValue(self._requested_frame)
            control.blockSignals(False)
        self.status.setText(f"{self._operation} failed: {message}")

    @pyqtSlot()
    def _finished(self):
        worker = self._worker
        self._worker = None
        self._set_busy(False)
        if worker is not None:
            worker.deleteLater()
        if self._play_timer.isActive() and self.frame_spin.value() == self.frame_spin.maximum():
            self.pause()

    def _show_snapshot(self, snapshot, *, preserve_selection=False):
        self._snapshot = snapshot
        if not preserve_selection:
            self._display_selection = (self.receptor_edit.text(), self.ligand_edit.text())
        self._requested_frame = snapshot.frame_index
        self.frame_spin.blockSignals(True)
        self.frame_spin.setMaximum(snapshot.n_frames - 1)
        self.frame_spin.setValue(snapshot.frame_index)
        self.frame_spin.blockSignals(False)
        self.frame_slider.blockSignals(True)
        self.frame_slider.setMaximum(snapshot.n_frames - 1)
        self.frame_slider.setValue(snapshot.frame_index)
        self.frame_slider.blockSignals(False)
        time = f"{snapshot.time_ps:g} ps" if snapshot.time_ps is not None else "unavailable"
        mode = "Structure (single frame)" if not self.trajectory else f"Frame {snapshot.frame_index} of {snapshot.n_frames} · Time: {time}"
        self.frame_label.setText(
            f"{mode}\n"
            f"Receptor: {snapshot.receptor_count} atoms · Ligand: {snapshot.ligand_count} atoms\n"
            "Time comes from the trajectory reader; verify timestep and origin."
        )
        camera = (self.axes.elev, self.axes.azim)
        limits = (self.axes.get_xlim3d(), self.axes.get_ylim3d(), self.axes.get_zlim3d())
        self.axes.clear()
        self.axes.computed_zorder = not bool(snapshot.highlight_coordinates)
        for coordinates, bonds, color, label in (
            (snapshot.receptor_coordinates, snapshot.receptor_bonds, "#247BA0", "Receptor"),
            (snapshot.ligand_coordinates, snapshot.ligand_bonds, "#D55E00", "Ligand"),
        ):
            if coordinates:
                x, y, z = zip(*coordinates)
                self.axes.scatter(x, y, z, c=color, s=8, label=label, alpha=0.8)
                if bonds:
                    lines = [(coordinates[a], coordinates[b]) for a, b in bonds]
                    self.axes.add_collection3d(art3d.Line3DCollection(lines, colors=color, linewidths=1))
        if snapshot.highlight_coordinates:
            x, y, z = zip(*snapshot.highlight_coordinates)
            self.axes.scatter(x, y, z, c="#CC79A7", s=50, label="Analysis residue", alpha=0.95,
                              edgecolors="white", linewidths=0.7, depthshade=False, zorder=10)
        self.axes.set(xlabel="X (Å)", ylabel="Y (Å)", zlabel="Z (Å)", title=f"Frame {snapshot.frame_index}")
        if snapshot.receptor_coordinates or snapshot.ligand_coordinates:
            self.axes.legend(loc="upper right")
            if self._reset_camera:
                bounds = [self.axes.get_xlim3d(), self.axes.get_ylim3d(), self.axes.get_zlim3d()]
                radius = max(max(high - low, 1.0) for low, high in bounds) / 2
                centers = [(low + high) / 2 for low, high in bounds]
                limits = tuple((center - radius, center + radius) for center in centers)
            self.axes.set_xlim3d(*limits[0])
            self.axes.set_ylim3d(*limits[1])
            self.axes.set_zlim3d(*limits[2])
            self.axes.set_box_aspect((1, 1, 1))
        self.axes.view_init(elev=camera[0], azim=camera[1])
        self._reset_camera = False
        self.canvas.draw_idle()
        self.status.setText(
            "Coordinates loaded. Rotate with the mouse; only topology-provided bonds are drawn. "
            + ("Display reduced to about 6,000 atoms; export keeps every selected atom." if snapshot.display_reduced else "Every selected atom is shown.")
            + (f"\n{snapshot.highlight_label}" if snapshot.highlight_label else "")
        )

    def _show_sources(self):
        self.source_label.setText(f"Topology: {self.topology}\nTrajectory: {self.trajectory}" if self.trajectory
                                  else f"Structure: {self.topology or 'not selected'}")

    def _can_operate(self):
        if self.operation_blocked():
            self.pause()
            self.status.setText("Wait for the current project operation before using the 3D viewer.")
            return False
        return True

    def set_sources(self, topology, trajectory="", *, autoload=True):
        if self.is_busy():
            self.status.setText("Wait for the current frame operation before changing the 3D sources.")
            return False
        if not isinstance(topology, str) or not isinstance(trajectory, str):
            raise ValueError("Viewer source paths must be strings.")  # noqa: TRY004 - saved project data validation.
        if self._session is not None:
            self._session.close()
        self._session = None
        self._snapshot = None
        self._display_selection = None
        self._requested_frame = 0
        self._reset_camera = True
        self.topology, self.trajectory = topology, trajectory
        self._invalidate_export()
        self.frame_spin.setMaximum(0)
        self.frame_slider.setMaximum(0)
        self.receptor_edit.setText("protein" if trajectory else "all")
        self.ligand_edit.setText("resname LIG" if trajectory else "")
        self.output.setText(str(Path(trajectory or topology).expanduser().parent / "frame_exports"))
        self._show_sources()
        self.axes.clear()
        self.axes.set_axis_off()
        self.canvas.draw_idle()
        self.frame_label.setText("No frame loaded.")
        self.status.setText("Choose a structure or topology and trajectory, then load the viewer.")
        self._set_busy(False)
        if autoload:
            self.load_trajectory()
        return True

    def clear_sources(self):
        return self.set_sources("", "", autoload=False)

    def get_view_state(self):
        return {"topology": self.topology, "trajectory": self.trajectory,
                "receptor_selection": self.receptor_edit.text(), "ligand_selection": self.ligand_edit.text(),
                "frame_index": self.frame_spin.value() if self._session is not None else self._requested_frame}

    def set_view_state(self, state: Mapping, *, autoload=False):
        if not isinstance(state, Mapping):
            raise ValueError("3D viewer state must be an object.")  # noqa: TRY004 - saved project data validation.
        values = {key: state.get(key, "") for key in ("topology", "trajectory", "receptor_selection", "ligand_selection")}
        frame = state.get("frame_index", 0)
        if any(not isinstance(value, str) for value in values.values()) or type(frame) is not int or frame < 0:
            raise ValueError("3D viewer paths/selections must be strings and frame index a nonnegative integer.")
        if not self.set_sources(values["topology"], values["trajectory"], autoload=False):
            return False
        self.receptor_edit.setText(values["receptor_selection"] if "receptor_selection" in state
                                   else ("protein" if self.trajectory else "all"))
        self.ligand_edit.setText(values["ligand_selection"] if "ligand_selection" in state
                                else ("resname LIG" if self.trajectory else ""))
        self._requested_frame = frame
        if autoload:
            self.load_trajectory()
        return True

    def _selection_changed(self):
        self.pause()
        self._invalidate_export()

    def _display_matches(self):
        return (self._snapshot is not None and self._snapshot.frame_index == self.frame_spin.value()
                and self._display_selection == (self.receptor_edit.text(), self.ligand_edit.text()))

    def _frame_changed(self, index):
        self._requested_frame = index
        self._invalidate_export()
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(index)
        self.frame_slider.blockSignals(False)
        if self._session is not None:
            self.show_frame()

    def toggle_playback(self):
        if self._play_timer.isActive():
            self.pause()
        elif (self._worker is None and self._session is not None
              and self.frame_spin.maximum() > 0 and self._can_operate()):
            if self.frame_spin.value() == self.frame_spin.maximum():
                self.frame_spin.setValue(0)
            self._play_timer.start()
            self._set_busy(self._worker is not None)

    def _advance_frame(self):
        if self._worker is not None or not self._can_operate():
            return
        if self.frame_spin.value() == self.frame_spin.maximum():
            self.pause()
        else:
            self.frame_spin.setValue(self.frame_spin.value() + 1)

    def pause(self):
        self._play_timer.stop()
        self._set_busy(self._worker is not None)

    def reset_view(self):
        if self._worker is not None:
            return
        self._reset_camera = True
        if self._snapshot is not None:
            self._show_snapshot(self._snapshot, preserve_selection=True)

    def hideEvent(self, event):
        self.pause()
        super().hideEvent(event)

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
        self.pause()
        if self.is_busy():
            self.status.setText("Wait for the current frame operation before closing this window.")
            event.ignore()
            return
        if self._session is not None:
            self._session.close()
            self._session = None
        super().closeEvent(event)
