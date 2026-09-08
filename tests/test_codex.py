import json
import os
import subprocess
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orc.adapters.base import AgentRequest
from orc.adapters.codex import CodexAdapter
from orc.config import AdapterConfig
from tests.conftest import read_fixture, require_cli


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


def test_codex_parses_real_recorded_events(tmp_path: Path) -> None:
    adapter = CodexAdapter(_codex_config())
    stdout = read_fixture("codex-exec-events.jsonl")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["codex"], 0, stdout=stdout, stderr="")
    )
    req = AgentRequest(
        prompt="fix it", mode="agent", model="gpt-5.6-terra", effort="high",
        cwd=tmp_path, timeout_s=30, transcript_path=tmp_path / "t.txt",
    )
    with patch("subprocess.run", mock_run):
        result = adapter.run(req)

    # item.started and item.completed share an id; counting keywords double-counts.
    assert result.tool_call_count == 1
    assert result.ran_commands == ['/bin/zsh -lc "pytest -q"']
    assert result.text == "I'll run the requested shell command, then make only the specified append to a.py.\nDone."
    assert result.usage is not None
    assert result.usage["input_tokens"] == 40157


def test_codex_never_combines_sandbox_and_approve_for_me(tmp_path: Path) -> None:
    """The CLI rejects that pair outright. Assert the invariant, not the whole argv."""
    adapter = CodexAdapter(_codex_config())
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["codex"], 0, stdout="", stderr="")
    )
    req = AgentRequest(
        prompt="fix it", mode="agent", model="gpt-5.6-terra", effort="high",
        cwd=tmp_path, timeout_s=30, transcript_path=tmp_path / "t.txt",
    )
    with patch("subprocess.run", mock_run):
        adapter.run(req)

    argv = mock_run.call_args[0][0]
    sandbox_flags = {"-s", "--sandbox"}
    assert not (sandbox_flags & set(argv) and "--approve-for-me" in argv)


def test_codex_passes_no_stdin(tmp_path: Path) -> None:
    adapter = CodexAdapter(_codex_config())
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["codex"], 0, stdout="", stderr="")
    )
    req = AgentRequest(
        prompt="fix it", mode="agent", model="gpt-5.6-terra", effort="high",
        cwd=tmp_path, timeout_s=30, transcript_path=tmp_path / "t.txt",
    )
    with patch("subprocess.run", mock_run):
        adapter.run(req)

    assert mock_run.call_args.kwargs["stdin"] is subprocess.DEVNULL


@pytest.mark.live
def test_codex_argv_is_accepted_by_the_real_cli(tmp_path: Path) -> None:
    """The check no mock can perform: does the CLI accept the flags we send?"""
    require_cli("codex")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    adapter = CodexAdapter(_codex_config())
    transcript = tmp_path / "t.txt"
    req = AgentRequest(
        prompt="Reply with exactly: OK", mode="agent", model="gpt-5.6-terra",
        effort="low", cwd=tmp_path, timeout_s=180, transcript_path=transcript,
    )
    result = adapter.run(req)

    output = transcript.read_text(encoding="utf-8")
    assert "cannot be used with" not in output, output[:500]
    assert "unexpected argument" not in output, output[:500]
    assert result.status in {"ok", "rate_limited"}


def _write_rollout(codex_home: Path, thread_id: str, used_5h: float, used_week: float) -> Path:
    session_dir = codex_home / "sessions" / "2026" / "09" / "07"
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"rollout-2026-09-07T13-53-18-{thread_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": thread_id}}) + "\n"
        + json.dumps(
            {
                "rate_limits": {
                    "primary": {
                        "used_percent": used_5h,
                        "window_minutes": 300,
                        "resets_at": 1788817290,
                    },
                    "secondary": {
                        "used_percent": used_week,
                        "window_minutes": 10080,
                        "resets_at": 1789356153,
                    },
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_codex_probes_quota_from_the_newest_rollout(tmp_path: Path) -> None:
    _write_rollout(tmp_path, "01a07d00-c866-79c3-9645-877ccb9aae60", 39.0, 38.0)
    adapter = CodexAdapter(_codex_config())

    with patch.dict(os.environ, {"CODEX_HOME": str(tmp_path)}):
        observation = adapter.quota_probe()

    assert observation is not None
    assert observation.source == "session-file"
    by_kind = {w.kind: w for w in observation.windows}
    assert by_kind["5h"].used_fraction == pytest.approx(0.39)
    assert by_kind["weekly"].used_fraction == pytest.approx(0.38)
    assert by_kind["weekly"].resets_at.tzinfo is UTC


def test_codex_quota_probe_is_none_when_no_rollout_exists(tmp_path: Path) -> None:
    adapter = CodexAdapter(_codex_config())
    with patch.dict(os.environ, {"CODEX_HOME": str(tmp_path)}):
        assert adapter.quota_probe() is None


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
