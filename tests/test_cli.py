from pathlib import Path

from typer.testing import CliRunner

from orc.cli import app

runner = CliRunner()


def test_bare_task_and_subcommands_coexist() -> None:
    """SPEC §1's one-line UX and §11's subcommands must both work."""
    help_result = runner.invoke(app, ["--help"])

    assert help_result.exit_code == 0
    assert "quota" in help_result.output
    assert "log" in help_result.output


def test_log_is_a_registered_subcommand(tmp_path: Path) -> None:
    result = runner.invoke(app, ["log", "--target", str(tmp_path), "--last", "1"])

    assert result.exit_code == 0
    assert result.output == "no recorded runs\n"


def test_bare_task_is_not_misparsed_as_a_subcommand(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ORC_CONFIG", str(tmp_path / "missing.toml"))
    result = runner.invoke(app, ["fix the auth test"])

    assert result.exit_code == 2  # no config in the isolated test environment
    assert "No such command" not in result.output
    assert "Traceback" not in result.output


def test_quota_does_not_need_a_target_repo(tmp_path: Path) -> None:
    result = runner.invoke(app, ["quota", "--target", str(tmp_path)])

    assert result.exit_code in {0, 2}  # 2 only when no config is discoverable
    assert "Traceback" not in result.output
