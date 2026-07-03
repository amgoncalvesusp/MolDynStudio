"""Post-MD analysis tab."""

from __future__ import annotations

from PyQt5.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import Qt

from analysis.cpptraj_builder import CpptrajBuilderWidget
from analysis.qc_pipeline import QCGromacsWorker, QCPipelineInputs, build_qc_tasks, default_qc_output_dir
from tabs.base import MolDynBasePage, PathSelector
from utils.tooltips import tooltip
from windows.mmpbsa_dialog import MMPBSADialog
from windows.trajectory_viewer import TrajectoryViewerWindow


class AnalysisTab(MolDynBasePage):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        title = QLabel("Analysis")
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._build_quick_tools(), "Analysis Tools")
        tabs.addTab(CpptrajBuilderWidget(), "CPPTRAJ Script Builder")
        outer.addWidget(tabs, 1)

    def _build_quick_tools(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        splitter.addWidget(left)

        files = QGroupBox("Input Files")
        form = QFormLayout(files)
        self.fields["topology"] = PathSelector("Topology (.tpr/.pdb/.gro)", "Topology (*.tpr *.pdb *.gro *.top);;All Files (*)")
        form.addRow("Topology", self.fields["topology"])
        self.fields["trajectory"] = PathSelector("Trajectory (.xtc/.trr/.nc)", "Trajectory (*.xtc *.trr *.nc *.dcd);;All Files (*)")
        form.addRow("Trajectory", self.fields["trajectory"])
        self.fields["energy"] = PathSelector("Energy (.edr/.xvg)", "Energy (*.edr *.xvg *.csv);;All Files (*)")
        form.addRow("Energy", self.fields["energy"])
        self.fields["qc_output"] = PathSelector("QC output folder", directory=True)
        form.addRow("QC output", self.fields["qc_output"])
        left_layout.addWidget(files)

        tools = QGroupBox("Analysis Selection")
        grid = QGridLayout(tools)
        for index, label in enumerate([
            "RMSD", "RMSF", "Radius of gyration", "SASA", "Hydrogen bonds", "PCA",
            "DSSP", "Contact map", "Ramachandran", "MM-GBSA", "MM-PBSA",
        ]):
            checkbox = QCheckBox(label)
            checkbox.setChecked(index < 4)
            if label in {"MM-GBSA", "MM-PBSA"}:
                checkbox.setToolTip(tooltip("mm_gbsa" if label == "MM-GBSA" else "mm_pbsa"))
            self.fields[f"analysis_{index}"] = checkbox
            grid.addWidget(checkbox, index // 2, index % 2)
        left_layout.addWidget(tools)

        buttons = QHBoxLayout()
        run = QPushButton("Run Selected")
        run.clicked.connect(self.run_selected_preview)
        buttons.addWidget(run)
        mmpbsa = QPushButton("Run MM-PBSA / MM-GBSA")
        mmpbsa.clicked.connect(self.open_mmpbsa_dialog)
        buttons.addWidget(mmpbsa)
        viewer = QPushButton("Open Trajectory Viewer")
        viewer.clicked.connect(self.open_trajectory_viewer)
        buttons.addWidget(viewer)
        qc = QPushButton("Generate QC Dashboard")
        qc.clicked.connect(self.run_qc_pipeline)
        buttons.addWidget(qc)
        preview_qc = QPushButton("Preview QC Commands")
        preview_qc.clicked.connect(self.preview_qc_commands)
        buttons.addWidget(preview_qc)
        buttons.addStretch(1)
        left_layout.addLayout(buttons)
        left_layout.addStretch(1)

        right = QGroupBox("Results")
        right_layout = QVBoxLayout(right)
        self.results = QTextEdit()
        self.results.setReadOnly(True)
        self.results.setPlainText("Analysis results and command previews will appear here.")
        right_layout.addWidget(self.results)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        return page

    def selected_analyses(self) -> list[str]:
        return [
            widget.text()
            for key, widget in self.fields.items()
            if key.startswith("analysis_") and isinstance(widget, QCheckBox) and widget.isChecked()
        ]

    def run_selected_preview(self) -> None:
        selected = self.selected_analyses()
        self.results.append("Selected analyses: " + (", ".join(selected) if selected else "none"))
        self.results.append("Execution backends are wired as lazy wrappers and will report missing packages at run time.")
        self.request_log.emit("Analysis preview prepared.")

    def open_mmpbsa_dialog(self) -> None:
        dialog = MMPBSADialog(self)
        dialog.run_requested.connect(lambda options: self.results.append(f"Prepared {options.method} with frames {options.startframe}-{options.endframe}."))
        dialog.exec_()

    def open_trajectory_viewer(self) -> None:
        self._viewer = TrajectoryViewerWindow(self.fields["topology"].text(), self.fields["trajectory"].text(), self)
        self._viewer.show()

    def qc_inputs(self) -> QCPipelineInputs:
        trajectory = self.fields["trajectory"].text()
        output_dir = self.fields["qc_output"].text() or default_qc_output_dir(trajectory)
        self.fields["qc_output"].setText(output_dir)
        return QCPipelineInputs(
            topology=self.fields["topology"].text(),
            trajectory=trajectory,
            energy=self.fields["energy"].text(),
            output_dir=output_dir,
        )

    def preview_qc_commands(self) -> None:
        try:
            tasks = build_qc_tasks(self.qc_inputs())
        except ValueError as exc:
            QMessageBox.warning(self, "Missing inputs", str(exc))
            return
        lines = ["QC commands to run:"]
        for task in tasks:
            lines.append(f"\n[{task.name}]\n{' '.join(task.command)}")
        self.results.append("\n".join(lines))
        self.request_log.emit("QC command preview prepared.")

    def run_qc_pipeline(self) -> None:
        try:
            inputs = self.qc_inputs()
            task_count = len(build_qc_tasks(inputs))
        except ValueError as exc:
            QMessageBox.warning(self, "Missing inputs", str(exc))
            return
        self.results.append(f"Starting QC pipeline with {task_count} commands. Output: {inputs.output_dir}")
        self._qc_worker = QCGromacsWorker(inputs, parent=self)
        self._qc_worker.log.connect(self._append_qc_log)
        self._qc_worker.finished.connect(self._on_qc_finished)
        self._qc_worker.start()

    def _append_qc_log(self, line: str) -> None:
        if line:
            self.results.append(line)
            self.request_log.emit(line)

    def _on_qc_finished(self, ok: bool, files: object, message: str) -> None:
        prefix = "[OK]" if ok else "[FAIL]"
        self.results.append(f"{prefix} {message}")
        self.request_log.emit(f"{prefix} {message}")
        if ok:
            self.request_qc_files.emit(tuple(files))

    def preview_text(self) -> str:
        return (
            "[Analysis Preview]\n"
            f"Topology: {self.fields['topology'].text() or 'Not set'}\n"
            f"Trajectory: {self.fields['trajectory'].text() or 'Not set'}\n"
            f"QC output: {self.fields['qc_output'].text() or 'Auto'}\n"
            f"Selected: {', '.join(self.selected_analyses()) if self.selected_analyses() else 'None'}"
        )
