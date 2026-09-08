from pathlib import Path

import pytest

from orc.config import ConfigError, find_config


def test_a_repo_outside_this_tree_falls_back_to_the_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "elsewhere" / "somerepo"
    target.mkdir(parents=True)
    user_config = tmp_path / "home" / ".config" / "orc" / "orc.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ORC_CONFIG", raising=False)

    assert find_config(target) == user_config


def test_the_env_var_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "custom.toml"
    explicit.write_text("", encoding="utf-8")
    target = tmp_path / "repo"
    target.mkdir()
    (target / "orc.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("ORC_CONFIG", str(explicit))

    assert find_config(target) == explicit


def test_the_error_names_both_places_a_config_may_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.delenv("ORC_CONFIG", raising=False)
    target = tmp_path / "repo"
    target.mkdir()

    with pytest.raises(ConfigError, match=r"\.config/orc/orc\.toml"):
        find_config(target)
