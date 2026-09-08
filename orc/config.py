"""Configuration loading and validation for orc.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ConfigError(ValueError):
    """Raised when no valid orchestrator configuration can be loaded."""


class AdapterConfig(BaseModel):
    command: str
    pool: str | None = None
    rate_limit_patterns: list[str] = Field(default_factory=list)


class PoolConfig(BaseModel):
    # A pool has several simultaneous windows: codex reports a 5h and a weekly one.
    windows: list[Literal["5h", "weekly", "monthly"]] = Field(min_length=1)
    budget_units: float  # fallback only, used when no telemetry is available
    flat_run_estimate: float  # fallback only
    reserve_fraction: float = Field(default=0.15, ge=0.0, lt=1.0)
    quota_group: str | None = None  # vendor-side group name, when one CLI serves two pools


class LaneConfig(BaseModel):
    candidates: list[str] = Field(min_length=1)
    # Optional per-lane override of [agents].timeout_s (e.g. a slower `volume` lane).
    timeout_s: int | None = Field(default=None, gt=0)


class AgentsConfig(BaseModel):
    # A coding run is not a test run; [verify].timeout_s sizes the latter.
    timeout_s: int = Field(default=900, gt=0)


class LadderConfig(BaseModel):
    order: list[str] = Field(default_factory=lambda: ["standard", "quality"])
    effort_order: list[str] = Field(min_length=1)
    max_total_attempts: int = Field(default=4, ge=1)

    def next_effort(self, current: str) -> str:
        """Return the next configured effort level, saturating at the maximum."""
        try:
            index = self.effort_order.index(current)
        except ValueError as error:
            raise ConfigError(f"effort {current!r} is not in ladder.effort_order") from error
        return self.effort_order[min(index + 1, len(self.effort_order) - 1)]

    def is_max_effort(self, current: str) -> bool:
        """Check if the given effort level is already at the maximum configured."""
        try:
            return self.effort_order.index(current) >= len(self.effort_order) - 1
        except ValueError as error:
            raise ConfigError(f"effort {current!r} is not in ladder.effort_order") from error


class TriageConfig(BaseModel):
    min_tool_calls: int = Field(default=2, ge=0)


class VerifyConfig(BaseModel):
    timeout_s: int = Field(default=120, gt=0)
    tests: list[str] = Field(default_factory=list)
    lint: list[str] = Field(default_factory=list)
    build: list[str] = Field(default_factory=list)


class SafetyConfig(BaseModel):
    # Match against paths from `git ls-files`. `tests/**` matches any path under `tests/`.
    test_path_patterns: list[str] = Field(
        default_factory=lambda: [
            "tests/**",
            "test/**",
            "spec/**",
            "conftest.py",
            "**/conftest.py",
            ".gitignore",
            "**/.gitignore",
            "test_*.py",
            "*_test.py",
            "*_test.go",
            "*.test.ts",
            "*.test.js",
            "*.spec.ts",
            "src/test/**",
        ]
    )
    # Directory names never scanned for test files: dependency and build trees an agent
    # may legitimately churn. A fixed denylist, not attacker-controllable ignore rules.
    # Mirrors orc.gitops._DEFAULT_SKIP_DIRS (a test asserts the two stay in sync).
    skip_dirs: list[str] = Field(
        default_factory=lambda: [
            ".git",
            ".orc",
            "node_modules",
            ".venv",
            "venv",
            ".tox",
            ".nox",
            "site-packages",
            "__pycache__",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            ".eggs",
            "dist",
            "build",
            "target",
            "vendor",
        ]
    )


class OrcConfig(BaseModel):
    adapters: dict[str, AdapterConfig]
    pools: dict[str, PoolConfig]
    lanes: dict[str, LaneConfig]
    ladder: LadderConfig
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    triage: TriageConfig = Field(default_factory=TriageConfig)
    verify: VerifyConfig = Field(default_factory=VerifyConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)


def find_config(target: Path) -> Path:
    """Find the nearest orc.toml at or above a target repository."""
    for directory in (target, *target.parents):
        candidate = directory / "orc.toml"
        if candidate.is_file():
            return candidate
    raise ConfigError(f"no orc.toml found at or above {target}")


def load_config(path: Path) -> OrcConfig:
    """Load and validate an orc.toml file."""
    try:
        with path.open("rb") as config_file:
            raw = tomllib.load(config_file)
        return OrcConfig.model_validate(raw)
    except (OSError, tomllib.TOMLDecodeError, ValueError) as error:
        raise ConfigError(f"invalid configuration {path}: {error}") from error
