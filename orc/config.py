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
    rate_limit_patterns: list[str] = Field(default_factory=list)


class PoolConfig(BaseModel):
    window: Literal["5h", "weekly", "monthly"]
    budget_units: float
    flat_run_estimate: float


class LaneConfig(BaseModel):
    candidates: list[str] = Field(min_length=1)


class LadderConfig(BaseModel):
    effort_order: list[str] = Field(min_length=1)
    retry_count: int = Field(ge=0, le=2)

    def next_effort(self, current: str) -> str:
        """Return the next configured effort level, saturating at the maximum."""
        try:
            index = self.effort_order.index(current)
        except ValueError as error:
            raise ConfigError(f"effort {current!r} is not in ladder.effort_order") from error
        return self.effort_order[min(index + 1, len(self.effort_order) - 1)]


class VerifyConfig(BaseModel):
    timeout_s: int = Field(default=120, gt=0)
    tests: list[str] = Field(default_factory=list)
    lint: list[str] = Field(default_factory=list)
    build: list[str] = Field(default_factory=list)


class OrcConfig(BaseModel):
    adapters: dict[str, AdapterConfig]
    pools: dict[str, PoolConfig]
    lanes: dict[str, LaneConfig]
    ladder: LadderConfig
    verify: VerifyConfig = Field(default_factory=VerifyConfig)


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
