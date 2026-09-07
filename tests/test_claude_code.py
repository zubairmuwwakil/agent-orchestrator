import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orc.adapters.base import AgentRequest
from orc.adapters.claude_code import ClaudeCodeAdapter
from orc.config import AdapterConfig
from tests.conftest import read_fixture, require_cli


def _config() -> AdapterConfig:
    return AdapterConfig(command="claude", rate_limit_patterns=["usage limit"])


def _request(tmp_path: Path) -> AgentRequest:
    return AgentRequest(
        prompt="fix it", mode="agent", model="sonnet", effort="high",
        cwd=tmp_path, timeout_s=900, transcript_path=tmp_path / "t.txt",
    )


def test_claude_extracts_tool_calls_and_commands(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter(_config())
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            ["claude"], 0, stdout=read_fixture("claude-stream-json.jsonl"), stderr=""
        )
    )
    with patch("subprocess.run", mock_run):
        result = adapter.run(_request(tmp_path))

    assert result.tool_call_count == 2          # one Bash, one Edit
    assert result.ran_commands == ["pytest -q"]  # Bash commands only
    assert result.text == "Fixed the failing test."
    assert result.status == "ok"
    assert result.usage is not None
    assert result.usage["total_cost_usd"] == 0.2405


def test_claude_loads_project_context_and_not_the_users_globals(tmp_path: Path) -> None:
    """--safe-mode hid the target repo's CLAUDE.md; verified A/B against the real CLI."""
    adapter = ClaudeCodeAdapter(_config())
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["claude"], 0, stdout="", stderr="")
    )
    with patch("subprocess.run", mock_run):
        adapter.run(_request(tmp_path))

    argv = mock_run.call_args[0][0]
    assert "--safe-mode" not in argv
    assert "--setting-sources" in argv
    assert argv[argv.index("--setting-sources") + 1] == "project"
    assert "stream-json" in argv
    assert mock_run.call_args.kwargs["stdin"] is subprocess.DEVNULL


@pytest.mark.live
def test_claude_argv_is_accepted_by_the_real_cli(tmp_path: Path) -> None:
    require_cli("claude")
    adapter = ClaudeCodeAdapter(_config())
    request = _request(tmp_path)
    request.prompt = "Reply with exactly: OK"
    request.effort = "low"
    result = adapter.run(request)

    output = request.transcript_path.read_text(encoding="utf-8")
    assert "unknown option" not in output.casefold(), output[:500]
    assert result.status in {"ok", "rate_limited"}
    assert result.tool_call_count is not None or result.text
