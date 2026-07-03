"""Common contracts for trajectory analysis backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AnalysisResult:
    name: str
    data: Any
    message: str = ""


class AnalysisBase(ABC):
    name = "Analysis"
    tool = "python"
    tooltip = ""

    @abstractmethod
    def run(self, *args, **kwargs) -> AnalysisResult:
        """Run the analysis and return structured data."""

