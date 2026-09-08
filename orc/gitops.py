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

_EXCLUDE_ENTRY = ".orc/"
_EXCLUDE_HEADER = "# added by orc: run artifacts, never committed"


def _git(target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=target, capture_output=True, text=True, check=False)


def git_dir(target: Path) -> Path:
    """Resolve the repository's git directory, correct for worktrees and submodules."""
    resolved = _git(target, "rev-parse", "--absolute-git-dir")
    if resolved.returncode != 0:
        raise SafetyError(f"target is not a git repository: {target}")
    return Path(resolved.stdout.strip())


def _exclude_path(target: Path) -> Path:
    """Resolve `info/exclude`, which git reads from the *common* git dir.

    `git_dir` (`--absolute-git-dir`) points at the per-worktree dir in a linked
    worktree, but `info/exclude` is only honored from the common dir; `--git-path`
    resolves to the right place in both layouts.
    """
    resolved = _git(target, "rev-parse", "--git-path", "info/exclude")
    if resolved.returncode != 0:
        raise SafetyError(f"target is not a git repository: {target}")
    path = Path(resolved.stdout.strip())
    return path if path.is_absolute() else (target / path)


def ensure_orc_excluded(target: Path) -> None:
    """Exclude `.orc/` locally so it neither dirties the tree nor can be staged.

    `.git/info/exclude` is the only permitted write inside `.git` (SPEC §10). It is
    local-only and never committed, so the user's repository is untouched.
    """
    exclude_path = _exclude_path(target)
    info_dir = exclude_path.parent
    if info_dir.is_symlink() or exclude_path.is_symlink():
        raise SafetyError(
            "refusing to write .git/info/exclude: it or its parent directory is a symlink"
        )
    if exclude_path.is_file() and exclude_path.stat().st_nlink > 1:
        raise SafetyError(
            "refusing to write .git/info/exclude: it has more than one hard link"
        )
    info_dir.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.is_file() else ""
    if any(line.strip() == _EXCLUDE_ENTRY for line in existing.splitlines()):
        return
    separator = "" if existing.endswith("\n") or not existing else "\n"
    exclude_path.write_text(
        f"{existing}{separator}{_EXCLUDE_HEADER}\n{_EXCLUDE_ENTRY}\n", encoding="utf-8"
    )


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
    ensure_orc_excluded(target)
    status = _git(target, "status", "--porcelain")
    if status.returncode != 0:
        raise SafetyError("could not inspect target git status")
    # Only untracked `.orc/` artifacts are ignored. A tracked, staged, deleted, or
    # renamed `.orc/` path stays in `dirty`: git's exclude never suppresses those, and
    # a rename like `R  .orc/x -> realfile` would otherwise conceal a change outside `.orc/`.
    dirty = [
        line
        for line in status.stdout.splitlines()
        if line.strip()
        and not (line.startswith("?? ") and line[3:].lstrip('"').startswith(".orc/"))
    ]
    if dirty:
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


def git_diff_stat(target: Path) -> str:
    """Return diff stat summary from git."""
    stat = _git(target, "diff", "--stat")
    return stat.stdout.strip() if stat.returncode == 0 else ""


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
