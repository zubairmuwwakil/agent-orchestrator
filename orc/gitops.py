"""Narrow git safety helpers; all git subprocesses are contained here."""

from __future__ import annotations

import hashlib
import re
import secrets
import subprocess
from pathlib import Path


class SafetyError(RuntimeError):
    """Raised when a task or repository violates an M1 safety rail."""


_DESTRUCTIVE_PATTERNS = (r"\brm\s+-rf\b", r"\bdrop\s+table\b", r"\bschema\s+migration\b")


def _git(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=target, capture_output=True, text=True, check=False)


def ensure_safe_target(target: Path, task: str, allow_destructive: bool) -> None:
    """Require a clean git repository and reject destructive task wording."""
    if not allow_destructive and any(
        re.search(pattern, task, re.IGNORECASE) for pattern in _DESTRUCTIVE_PATTERNS
    ):
        raise SafetyError(
            "task matches the destructive-operation denylist; pass --allow-destructive to continue"
        )
    inside = _git(target, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise SafetyError(f"target is not a git repository: {target}")
    status = _git(target, "status", "--porcelain")
    if status.returncode != 0:
        raise SafetyError("could not inspect target git status")
    if status.stdout.strip():
        raise SafetyError("target git repository is not clean; commit or stash changes first")


def create_branch(target: Path, task: str, task_id: str) -> str:
    """Create and switch to an isolated run branch without touching the base branch."""
    slug = re.sub(r"[^a-z0-9]+", "-", task.casefold()).strip("-")[:40] or "task"
    branch = f"orc/{slug}-{task_id}"
    created = _git(target, "switch", "-c", branch)
    if created.returncode != 0:
        raise SafetyError(created.stderr.strip() or f"could not create branch {branch}")
    return branch


def new_task_id() -> str:
    """Return a short collision-resistant run identifier."""
    return secrets.token_hex(3)


def git_diff(target: Path) -> str:
    """Return the working-tree diff from git, never agent output."""
    diff = _git(target, "diff", "--no-ext-diff")
    if diff.returncode != 0:
        raise SafetyError(diff.stderr.strip() or "could not collect git diff")
    return diff.stdout


def test_file_snapshot(target: Path) -> dict[Path, str]:
    """Hash test files so an M1 run cannot accept a test-altering patch."""
    snapshot: dict[Path, str] = {}
    for path in target.rglob("*.py"):
        relative = path.relative_to(target)
        name = path.name
        if "tests" in relative.parts or name.startswith("test_") or name.endswith("_test.py"):
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def assert_tests_unchanged(target: Path, before: dict[Path, str]) -> None:
    """Fail closed if an agent changed, added, or removed any Python test file."""
    after = test_file_snapshot(target)
    if after != before:
        raise SafetyError("agent changed test files; refusing to accept a test-altering patch")
