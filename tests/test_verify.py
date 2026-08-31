from pathlib import Path

from orc.config import VerifyConfig
from orc.verify import detect_commands, run_verification


def test_target_instructions_take_precedence_over_config_and_autodetection(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "# Notes\n\n## Testing\n\n```sh\npython -m pytest -q\nruff check .\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n", encoding="utf-8")

    plan = detect_commands(tmp_path, VerifyConfig(tests=["different-test"]))

    assert plan.tests == ["python -m pytest -q"]
    assert plan.lint == ["ruff check ."]


def test_python_project_without_test_files_has_lint_only_plan(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("answer = 42\n", encoding="utf-8")

    plan = detect_commands(tmp_path, VerifyConfig())

    assert plan.tests == []
    assert plan.lint == ["ruff check ."]


def test_no_test_project_reports_partial_lint_only_verification(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("answer = 42\n", encoding="utf-8")
    plan = detect_commands(tmp_path, VerifyConfig())

    result = run_verification(tmp_path, plan, timeout_s=30)

    assert result.ok
    assert result.partial
    assert "No tests detected; completed lint/build verification only." in result.output


def test_fenced_commands_strip_trailing_shell_comments(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text(
        "## Commands\n\n"
        "```\n"
        "uv run pytest -q        # tests\n"
        "uv run ruff check .     # lint\n"
        "uv run mypy orc         # types (permissive config is fine initially)\n"
        "```\n",
        encoding="utf-8",
    )

    plan = detect_commands(tmp_path, VerifyConfig())

    assert plan.tests == ["uv run pytest -q"]
    assert plan.lint == ["uv run ruff check .", "uv run mypy orc"]
