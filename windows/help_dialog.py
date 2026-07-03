"""Built-in MolDynStudio help manual."""

from __future__ import annotations

from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QTextEdit, QVBoxLayout


HELP_HTML = """
<h1>MolDynStudio Manual</h1>
<p><b>Inventor:</b> Adriano Marques Gonçalves (UNIARA)</p>
<h2>Workflow</h2>
<ol>
  <li>Create or open an .mds project.</li>
  <li>Use <b>MD Setup</b> to select protein/ligand inputs and generate .mdp files.</li>
  <li>Use <b>MD Run</b> to monitor minimization, equilibration, and production stages.</li>
  <li>Use <b>Analysis</b> for RMSD, RMSF, SASA, PCA, MM-PBSA/MM-GBSA, and CPPTRAJ scripts.</li>
</ol>
<h2>Runtime</h2>
<p>On Windows, GROMACS is expected to run through WSL2 when available. Commands are prefixed with
<code>conda run -n moldynstudio</code>.</p>
<h2>Missing Tools</h2>
<p>If a scientific package is missing, the relevant button remains available for configuration but execution
will report the missing dependency. Install from <code>environment.yml</code>.</p>
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MolDynStudio Documentation")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setHtml(HELP_HTML)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
