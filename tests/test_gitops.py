import os
import subprocess
from pathlib import Path

import pytest

from orc.gitops import (
    SafetyError,
    base_commit,
    ensure_orc_excluded,
    ensure_safe_target,
    git_diff,
    git_dir,
    test_file_snapshot,
)


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path


def test_second_run_starts_even_though_orc_wrote_artifacts(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    (tmp_path / ".orc" / "runs").mkdir(parents=True)
    (tmp_path / ".orc" / "runs" / "x.txt").write_text("artifact\n", encoding="utf-8")

    ensure_safe_target(tmp_path, "do a thing", allow_destructive=False)  # must not raise


def test_exclude_prevents_git_add_all_from_staging_orc(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    (tmp_path / ".orc").mkdir()
    (tmp_path / ".orc" / "ledger.json").write_text("{}\n", encoding="utf-8")

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert ".orc" not in staged


def test_exclude_is_idempotent_and_writes_nothing_else_in_git(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    ensure_orc_excluded(tmp_path)

    exclude = git_dir(tmp_path) / "info" / "exclude"
    assert exclude.read_text(encoding="utf-8").count(".orc/") == 1


def test_real_dirt_still_blocks_the_run(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")

    with pytest.raises(SafetyError, match="not clean"):
        ensure_safe_target(tmp_path, "do a thing", allow_destructive=False)


def test_staged_rename_out_of_orc_is_not_hidden(tmp_path: Path) -> None:
    """A `R  .orc/x -> realfile` porcelain line must not be filtered as an orc artifact:
    the destination is outside `.orc/`, so treating it as clean conceals real dirt."""
    _repo(tmp_path)
    (tmp_path / ".orc").mkdir()
    (tmp_path / ".orc" / "tracked").write_text("data\n", encoding="utf-8")
    subprocess.run(["git", "add", "-f", ".orc/tracked"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "track an orc file"], cwd=tmp_path, check=True)
    subprocess.run(["git", "mv", ".orc/tracked", "realfile"], cwd=tmp_path, check=True)

    with pytest.raises(SafetyError, match="not clean"):
        ensure_safe_target(tmp_path, "do a thing", allow_destructive=False)


def test_exclude_works_from_a_linked_worktree(tmp_path: Path) -> None:
    """`info/exclude` is read from the common git dir; `--absolute-git-dir` points at the
    per-worktree one. Writing to the wrong place leaves `.orc/` stageable in a worktree."""
    main = tmp_path / "main"
    main.mkdir()
    _repo(main)
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", str(worktree), "-b", "wt-branch"],
        cwd=main,
        check=True,
    )

    ensure_orc_excluded(worktree)
    (worktree / ".orc").mkdir()
    (worktree / ".orc" / "ledger.json").write_text("{}\n", encoding="utf-8")

    subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert ".orc" not in staged
    common_exclude = git_dir(main) / "info" / "exclude"
    assert common_exclude.read_text(encoding="utf-8").count(".orc/") == 1


def test_refuses_to_write_through_a_symlinked_exclude(tmp_path: Path) -> None:
    """The one permitted `.git` write must not follow a symlink out of the git dir."""
    _repo(tmp_path)
    outside = tmp_path.parent / "outside-exclude.txt"
    outside.write_text("", encoding="utf-8")
    exclude = git_dir(tmp_path) / "info" / "exclude"
    exclude.unlink()
    exclude.symlink_to(outside)

    with pytest.raises(SafetyError, match="symlink"):
        ensure_orc_excluded(tmp_path)
    assert outside.read_text(encoding="utf-8") == ""


def test_refuses_to_write_through_a_hardlinked_exclude(tmp_path: Path) -> None:
    """A hard link shares the inode with an external file; writing would rewrite it."""
    _repo(tmp_path)
    outside = tmp_path.parent / "outside-hardlink.txt"
    outside.write_text("keep me\n", encoding="utf-8")
    exclude = git_dir(tmp_path) / "info" / "exclude"
    exclude.unlink()
    os.link(outside, exclude)

    with pytest.raises(SafetyError, match="hard link"):
        ensure_orc_excluded(tmp_path)
    assert outside.read_text(encoding="utf-8") == "keep me\n"


def test_diff_sees_work_the_agent_committed(tmp_path: Path) -> None:
    _repo(tmp_path)
    base = base_commit(tmp_path)
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "agent work"], cwd=tmp_path, check=True)

    assert "x = 2" in git_diff(tmp_path, base)


def test_diff_sees_files_the_agent_created(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    base = base_commit(tmp_path)
    (tmp_path / "new_module.py").write_text("y = 3\n", encoding="utf-8")

    diff = git_diff(tmp_path, base)
    assert "new_module.py" in diff
    assert "y = 3" in diff


def test_diff_sees_work_in_a_gitignored_path(tmp_path: Path) -> None:
    """An agent's change can land in a path the repo already gitignores (local config,
    generated code). The run diff — triage, feedback, review — must still show it, while
    `.orc/` and dependency trees stay out."""
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    (tmp_path / ".gitignore").write_text("generated/\nnode_modules/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "ignore rules"], cwd=tmp_path, check=True)
    base = base_commit(tmp_path)

    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "out.py").write_text("value = 42\n", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("//dep\n", encoding="utf-8")
    (tmp_path / ".orc" / "runs").mkdir(parents=True)
    (tmp_path / ".orc" / "runs" / "log.txt").write_text("noise\n", encoding="utf-8")

    diff = git_diff(tmp_path, base)
    assert "generated/out.py" in diff
    assert "value = 42" in diff
    assert "node_modules" not in diff
    assert ".orc" not in diff


def test_diff_never_includes_orc_artifacts(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    base = base_commit(tmp_path)
    (tmp_path / ".orc" / "runs").mkdir(parents=True)
    (tmp_path / ".orc" / "runs" / "x.txt").write_text("artifact\n", encoding="utf-8")

    assert ".orc" not in git_diff(tmp_path, base)


def test_snapshot_uses_the_index_not_a_filesystem_walk(tmp_path: Path) -> None:
    _repo(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_real.py").write_text("def test_x(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "add tests"], cwd=tmp_path, check=True)

    # An untracked virtualenv must not be walked, and must not appear in the snapshot.
    venv_tests = tmp_path / ".venv" / "lib" / "site-packages" / "pkg"
    venv_tests.mkdir(parents=True)
    (venv_tests / "test_vendored.py").write_text("def test_y(): pass\n", encoding="utf-8")

    snapshot = test_file_snapshot(tmp_path, ["tests/**", "test_*.py", "*_test.py"])
    assert set(snapshot) == {"tests/test_real.py"}


def test_snapshot_catches_an_untracked_test_file(tmp_path: Path) -> None:
    """An agent that adds a brand-new untracked conftest to neuter a failing test must
    still trip the tamper guard; index-only enumeration would miss it."""
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    patterns = ["tests/**", "conftest.py", "test_*.py"]
    before = test_file_snapshot(tmp_path, patterns)

    (tmp_path / "conftest.py").write_text("collect_ignore = ['x']\n", encoding="utf-8")

    assert test_file_snapshot(tmp_path, patterns) != before


def test_default_patterns_cover_a_root_conftest(tmp_path: Path) -> None:
    """A root conftest.py is the prime pytest-collection tamper vector; the shipped
    SafetyConfig defaults must cover it, not just files under tests/."""
    from orc.config import SafetyConfig

    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    patterns = SafetyConfig().test_path_patterns
    before = test_file_snapshot(tmp_path, patterns)

    (tmp_path / "conftest.py").write_text("collect_ignore = ['tests']\n", encoding="utf-8")

    assert test_file_snapshot(tmp_path, patterns) != before


def test_snapshot_catches_a_gitignored_new_test_file(tmp_path: Path) -> None:
    """Enumeration is ignore-unaware, so a new conftest.py is caught even when the agent
    also adds a .gitignore rule for it (and the .gitignore edit is itself watched)."""
    from orc.config import SafetyConfig

    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    safety = SafetyConfig()
    before = test_file_snapshot(tmp_path, safety.test_path_patterns, safety.skip_dirs)

    (tmp_path / ".gitignore").write_text("tests/conftest.py\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text("collect_ignore = ['x']\n", encoding="utf-8")

    after = test_file_snapshot(tmp_path, safety.test_path_patterns, safety.skip_dirs)
    assert "tests/conftest.py" in after
    assert after != before


def test_self_ignoring_gitignore_cannot_hide_a_conftest(tmp_path: Path) -> None:
    """`tests/.gitignore` containing `*` ignores itself and everything beside it. An
    ignore-aware scan would see neither file; the guard must still trip."""
    from orc.config import SafetyConfig

    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    safety = SafetyConfig()
    before = test_file_snapshot(tmp_path, safety.test_path_patterns, safety.skip_dirs)

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / ".gitignore").write_text("*\n", encoding="utf-8")
    (tests_dir / "conftest.py").write_text("collect_ignore = ['x']\n", encoding="utf-8")

    after = test_file_snapshot(tmp_path, safety.test_path_patterns, safety.skip_dirs)
    assert "tests/conftest.py" in after
    assert after != before


def test_dependency_tree_churn_does_not_trip_the_guard(tmp_path: Path) -> None:
    """A dep install writes many test-named files under node_modules/. The skip-dir
    denylist (not ignore rules) keeps them out, so `npm install` never aborts the run."""
    from orc.config import SafetyConfig

    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    safety = SafetyConfig()
    before = test_file_snapshot(tmp_path, safety.test_path_patterns, safety.skip_dirs)

    # No .gitignore for node_modules/: the denylist alone must handle it.
    dep = tmp_path / "node_modules" / "pkg" / "dist"
    dep.mkdir(parents=True)
    (dep / "index.test.js").write_text("it('x', () => {});\n", encoding="utf-8")
    (dep / "conftest.py").write_text("collect_ignore = ['x']\n", encoding="utf-8")

    assert test_file_snapshot(tmp_path, safety.test_path_patterns, safety.skip_dirs) == before


def test_skip_dirs_config_matches_gitops_default(tmp_path: Path) -> None:
    """The two copies of the denylist must not drift."""
    from orc.config import SafetyConfig
    from orc.gitops import _DEFAULT_SKIP_DIRS

    assert tuple(SafetyConfig().skip_dirs) == _DEFAULT_SKIP_DIRS


def test_diff_does_not_mutate_the_real_index(tmp_path: Path) -> None:
    """git_diff surfaces new files through a throwaway index; the real one stays clean,
    so the next run's cleanliness check is not tripped by a stray intent-to-add entry."""
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    base = base_commit(tmp_path)
    (tmp_path / "created.py").write_text("z = 9\n", encoding="utf-8")

    assert "created.py" in git_diff(tmp_path, base)

    porcelain = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert porcelain.splitlines() == ["?? created.py"]
