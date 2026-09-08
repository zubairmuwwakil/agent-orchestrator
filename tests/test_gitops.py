import os
import subprocess
from pathlib import Path

import pytest

from orc.gitops import SafetyError, ensure_orc_excluded, ensure_safe_target, git_dir


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
