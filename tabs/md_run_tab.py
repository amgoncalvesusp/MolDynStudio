"""Real molecular-dynamics orchestration and monitoring controls."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QTextCursor
from PyQt5.QtWidgets import (
    QApplication,
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

from core.artifact_validation import ValidationResult
from core.gromacs_capabilities import (
    GromacsCapabilities,
    probe_gromacs,
)
from core.gromacs_log_parser import GromacsLogParser, MDProgress
from core.matplotlib_qt import configure_pyqt5_backend, is_pyqt5_widget_type
from core.md_pipeline import PipelineStage, build_pipeline
from core.md_pipeline_runner import ArtifactValidators, MDPipelineRunner
from core.run_manifest import (
    RunManifest,
    StageName,
    StageStatus,
    load_manifest,
    manifest_path,
)
from core.settings import SettingsStore
from tabs.base import MolDynBasePage

try:
    configure_pyqt5_backend()
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure

    if not is_pyqt5_widget_type(FigureCanvas):
        raise ImportError("Matplotlib selected a non-PyQt5 Qt binding")
except Exception:  # pragma: no cover - the text fallback remains fully usable
    FigureCanvas = None  # type: ignore[assignment]
    Figure = None  # type: ignore[assignment]


RunnerFactory = Callable[..., object]
CapabilityProbe = Callable[[str, str], GromacsCapabilities]

_DYNAMICS_STAGES = (
    StageName.MINIMIZATION,
    StageName.NVT,
    StageName.NPT,
    StageName.PRODUCTION,
)

_STAGE_LABELS = {
    StageName.MINIMIZATION: "Minimization",
    StageName.NVT: "NVT Equilibration",
    StageName.NPT: "NPT Equilibration",
    StageName.PRODUCTION: "Production MD",
}

_STATUS_LABELS = {
    StageStatus.NOT_READY: "Not ready",
    StageStatus.READY: "Ready",
    StageStatus.RUNNING: "Running",
    StageStatus.COMPLETED: "Completed",
    StageStatus.FAILED: "Failed",
    StageStatus.STOPPED: "Stopped",
    StageStatus.INTERRUPTED: "Interrupted",
}

_RESUME_OPTIONS = (
    ("Minimization", StageName.MINIMIZATION, False),
    ("NVT Equilibration", StageName.NVT, False),
    ("NPT Equilibration", StageName.NPT, False),
    ("Production MD", StageName.PRODUCTION, False),
    ("Resume Production checkpoint", StageName.PRODUCTION, True),
)


@dataclass(frozen=True)
class StageWidgets:
    progress: QProgressBar
    status: QLabel


@dataclass(frozen=True)
class PlotPanel:
    canvas: object | None
    axes: object | None
    empty_label: QLabel
    title: str
    ylabel: str


class MDRunTab(MolDynBasePage):
    """Thin Qt view over :class:`MDPipelineRunner` and a run manifest."""

    def __init__(
        self,
        settings: SettingsStore | None = None,
        parent=None,
        *,
        runner_factory: RunnerFactory | None = None,
        capability_probe: CapabilityProbe = probe_gromacs,
        artifact_validators: ArtifactValidators | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings or SettingsStore()
        self._runner_factory = runner_factory
        self._capability_probe = capability_probe
        self._artifact_validators = artifact_validators or ArtifactValidators()
        self._project_dir: Path | None = None
        self._manifest_file: Path | None = None
        self._manifest: RunManifest | None = None
        self._runner: object | None = None
        self._active = False
        self._active_stage: StageName | None = None
        self._preparation_valid = False
        self._plot_panels: dict[str, PlotPanel] = {}
        self._plot_values: dict[str, tuple[tuple[float, ...], tuple[float, ...]]] = {}
        self._log_parser = GromacsLogParser()
        self._last_parsed_step: int | None = None

        outer = QVBoxLayout(self)
        title = QLabel("MD Run")
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        self.project_context = QLabel("No prepared MD project loaded.")
        self.project_context.setWordWrap(True)
        self.project_context.setObjectName("ProjectContext")
        outer.addWidget(self.project_context)

        control = QGroupBox("Pipeline Control")
        control_layout = QVBoxLayout(control)
        row = QHBoxLayout()

        self.run_button = QPushButton("Run All")
        self.run_button.clicked.connect(self.start_run)
        row.addWidget(self.run_button)

        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_run)
        row.addWidget(self.stop_button)

        self.resume_selector = QComboBox()
        for label, _stage, _checkpoint in _RESUME_OPTIONS:
            self.resume_selector.addItem(label)
        self.resume_selector.setAccessibleName("Resume from stage")
        self.resume_selector.currentIndexChanged.connect(self._update_resume_button)
        self.resume = self.resume_selector
        row.addWidget(self.resume_selector)

        self.resume_button = QPushButton("Resume")
        self.resume_button.clicked.connect(self.resume_run)
        row.addWidget(self.resume_button)
        row.addStretch(1)
        control_layout.addLayout(row)

        self.stage_widgets: dict[StageName, StageWidgets] = {}
        self.steps: list[tuple[str, QProgressBar, QLabel]] = []
        for stage in _DYNAMICS_STAGES:
            step_row = QHBoxLayout()
            label = _STAGE_LABELS[stage]
            step_row.addWidget(QLabel(label), 1)
            progress = QProgressBar()
            progress.setRange(0, 100)
            progress.setValue(0)
            progress.setAccessibleName(f"{label} progress")
            step_row.addWidget(progress, 3)
            status = QLabel("Not ready")
            status.setAccessibleName(f"{label} status")
            step_row.addWidget(status, 1)
            control_layout.addLayout(step_row)
            self.stage_widgets[stage] = StageWidgets(progress, status)
            self.steps.append((label, progress, status))
        outer.addWidget(control)

        monitor = QGroupBox("Real-Time Monitoring")
        monitor_layout = QGridLayout(monitor)
        self._add_plot(monitor_layout, "temperature", "Temperature", "K", 0, 0)
        self._add_plot(monitor_layout, "pressure", "Pressure", "bar", 0, 1)
        self._add_plot(monitor_layout, "energy", "Energy", "kJ/mol", 0, 2)
        outer.addWidget(monitor, 1)

        terminal = QGroupBox("Terminal Output")
        terminal_layout = QVBoxLayout(terminal)
        self.log = QTextEdit()
        self.log.setAcceptRichText(False)
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(160)
        terminal_layout.addWidget(self.log)
        buttons = QHBoxLayout()
        self.clear_log_button = QPushButton("Clear Log")
        self.clear_log_button.clicked.connect(self.log.clear)
        buttons.addWidget(self.clear_log_button)
        copy_button = QPushButton("Copy Last Line")
        copy_button.clicked.connect(self.copy_last_line)
        buttons.addWidget(copy_button)
        buttons.addStretch(1)
        terminal_layout.addLayout(buttons)
        outer.addWidget(terminal)

        self._set_controls_active(False)
        self._disable_resume_options()

    @property
    def is_active(self) -> bool:
        """Whether this tab currently owns a running MD pipeline."""

        return self._active

    def load_project(self, project_dir: str) -> None:
        """Use the exact project directory selected by MD Setup."""

        value = str(project_dir or "").strip()
        selected = Path(value).expanduser().resolve() if value else None
        if self._active:
            if selected != self._project_dir:
                self._append_ui_message(
                    "The project cannot be changed while an MD pipeline is active."
                )
            return
        self._project_dir = selected
        self._manifest_file = (
            manifest_path(self._project_dir) if self._project_dir is not None else None
        )
        self.refresh_from_manifest()

    def refresh_from_manifest(self) -> None:
        """Reload persisted stage state and recalculate safe restart points."""

        if self._active:
            return
        self._manifest = None
        self._preparation_valid = False
        if self._project_dir is None or self._manifest_file is None:
            self.project_context.setText("No prepared MD project loaded.")
            self._show_all_stages(StageStatus.NOT_READY)
            self._disable_resume_options()
            self._set_controls_active(False)
            return

        try:
            manifest = load_manifest(self._manifest_file)
            recorded_project = Path(manifest.project_dir).expanduser().resolve()
            if recorded_project != self._project_dir:
                raise ValueError(
                    "Manifest project directory does not match the loaded MD project."
                )
        except Exception as exc:
            self.project_context.setText(
                f"Project not ready: {self._project_dir}"
            )
            self._show_all_stages(StageStatus.NOT_READY)
            self._disable_resume_options()
            self._set_controls_active(False)
            self._append_ui_message(f"Could not load run manifest: {exc}")
            return

        self._manifest = manifest
        self.project_context.setText(f"Prepared project: {self._project_dir}")
        for stage in _DYNAMICS_STAGES:
            try:
                status = StageStatus(manifest.stages[stage.value].status)
            except (KeyError, ValueError):
                status = StageStatus.NOT_READY
            self._set_stage_status(stage, status)

        self._preparation_valid = self._validate_preparation(manifest)
        self._refresh_resume_options()
        self._set_controls_active(False)

    def start_run(self) -> None:
        """Validate the prepared project and start all dynamics stages."""

        if self._active:
            return
        self.refresh_from_manifest()
        if self._manifest is None or not self._preparation_valid:
            self._append_ui_message(
                "Run All is unavailable until MD preparation artifacts validate."
            )
            return
        stages = build_pipeline(self._project_dir, stages=_DYNAMICS_STAGES)
        self._start_runner(stages, resume=False, label="Minimization")

    def stop_run(self) -> None:
        """Request cancellation of the active external GROMACS process."""

        runner = self._runner
        if not self._active or runner is None:
            return
        self._append_ui_message("Stopping the active MD pipeline...")
        try:
            runner.stop()
        except Exception as exc:
            self._append_ui_message(f"Could not stop the MD pipeline: {exc}")

    def resume_run(self) -> None:
        """Start at a validated stage or continue a production checkpoint."""

        if self._active:
            return
        self.refresh_from_manifest()
        index = self.resume_selector.currentIndex()
        item = self.resume_selector.model().item(index)
        if self._manifest is None or item is None or not item.isEnabled():
            self._append_ui_message(
                "The selected resume point does not have valid scientific inputs."
            )
            return
        _label, stage, checkpoint = _RESUME_OPTIONS[index]
        stages = build_pipeline(
            self._project_dir,
            stages=_DYNAMICS_STAGES,
            resume_from=stage,
        )
        self._start_runner(
            stages,
            resume=checkpoint,
            label=_RESUME_OPTIONS[index][0],
        )

    def append_live_data(self, metric: str, step: float, value: float) -> None:
        """Append a value only after a real output parser has produced it."""

        panel = self._plot_panels.get(str(metric).casefold())
        if panel is None or panel.canvas is None or panel.axes is None:
            return
        try:
            parsed_step = float(step)
            parsed_value = float(value)
        except (TypeError, ValueError):
            return
        x_values, y_values = self._plot_values[metric.casefold()]
        x_values = (*x_values, parsed_step)[-500:]
        y_values = (*y_values, parsed_value)[-500:]
        self._plot_values = {
            **self._plot_values,
            metric.casefold(): (x_values, y_values),
        }
        panel.empty_label.hide()
        panel.axes.clear()
        panel.axes.plot(x_values, y_values, color="#E87722", linewidth=2)
        panel.axes.set_title(panel.title)
        panel.axes.set_xlabel("Step")
        panel.axes.set_ylabel(panel.ylabel)
        panel.axes.grid(True, alpha=0.25)
        panel.canvas.draw_idle()

    def _start_runner(
        self,
        stages: Sequence[PipelineStage],
        *,
        resume: bool,
        label: str,
    ) -> None:
        assert self._manifest is not None
        assert self._manifest_file is not None
        self._set_controls_active(True)
        try:
            executable = self._manifest.gromacs_binary
            capabilities = self._capability_probe(
                executable,
                self._manifest.conda_environment,
            )
            if not capabilities.executable_ok:
                detail = capabilities.version_text.strip() or "probe failed"
                raise RuntimeError(f"GROMACS is not callable: {detail}")
            runner = self._make_runner(
                self._manifest_file,
                stages,
                capabilities,
                resume=resume,
            )
            runner.stage_changed.connect(self._on_stage_changed)
            runner.log_line.connect(self._on_gromacs_line)
            runner.finished_with_status.connect(self._on_runner_finished)
        except Exception as exc:
            self._append_ui_message(f"Could not start the MD pipeline: {exc}")
            self._set_controls_active(False)
            return

        self._runner = runner
        self._reset_plots()
        self._append_ui_message(f"Starting {label}...")
        try:
            runner.start()
        except Exception as exc:
            self._runner = None
            self._set_controls_active(False)
            self._append_ui_message(f"Could not start the MD pipeline: {exc}")

    def _make_runner(
        self,
        manifest_file: Path,
        stages: Sequence[PipelineStage],
        capabilities: GromacsCapabilities,
        *,
        resume: bool,
    ) -> object:
        if self._runner_factory is not None:
            return self._runner_factory(
                manifest_file,
                stages,
                capabilities,
                resume=resume,
            )
        return MDPipelineRunner(
            manifest_file,
            stages,
            capabilities,
            validators=self._artifact_validators,
            resume=resume,
            parent=self,
        )

    def _validate_preparation(self, manifest: RunManifest) -> bool:
        preparation = manifest.stages.get(StageName.PREPARATION.value)
        if preparation is None or preparation.status != StageStatus.COMPLETED.value:
            return False
        root = self._project_dir
        assert root is not None
        expected_paths = {
            filename: (root / filename).resolve()
            for filename in ("system.gro", "topol.top", "em.mdp", "nvt.mdp", "npt.mdp", "md.mdp")
        }
        if any(
            not preparation.outputs.get(filename)
            or Path(preparation.outputs[filename]).expanduser().resolve() != expected
            for filename, expected in expected_paths.items()
        ):
            return False
        checks = (
            (self._artifact_validators.validate_gro, expected_paths["system.gro"]),
            (self._artifact_validators.validate_file, expected_paths["topol.top"]),
            (self._artifact_validators.validate_file, expected_paths["em.mdp"]),
            (self._artifact_validators.validate_file, expected_paths["nvt.mdp"]),
            (self._artifact_validators.validate_file, expected_paths["npt.mdp"]),
            (self._artifact_validators.validate_file, expected_paths["md.mdp"]),
        )
        return all(self._is_valid(validator, path) for validator, path in checks)

    def _refresh_resume_options(self) -> None:
        root = self._project_dir
        manifest = self._manifest
        if root is None or manifest is None:
            self._disable_resume_options()
            return
        stage_status = {
            stage: StageStatus(manifest.stages[stage.value].status)
            for stage in _DYNAMICS_STAGES
        }
        valid = (
            self._preparation_valid,
            self._preparation_valid
            and stage_status[StageName.MINIMIZATION] == StageStatus.COMPLETED
            and self._recorded_outputs_are_valid(
                StageName.MINIMIZATION,
                (("em.gro", self._artifact_validators.validate_gro),),
            ),
            self._preparation_valid
            and stage_status[StageName.NVT] == StageStatus.COMPLETED
            and self._recorded_outputs_are_valid(
                StageName.NVT,
                (
                    ("nvt.gro", self._artifact_validators.validate_gro),
                    ("nvt.cpt", self._artifact_validators.validate_checkpoint),
                ),
            ),
            self._preparation_valid
            and stage_status[StageName.NPT] == StageStatus.COMPLETED
            and self._recorded_outputs_are_valid(
                StageName.NPT,
                (
                    ("npt.gro", self._artifact_validators.validate_gro),
                    ("npt.cpt", self._artifact_validators.validate_checkpoint),
                ),
            ),
            self._preparation_valid
            and stage_status[StageName.PRODUCTION]
            in {StageStatus.INTERRUPTED, StageStatus.STOPPED, StageStatus.FAILED}
            and self._is_valid(self._artifact_validators.validate_tpr, root / "md.tpr")
            and self._is_valid(
                self._artifact_validators.validate_checkpoint, root / "md.cpt"
            ),
        )
        for index, enabled in enumerate(valid):
            self.resume_selector.model().item(index).setEnabled(enabled)
        self._update_resume_button()

    def _recorded_outputs_are_valid(
        self,
        stage: StageName,
        requirements: Sequence[
            tuple[str, Callable[[str | Path], ValidationResult]]
        ],
    ) -> bool:
        if self._manifest is None or self._project_dir is None:
            return False
        outputs = self._manifest.stages[stage.value].outputs
        for filename, validator in requirements:
            recorded = outputs.get(filename)
            expected = (self._project_dir / filename).resolve()
            if not recorded or Path(recorded).expanduser().resolve() != expected:
                return False
            if not self._is_valid(validator, expected):
                return False
        return True

    def _disable_resume_options(self) -> None:
        for index in range(self.resume_selector.count()):
            self.resume_selector.model().item(index).setEnabled(False)
        self.resume_selector.setEnabled(False)
        self.resume_button.setEnabled(False)

    @staticmethod
    def _is_valid(validator, path: Path) -> bool:
        try:
            result = validator(path)
        except Exception:
            return False
        return isinstance(result, ValidationResult) and result.ok

    def _set_controls_active(self, active: bool) -> None:
        self._active = active
        self.run_button.setEnabled(not active and self._preparation_valid)
        self.stop_button.setEnabled(active)
        self.clear_log_button.setEnabled(not active)
        self.resume_selector.setEnabled(not active and self._preparation_valid)
        self._update_resume_button()

    def _update_resume_button(self) -> None:
        index = self.resume_selector.currentIndex()
        item = self.resume_selector.model().item(index) if index >= 0 else None
        self.resume_button.setEnabled(
            not self._active
            and self.resume_selector.isEnabled()
            and item is not None
            and item.isEnabled()
        )

    def _show_all_stages(self, status: StageStatus) -> None:
        for stage in _DYNAMICS_STAGES:
            self._set_stage_status(stage, status)

    def _set_stage_status(self, stage: StageName, status: StageStatus) -> None:
        widgets = self.stage_widgets[stage]
        widgets.progress.setRange(0, 0 if status == StageStatus.RUNNING else 100)
        if status != StageStatus.RUNNING:
            widgets.progress.setValue(100 if status == StageStatus.COMPLETED else 0)
        widgets.status.setText(_STATUS_LABELS[status])

    def _on_stage_changed(self, stage_name: str, status_name: str, message: str) -> None:
        try:
            stage = StageName(stage_name)
            status = StageStatus(status_name)
        except ValueError:
            self._append_ui_message(
                f"Runner reported an unknown stage state: {stage_name}/{status_name}."
            )
            return
        if stage in self.stage_widgets:
            self._set_stage_status(stage, status)
        if status == StageStatus.RUNNING:
            self._active_stage = stage
            mdp_names = {
                StageName.MINIMIZATION: "em.mdp",
                StageName.NVT: "nvt.mdp",
                StageName.NPT: "npt.mdp",
                StageName.PRODUCTION: "md.mdp",
            }
            mdp_path = (
                self._project_dir / mdp_names[stage]
                if self._project_dir is not None
                else None
            )
            self._log_parser = GromacsLogParser(mdp_path)
            self._last_parsed_step = None
        elif self._active_stage == stage and status in {
            StageStatus.COMPLETED,
            StageStatus.FAILED,
            StageStatus.STOPPED,
            StageStatus.INTERRUPTED,
        }:
            self._active_stage = None
        if message:
            self._append_ui_message(message)

    def _on_gromacs_line(self, line: str) -> None:
        text = str(line)
        self._append_log_line("[GROMACS] ", text)
        update = self._log_parser.feed_line(text)
        if update is not None:
            self._apply_parsed_progress(update)

    def _apply_parsed_progress(self, update: MDProgress) -> None:
        if update.step is not None:
            self._last_parsed_step = update.step
        if update.fraction is not None and self._active_stage is not None:
            progress = self.stage_widgets[self._active_stage].progress
            progress.setRange(0, 100)
            progress.setValue(round(update.fraction * 100))

        plot_step = update.step if update.step is not None else self._last_parsed_step
        if plot_step is None:
            return
        values = {
            "temperature": update.temperature_k,
            "pressure": update.pressure_bar,
            "energy": update.potential_kj_mol,
        }
        for metric, value in values.items():
            if value is not None:
                self.append_live_data(metric, plot_step, value)

    def _on_runner_finished(self, _success: bool, message: str) -> None:
        self._runner = None
        self._active = False
        self._active_stage = None
        self._append_ui_message(message)
        if self._project_dir is None or self._manifest_file is None:
            self._preparation_valid = False
            self._refresh_resume_options()
            self._set_controls_active(False)
            return
        self.refresh_from_manifest()

    def _append_ui_message(self, message: str) -> None:
        self._append_log_line("[MolDynStudio] ", str(message))
        self.request_log.emit(str(message))

    def _append_log_line(self, prefix: str, line: str) -> None:
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.End)
        if cursor.position() > 0 and not self.log.toPlainText().endswith("\n"):
            cursor.insertText("\n")
        cursor.insertText(f"{prefix}{line}\n")
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def _add_plot(
        self,
        layout: QGridLayout,
        key: str,
        title: str,
        ylabel: str,
        row: int,
        column: int,
    ) -> None:
        container = QWidget()
        panel_layout = QVBoxLayout(container)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        empty_label = QLabel("No live data yet")
        empty_label.setAlignment(Qt.AlignHCenter)
        empty_label.setAccessibleName(f"{title} plot empty state")

        canvas = None
        axes = None
        if FigureCanvas is not None and Figure is not None:
            figure = Figure(figsize=(3.8, 2.4))
            canvas = FigureCanvas(figure)
            axes = figure.add_subplot(111)
            axes.set_title(title)
            axes.set_xlabel("Step")
            axes.set_ylabel(ylabel)
            axes.grid(True, alpha=0.25)
            figure.tight_layout()
            panel_layout.addWidget(canvas)
        else:
            fallback = QLabel(f"{title} plot requires matplotlib.")
            fallback.setAlignment(Qt.AlignHCenter)
            panel_layout.addWidget(fallback)
        panel_layout.addWidget(empty_label)
        layout.addWidget(container, row, column)
        self._plot_panels[key] = PlotPanel(
            canvas,
            axes,
            empty_label,
            title,
            ylabel,
        )
        self._plot_values = {**self._plot_values, key: ((), ())}

    def _reset_plots(self) -> None:
        self._plot_values = {key: ((), ()) for key in self._plot_panels}
        for panel in self._plot_panels.values():
            panel.empty_label.show()
            if panel.axes is None or panel.canvas is None:
                continue
            panel.axes.clear()
            panel.axes.set_title(panel.title)
            panel.axes.set_xlabel("Step")
            panel.axes.set_ylabel(panel.ylabel)
            panel.axes.grid(True, alpha=0.25)
            panel.canvas.draw_idle()

    def copy_last_line(self) -> None:
        lines = self.log.toPlainText().rstrip().splitlines()
        if not lines:
            return
        QApplication.clipboard().setText(lines[-1])
        self.request_log.emit(lines[-1])

    def preview_text(self) -> str:
        project = str(self._project_dir) if self._project_dir else "No project loaded"
        return (
            "[MD Run Preview]\n"
            f"Project: {project}\n"
            "Pipeline: Minimization -> NVT -> NPT -> Production MD"
        )
