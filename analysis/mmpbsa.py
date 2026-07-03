"""gmx_MMPBSA input generation and subprocess wrapper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
    section = "pb" if options.method.upper() == "PBSA" else "gb"
    decomp = "\n&decomp\n  idecomp=2,\n/\n" if options.decomposition else ""
    entropy = "\n&nmode\n  nmstartframe=1, nmendframe=100, nminterval=10,\n/\n" if options.entropy.upper() == "NMODE" else ""
    return f"""&general
  startframe={options.startframe}, endframe={options.endframe}, interval={options.interval},
  verbose=2,
/
&{section}
  igb={options.igb}, saltcon={options.saltcon:.3f},
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
    ) -> AnalysisResult:
        target = Path(workdir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "MMPBSA.in").write_text(build_input_file(options), encoding="utf-8")
        command = self.build_command(topology, trajectory, receptor_group, ligand_group, str(target))
        completed = wsl_bridge.run(command, cwd=str(target), check=False)
        data = {
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "output": str(target / "FINAL_RESULTS.dat"),
        }
        return AnalysisResult(self.name, data, "gmx_MMPBSA finished." if completed.returncode == 0 else "gmx_MMPBSA failed.")
