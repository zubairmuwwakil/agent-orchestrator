"""Vendor-neutral contract for coding-agent subprocess adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AgentMode = Literal["agent", "consult"]
AgentStatus = Literal["ok", "fail", "rate_limited", "timeout", "error", "unavailable"]


@dataclass(slots=True)
class AgentRequest:
    prompt: str
    mode: AgentMode
    model: str
    effort: str
    cwd: Path
    timeout_s: int
    context_files: list[Path] | None = None
    # The base contract needs an explicit destination to guarantee run artifacts
    # remain below the target repository's .orc/runs directory.
    transcript_path: Path | None = None


@dataclass(slots=True)
class AgentResult:
    status: AgentStatus
    text: str
    usage: dict[str, object] | None
    transcript_path: Path
    tool_call_count: int | None
    ran_commands: list[str]


class AgentAdapter(ABC):
    """A coding-agent CLI wrapper."""

    name: str

    @abstractmethod
    def available(self) -> bool:
        """Return whether this CLI is installed and authenticated."""

    @abstractmethod
    def run(self, req: AgentRequest) -> AgentResult:
        """Run a single agent or consultant request."""
