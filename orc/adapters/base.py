"""Vendor-neutral contract for coding-agent subprocess adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

AgentMode = Literal["agent", "consult"]
AgentStatus = Literal["ok", "fail", "rate_limited", "timeout", "error", "unavailable"]
WindowKind = Literal["5h", "weekly", "monthly"]
QuotaSource = Literal["stream", "session-file", "command"]

_MINUTES_PER_DAY = 60 * 24


def window_kind_from_minutes(minutes: float) -> WindowKind:
    """Classify a vendor-reported window length. Codex reports 300 and 10080."""
    if minutes <= _MINUTES_PER_DAY:
        return "5h"
    if minutes <= _MINUTES_PER_DAY * 14:
        return "weekly"
    return "monthly"


@dataclass(slots=True)
class QuotaWindow:
    """One rate-limit window, normalized to fraction *used* regardless of vendor polarity."""

    kind: WindowKind
    used_fraction: float
    resets_at: datetime


@dataclass(slots=True)
class QuotaObservation:
    """A reading of one pool's utilization. A pool may have several simultaneous windows."""

    windows: list[QuotaWindow]
    observed_at: datetime
    source: QuotaSource


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
    # Utilization observed during this run, when the CLI reports it. Defaulted so
    # existing positional constructions keep working.
    quota: QuotaObservation | None = None


class AgentAdapter(ABC):
    """A coding-agent CLI wrapper."""

    name: str

    @abstractmethod
    def available(self) -> bool:
        """Return whether this CLI is installed and authenticated."""

    @abstractmethod
    def run(self, req: AgentRequest) -> AgentResult:
        """Run a single agent or consultant request."""

    def quota_probe(self) -> QuotaObservation | None:
        """Return current utilization without spending a run, when the CLI allows it.

        Adapters without a telemetry mechanism return None and the ledger estimates.
        """
        return None
