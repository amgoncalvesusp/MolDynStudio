"""One project-owned 3D dock using the existing frame inspector."""
from __future__ import annotations

from collections.abc import Mapping

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDockWidget, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from core.project_manager import ProjectFormatError
from tabs.base import PathSelector
from windows.trajectory_viewer import TrajectoryViewerWindow


class ProjectViewerDock(QDockWidget):
    def __init__(self, parent, settings, operation_blocked):
        super().__init__("3D structures and trajectories", parent)
        self.setObjectName("Project3DViewer")
        self.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.RightDockWidgetArea)
        body = QWidget()
        layout = QVBoxLayout(body)
        sources = QHBoxLayout()
        self.topology = PathSelector("Structure / topology (PDB, GRO, TPR)",
                                     "Structures (*.pdb *.gro *.tpr *.mol2);;All files (*)")
        self.trajectory = PathSelector("Trajectory (optional for a structure)",
                                       "Trajectories (*.xtc *.trr *.dcd);;All files (*)")
        self.load_button = QPushButton("Load 3D view")
        self.load_button.clicked.connect(self.load_sources)
        for widget in (self.topology, self.trajectory, self.load_button):
            sources.addWidget(widget)
        layout.addLayout(sources)
        self.viewer = TrajectoryViewerWindow("", "", body, settings=settings,
                                             embedded=True, autoload=False)
        self.viewer.operation_blocked = operation_blocked
        self.viewer.sources_changed.connect(self._sync_sources)
        layout.addWidget(self.viewer)
        self.setWidget(body)
        self.visibilityChanged.connect(self._visibility_changed)
        self.hide()

    def _sync_sources(self, topology: str, trajectory: str):
        self.topology.setText(topology)
        self.trajectory.setText(trajectory)

    def inspect_analysis_point(self, source: str, row_index: int):
        self.show()
        self.raise_()
        self.viewer.inspect_analysis_point(source, row_index)

    def is_busy(self):
        return self.viewer.is_busy()

    def _visibility_changed(self, visible):
        if not visible:
            self.viewer.pause()

    def show_sources(self, topology: str, trajectory: str = "", *, autoload=True) -> bool:
        if self.viewer.is_busy():
            self.viewer.status.setText("Pause playback and wait for the current frame operation before changing sources.")
            return False
        changed = (topology, trajectory) != (self.viewer.topology, self.viewer.trajectory)
        if changed:
            if not self.viewer.set_sources(topology, trajectory, autoload=autoload):
                return False
        elif self.viewer._session is None and autoload:
            self.viewer.load_trajectory()
        self.topology.setText(topology)
        self.trajectory.setText(trajectory)
        self.show()
        self.raise_()
        return True

    def offer_sources(self, topology: str, trajectory: str) -> bool:
        if not self.viewer.set_sources(topology, trajectory, autoload=False):
            return False
        self.topology.setText(topology)
        self.trajectory.setText(trajectory)
        self.viewer.status.setText("Production files are available. Load the 3D view to inspect their frames.")
        return True

    def load_sources(self):
        topology, trajectory = self.topology.text(), self.trajectory.text()
        if (topology, trajectory) == (self.viewer.topology, self.viewer.trajectory):
            self.viewer.load_trajectory()
        else:
            self.viewer.set_sources(topology, trajectory)

    def release_sources(self) -> bool:
        self.viewer.pause()
        if self.viewer.is_busy():
            self.viewer.status.setText("Wait for the frame operation before preparing or running this project.")
            return False
        state = self.viewer.get_view_state()
        if not self.viewer.set_view_state(state, autoload=False):
            return False
        self.viewer.status.setText("3D reader released for project changes. Reload the view after the operation finishes.")
        return True

    def clear_sources(self):
        if not self.viewer.clear_sources():
            return False
        self.topology.setText("")
        self.trajectory.setText("")
        self.hide()
        return True

    def get_state(self) -> dict:
        state = self.viewer.get_view_state()
        if (self.topology.text(), self.trajectory.text()) != (state["topology"], state["trajectory"]):
            state = {**state, "topology": self.topology.text(), "trajectory": self.trajectory.text(),
                     "frame_index": 0, "receptor_selection": "protein" if self.trajectory.text() else "all",
                     "ligand_selection": "resname LIG" if self.trajectory.text() else ""}
        return {**state, "visible": not self.isHidden()}

    @staticmethod
    def validate_state(value: object) -> dict:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ProjectFormatError("The saved 3D viewer state must be an object.")
        state = dict(value)
        for key in ("topology", "trajectory", "receptor_selection", "ligand_selection"):
            if key in state and not isinstance(state[key], str):
                raise ProjectFormatError(f"Invalid saved 3D viewer field: {key}.")
        frame = state.get("frame_index", 0)
        if type(frame) is not int or frame < 0:
            raise ProjectFormatError("The saved 3D frame index must be a nonnegative integer.")
        if "visible" in state and type(state["visible"]) is not bool:
            raise ProjectFormatError("The saved 3D visibility must be a boolean.")
        return state

    def set_state(self, state: dict):
        state = self.validate_state(state)
        if not state:
            return self.clear_sources()
        if not self.viewer.set_view_state(state, autoload=False):
            return False
        self.topology.setText(state.get("topology", ""))
        self.trajectory.setText(state.get("trajectory", ""))
        self.setVisible(state.get("visible", False))
        self.viewer.status.setText("Saved 3D inputs restored. Load the view to read their coordinates.")
        return True
