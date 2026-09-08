"""Narrow git safety helpers; all git subprocesses are contained here."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


class SafetyError(RuntimeError):
    """Raised when a task or repository violates an M1 safety rail."""


_DESTRUCTIVE_PATTERNS = (r"\brm\s+-rf\b", r"\bdrop\s+table\b", r"\bschema\s+migration\b")

_EXCLUDE_ENTRY = ".orc/"
_EXCLUDE_HEADER = "# added by orc: run artifacts, never committed"

# Dependency and cache trees an agent may legitimately churn. Test-file enumeration
# prunes these by name so `git ls-files` can run ignore-unaware — an agent-authored
# `.gitignore` must not be able to blind the tamper guard. Kept in sync with
# `SafetyConfig.skip_dirs` (a test asserts equality); `orc.toml` overrides the config.
_DEFAULT_SKIP_DIRS: tuple[str, ...] = (
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
)


def _git(
    target: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    run_env = {**os.environ, **env} if env else None
    return subprocess.run(
        ["git", *args], cwd=target, capture_output=True, text=True, check=False, env=run_env
    )


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


def base_commit(target: Path) -> str:
    """Record HEAD at branch creation so the whole run's work can be diffed later."""
    resolved = _git(target, "rev-parse", "HEAD")
    if resolved.returncode != 0:
        raise SafetyError("could not resolve HEAD; the target repository has no commits")
    return resolved.stdout.strip()


@contextmanager
def _throwaway_index(target: Path) -> Iterator[dict[str, str]]:
    """Yield a git env whose index is a scratch copy of the real one.

    ``git_diff`` marks new files intent-to-add so they show in the diff. Doing that on
    the real index leaves ``A `` entries behind, which the next run's cleanliness check
    reads as a dirty tree — the very failure Task 4 removed. A throwaway index keeps the
    diff a pure read.
    """
    resolved = _git(target, "rev-parse", "--git-path", "index")
    real_index = Path(resolved.stdout.strip())
    if not real_index.is_absolute():
        real_index = target / real_index
    handle, scratch = tempfile.mkstemp(prefix="orc-diff-index-")
    os.close(handle)
    try:
        if real_index.is_file():
            shutil.copyfile(real_index, scratch)
        else:
            os.unlink(scratch)  # let git create it fresh
        yield {"GIT_INDEX_FILE": scratch}
    finally:
        Path(scratch).unlink(missing_ok=True)


def _intent_to_add_run_work(target: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Intent-to-add everything a run diff should show: tracked changes, untracked files,
    and untracked files the target already *gitignores* (an agent's fix can land in a
    path like ``instance/config.py``). Never ``.orc/`` artifacts, and never a dependency
    or build tree from the skip-dir denylist — those churn and would swamp the diff.
    """
    added = _git(target, "add", "--intent-to-add", "--all", env=env)
    if added.returncode != 0:
        return added
    ignored = _git(
        target, "ls-files", "-z", "--others", "--ignored", "--exclude-standard", env=env
    )
    if ignored.returncode != 0:
        return added
    skip = set(_DEFAULT_SKIP_DIRS)
    paths = [
        p
        for p in ignored.stdout.split("\0")
        if p
        and not p.startswith(_EXCLUDE_ENTRY)
        and not skip.intersection(PurePosixPath(p).parts)
    ]
    if paths:
        return _git(target, "add", "--intent-to-add", "--force", "--", *paths, env=env)
    return added


def git_diff(target: Path, base: str) -> str:
    """Diff the working tree against the run's base commit.

    `git diff` alone shows only unstaged tracked changes, so work an agent committed or
    staged is invisible. Intent-to-add (in a throwaway index) makes new files visible
    without staging content; `.orc/` and dependency trees are excluded, so they never
    appear.
    """
    with _throwaway_index(target) as env:
        staged = _intent_to_add_run_work(target, env)
        if staged.returncode != 0:
            raise SafetyError(staged.stderr.strip() or "could not stage new files for diff")
        diff = _git(target, "diff", "--no-ext-diff", base, env=env)
    if diff.returncode != 0:
        raise SafetyError(diff.stderr.strip() or "could not collect git diff")
    return diff.stdout


def git_diff_stat(target: Path, base: str) -> str:
    """Return the diff stat against the run's base commit."""
    with _throwaway_index(target) as env:
        if _intent_to_add_run_work(target, env).returncode != 0:
            return ""
        stat = _git(target, "diff", "--stat", base, env=env)
    return stat.stdout.strip() if stat.returncode == 0 else ""


def test_file_snapshot(
    target: Path, patterns: list[str], skip_dirs: list[str] | None = None
) -> dict[str, str]:
    """Hash test files so a test-altering patch can be refused.

    Enumerated with ``git ls-files`` (tracked *and* untracked), never a filesystem walk.
    It runs **ignore-unaware** on purpose: an agent controls ``.gitignore``, so relying
    on git's ignore lens lets a self-ignoring ``tests/.gitignore`` (``*``) hide a new
    ``conftest.py``. Dependency and cache trees are pruned by a fixed name denylist
    instead, so a legitimate ``npm install`` still does not bloat the snapshot or trip
    the abort. ``.gitignore`` files are themselves in the watched pattern set.
    """
    skip = set(skip_dirs) if skip_dirs is not None else set(_DEFAULT_SKIP_DIRS)
    listed = _git(target, "ls-files", "-z", "--cached", "--others")
    if listed.returncode != 0:
        raise SafetyError("could not list tracked files")
    snapshot: dict[str, str] = {}
    for relative in listed.stdout.split("\0"):
        if not relative:
            continue
        if skip.intersection(PurePosixPath(relative).parts):
            continue
        if not any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
            continue
        path = target / relative
        if path.is_file():
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


# The name starts with `test_`; tell pytest it is not a test when a test module imports it.
test_file_snapshot.__test__ = False  # type: ignore[attr-defined]


def assert_tests_unchanged(
    target: Path, before: dict[str, str], patterns: list[str], skip_dirs: list[str] | None = None
) -> None:
    """Fail closed if an agent changed, added, or removed any watched test file."""
    after = test_file_snapshot(target, patterns, skip_dirs)
    if after != before:
        raise SafetyError("agent changed test files; refusing to accept a test-altering patch")
