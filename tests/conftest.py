"""Shared fixtures. Live tests need a real, authenticated CLI on PATH."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    """Return recorded CLI output captured from a real vendor CLI."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def require_cli(command: str) -> None:
    """Skip a live test when its CLI is absent, rather than failing."""
    if shutil.which(command) is None:
        pytest.skip(f"{command} is not installed; live test skipped")
