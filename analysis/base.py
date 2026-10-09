"""Common contracts for trajectory analysis backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AnalysisResult:
    name: str
    data: Any
    message: str = ""
    columns: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class AnalysisBase(ABC):
    name = "Analysis"
    tool = "python"
    tooltip = ""

    @abstractmethod
    def run(self, *args, **kwargs) -> AnalysisResult:
        """Run the analysis and return structured data."""

