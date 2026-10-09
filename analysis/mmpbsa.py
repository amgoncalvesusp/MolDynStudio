"""gmx_MMPBSA input generation and subprocess wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import tempfile
import json
import time
from dataclasses import asdict

from analysis.base import AnalysisBase, AnalysisResult
from core import wsl_bridge


@dataclass(frozen=True)
class MMPBSAOptions:
    method: str = "GBSA"
    igb: int = 5
    saltcon: float = 0.150
    startframe: int = 1
    endframe: int = 9999
    interval: int = 1
    decomposition: bool = False
    entropy: str = "None"


def build_input_file(options: MMPBSAOptions) -> str:
    if options.method.upper() not in {"PBSA", "GBSA"}:
        raise ValueError("Method must be PBSA or GBSA.")
    if options.startframe < 1 or options.endframe < options.startframe or options.interval < 1:
        raise ValueError("Frames are 1-based; end must follow start and interval must be positive.")
    if not math.isfinite(options.saltcon) or options.saltcon < 0:
        raise ValueError("Salt concentration must be finite and nonnegative (M).")
    if options.igb not in {2, 5, 8}:
        raise ValueError("Supported GB models are 2, 5, and 8.")
    if options.entropy.upper() not in {"NONE", "NMODE"}:
        raise ValueError("Only None and NMODE entropy options are supported.")
    section = "pb" if options.method.upper() == "PBSA" else "gb"
    solvent = f"istrng={options.saltcon:.3f}" if section == "pb" else f"igb={options.igb}, saltcon={options.saltcon:.3f}"
    decomp = "\n&decomp\n  idecomp=2,\n/\n" if options.decomposition else ""
    entropy = (
        f"\n&nmode\n  nmstartframe={options.startframe}, nmendframe={options.endframe}, "
        f"nminterval={options.interval},\n/\n"
        if options.entropy.upper() == "NMODE" else ""
    )
    return f"""&general
  startframe={options.startframe}, endframe={options.endframe}, interval={options.interval},
  verbose=2,
/
&{section}
  {solvent},
/
{decomp}{entropy}"""


class MMPBSAAnalysis(AnalysisBase):
    name = "MM-PBSA / MM-GBSA"
    tool = "gmx_MMPBSA"
    tooltip = "Binding free energy through gmx_MMPBSA."

    def build_command(
        self,
        topology: str,
        trajectory: str,
        receptor_group: str,
        ligand_group: str,
        workdir: str,
        conda_env: str = "moldynstudio",
    ) -> list[str]:
        del conda_env, workdir
        return [
            "gmx_MMPBSA",
            "-i",
            "MMPBSA.in",
            "-cs",
            wsl_bridge.win_to_wsl(topology),
            "-ct",
            wsl_bridge.win_to_wsl(trajectory),
            "-ci",
            "index.ndx",
            "-cg",
            receptor_group,
            ligand_group,
            "-cp",
            "topol.top",
            "-o",
            "FINAL_RESULTS.dat",
        ]

    def run(
        self,
        topology: str,
        trajectory: str,
        receptor_group: str,
        ligand_group: str,
        options: MMPBSAOptions,
        workdir: str,
        conda_env: str = "moldynstudio",
    ) -> AnalysisResult:
        if not workdir.strip():
            raise ValueError("Choose the folder containing topol.top and index.ndx.")
        target = Path(workdir).expanduser().resolve()
        for source in (topology, trajectory, str(target / "index.ndx"), str(target / "topol.top")):
            if not Path(source).is_file():
                raise ValueError(f"Required MM-PBSA input does not exist: {source}")
        if not receptor_group.isdecimal() or not ligand_group.isdecimal() or receptor_group == ligand_group:
            raise ValueError("Receptor and ligand must be distinct numeric index-group IDs.")
        input_text = build_input_file(options)
        output_dir = Path(tempfile.mkdtemp(prefix="mmpbsa_", dir=target))
        input_file = output_dir / "MMPBSA.in"
        input_file.write_text(input_text, encoding="utf-8")
        command = self.build_command(str(Path(topology).resolve()), str(Path(trajectory).resolve()), receptor_group, ligand_group, str(target))
        command[command.index("-i") + 1] = wsl_bridge.win_to_wsl(str(input_file))
        command[command.index("-o") + 1] = wsl_bridge.win_to_wsl(str(output_dir / "FINAL_RESULTS.dat"))
        command.extend(["-prefix", wsl_bridge.win_to_wsl(str(output_dir / "_MMPBSA_")),
                        "-eo", wsl_bridge.win_to_wsl(str(output_dir / "FINAL_RESULTS.csv")),
                        "-do", wsl_bridge.win_to_wsl(str(output_dir / "DECOMP_RESULTS.dat")),
                        "-deo", wsl_bridge.win_to_wsl(str(output_dir / "DECOMP_RESULTS.csv")), "-nogui"])
        from analysis.execution import fingerprint
        started = time.monotonic()
        provenance = {"options": asdict(options), "command": command, "conda_environment": conda_env,
                      "inputs": [fingerprint(p) for p in (topology, trajectory, target / "topol.top", target / "index.ndx")],
                      "limitations": "Topology include dependencies remain in the working folder and are not recursively hashed. End-state estimates are not experimental binding affinities.",
                      "status": "running"}
        manifest = output_dir / "mmpbsa_manifest.json"
        manifest.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        try:
            completed = wsl_bridge.run(command, cwd=str(target), env_name=conda_env, check=False)
        except Exception as exc:
            manifest.write_text(json.dumps({**provenance, "status": "failed", "error": str(exc),
                                           "elapsed_seconds": time.monotonic() - started}, indent=2), encoding="utf-8")
            raise
        output = output_dir / "FINAL_RESULTS.dat"
        valid = completed.returncode == 0 and output.is_file() and output.stat().st_size > 0
        provenance = {**provenance, "status": "complete" if valid else "failed",
                      "returncode": completed.returncode, "elapsed_seconds": time.monotonic() - started,
                      "output": fingerprint(output) if valid else None}
        manifest.write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        (output_dir / "execution.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        data = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "output": str(output),
            "manifest": str(manifest),
            "success": valid,
        }
        return AnalysisResult(self.name, data, "gmx_MMPBSA finished." if valid else "gmx_MMPBSA failed or produced no result file.")
