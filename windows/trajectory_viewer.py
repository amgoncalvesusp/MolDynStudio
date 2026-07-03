"""NGL trajectory viewer embedded through Qt WebEngine when available."""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView
except Exception:  # pragma: no cover - QtWebEngine may be absent
    QWebEngineView = None  # type: ignore[assignment]


class TrajectoryViewerWindow(QMainWindow):
    def __init__(self, topology: str, trajectory: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Trajectory Viewer")
        self.resize(1000, 740)
        if QWebEngineView is None:
            fallback = QLabel(
                "QtWebEngine is not installed. Install qtwebengine/nglview to use the embedded trajectory viewer.\n\n"
                f"Topology: {topology}\nTrajectory: {trajectory}"
            )
            fallback.setWordWrap(True)
            self.setCentralWidget(fallback)
            return
        view = QWebEngineView()
        view.setHtml(self._html(topology, trajectory), baseUrl=None)
        self.setCentralWidget(view)

    def _html(self, topology: str, trajectory: str) -> str:
        top_name = Path(topology).name or "topology"
        traj_name = Path(trajectory).name or "trajectory"
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="https://unpkg.com/ngl@2.0.0-dev.39/dist/ngl.js"></script>
  <style>html, body, #viewport {{ width: 100%; height: 100%; margin: 0; }}</style>
</head>
<body>
  <div id="viewport"></div>
  <script>
    const stage = new NGL.Stage("viewport");
    stage.loadFile("{top_name}").then(function(component) {{
      component.addRepresentation("cartoon", {{ color: "chainid" }});
      component.autoView();
    }}).catch(function() {{
      document.body.innerHTML = "<pre>Load the generated HTML near your files or use nglview export.\\nTopology: {top_name}\\nTrajectory: {traj_name}</pre>";
    }});
  </script>
</body>
</html>"""

