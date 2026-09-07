import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from orc.adapters.base import AgentRequest
from orc.adapters.codex import CodexAdapter
from orc.config import AdapterConfig


def _codex_config() -> AdapterConfig:
    return AdapterConfig(
        command="codex",
        rate_limit_patterns=["rate limit", "quota exceeded", "too many requests"],
    )


def test_codex_available_false_when_binary_missing() -> None:
    adapter = CodexAdapter(_codex_config())
    with patch("shutil.which", return_value=None):
        assert not adapter.available()


def test_codex_available_true_when_login_status_ok() -> None:
    adapter = CodexAdapter(_codex_config())
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["codex"], 0, stdout="Logged in using ChatGPT")
    )
    with patch("shutil.which", return_value="/bin/codex"), patch("subprocess.run", mock_run):
        assert adapter.available()


def test_codex_available_fallback_to_auth_file(tmp_path: Path) -> None:
    adapter = CodexAdapter(_codex_config())
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({"tokens": {"access_token": "secret"}}), encoding="utf-8")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["codex"], 1, stdout="Not logged in")
    )
    with (
        patch("shutil.which", return_value="/bin/codex"),
        patch("subprocess.run", mock_run),
        patch.dict("os.environ", {"CODEX_HOME": str(tmp_path)}),
    ):
        assert adapter.available()


def test_codex_run_parses_jsonl_events_and_commands(tmp_path: Path) -> None:
    adapter = CodexAdapter(_codex_config())
    events = [
        json.dumps({"type": "tool_call", "command": "pytest -q"}),
        json.dumps({"type": "message", "text": "Fixed the failing test"}),
    ]
    stdout = "\n".join(events) + "\n"
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["codex"], 0, stdout=stdout, stderr="")
    )

    transcript = tmp_path / "transcript.txt"
    req = AgentRequest(
        prompt="fix it",
        mode="agent",
        model="gpt-5.6-terra",
        effort="high",
        cwd=tmp_path,
        timeout_s=30,
        transcript_path=transcript,
    )
    with patch("subprocess.run", mock_run):
        result = adapter.run(req)

    assert result.status == "ok"
    assert result.tool_call_count == 1
    assert result.ran_commands == ["pytest -q"]
    assert "Fixed the failing test" in result.text
    assert transcript.is_file()


def test_codex_run_detects_rate_limit(tmp_path: Path) -> None:
    adapter = CodexAdapter(_codex_config())
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            ["codex"], 1, stdout="", stderr="Error: You have exceeded your rate limit."
        )
    )

    req = AgentRequest(
        prompt="fix it",
        mode="agent",
        model="gpt-5.6-terra",
        effort="high",
        cwd=tmp_path,
        timeout_s=30,
        transcript_path=tmp_path / "transcript.txt",
    )
    with patch("subprocess.run", mock_run):
        result = adapter.run(req)

    assert result.status == "rate_limited"


def test_codex_run_handles_timeout(tmp_path: Path) -> None:
    adapter = CodexAdapter(_codex_config())
    mock_run = MagicMock(
        side_effect=subprocess.TimeoutExpired(["codex"], 30, output="partial output")
    )

    req = AgentRequest(
        prompt="fix it",
        mode="agent",
        model="gpt-5.6-terra",
        effort="high",
        cwd=tmp_path,
        timeout_s=30,
        transcript_path=tmp_path / "transcript.txt",
    )
    with patch("subprocess.run", mock_run):
        result = adapter.run(req)

    assert result.status == "timeout"
    assert "timed out" in result.text
