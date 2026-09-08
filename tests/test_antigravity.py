"""Tests for the Antigravity CLI adapter, including its recorded real stream."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orc.adapters.antigravity import AntigravityAdapter
from orc.adapters.base import AgentRequest
from orc.config import AdapterConfig
from tests.conftest import read_fixture, require_cli

_USAGE_ENVELOPE = json.dumps(
    {
        "conversation_id": "",
        "status": "SUCCESS",
        "usage": {"total_tokens": 0},
        "command": {
            "name": "usage",
            "data": {
                "groups": [
                    {
                        "name": "Gemini Models",
                        "buckets": [
                            {
                                "window": "weekly",
                                "remaining_fraction": 0.6857,
                                "reset_time": "2026-09-11T03:55:06Z",
                            },
                            {
                                "window": "5h",
                                "remaining_fraction": 0.8611,
                                "reset_time": "2026-09-07T22:24:46Z",
                            },
                        ],
                    },
                    {
                        "name": "Claude and GPT models",
                        "buckets": [
                            {
                                "window": "weekly",
                                "remaining_fraction": 0.7492,
                                "reset_time": "2026-09-14T16:42:06Z",
                            },
                            {
                                "window": "5h",
                                "remaining_fraction": 0.2475,
                                "reset_time": "2026-09-07T21:42:06Z",
                            },
                        ],
                    },
                ]
            },
        },
    }
)


def _config() -> AdapterConfig:
    return AdapterConfig(
        command="agy",
        pool="antigravity_gemini",
        rate_limit_patterns=["rate limit", "quota exceeded"],
        supported_efforts=["low", "medium", "high"],
    )


def _request(tmp_path: Path, effort: str = "medium") -> AgentRequest:
    return AgentRequest(
        prompt="fix it",
        mode="agent",
        model="gemini-3.8-flash",
        effort=effort,
        cwd=tmp_path,
        timeout_s=900,
        transcript_path=tmp_path / "t.txt",
    )


def test_effort_is_composed_into_the_model_slug(tmp_path: Path) -> None:
    """agy models lists no bare slug; the tier is part of the model name."""
    adapter = AntigravityAdapter(_config())
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(["agy"], 0, stdout="", stderr=""))
    with patch("subprocess.run", mock_run):
        adapter.run(_request(tmp_path, effort="medium"))

    argv = mock_run.call_args[0][0]
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-medium"
    assert "--effort" not in argv


def test_an_unsupported_effort_clamps_to_the_highest_supported(tmp_path: Path) -> None:
    """The ladder bumps to xhigh; agy offers only low/medium/high."""
    adapter = AntigravityAdapter(_config())
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(["agy"], 0, stdout="", stderr=""))
    with patch("subprocess.run", mock_run):
        adapter.run(_request(tmp_path, effort="xhigh"))

    argv = mock_run.call_args[0][0]
    assert argv[argv.index("--model") + 1] == "gemini-3.8-flash-high"


def test_working_directory_is_set_on_the_subprocess_not_a_flag(tmp_path: Path) -> None:
    """agy has no -C; cwd must come from the subprocess and --add-dir."""
    adapter = AntigravityAdapter(_config())
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(["agy"], 0, stdout="", stderr=""))
    with patch("subprocess.run", mock_run):
        adapter.run(_request(tmp_path))

    assert mock_run.call_args.kwargs["cwd"] == tmp_path
    assert mock_run.call_args.kwargs["stdin"] is subprocess.DEVNULL
    argv = mock_run.call_args[0][0]
    assert "-C" not in argv
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path)


def test_parses_the_recorded_stream(tmp_path: Path) -> None:
    """The real stream has step_update records, not a generic tool-completed event."""
    adapter = AntigravityAdapter(_config())
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            ["agy"], 0, stdout=read_fixture("agy-stream-json.jsonl"), stderr=""
        )
    )
    with patch("subprocess.run", mock_run):
        result = adapter.run(_request(tmp_path))

    assert result.status == "ok"
    assert result.tool_call_count == 1
    assert result.ran_commands == ["echo hello-from-agy"]
    assert result.usage == {
        "input_tokens": 6121,
        "output_tokens": 69,
        "thinking_tokens": 0,
        "cache_read_tokens": 8125,
        "total_tokens": 6190,
    }


def test_quota_probe_selects_its_group_and_inverts_the_polarity() -> None:
    """agy reports remaining; QuotaWindow stores used."""
    adapter = AntigravityAdapter(_config(), quota_group="Gemini Models")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["agy"], 0, stdout=_USAGE_ENVELOPE, stderr="")
    )
    with (
        patch("shutil.which", return_value="/usr/local/bin/agy"),
        patch("subprocess.run", mock_run),
    ):
        observation = adapter.quota_probe()

    assert observation is not None
    assert observation.source == "command"
    by_kind = {window.kind: window for window in observation.windows}
    assert by_kind["weekly"].used_fraction == pytest.approx(1 - 0.6857, abs=1e-4)
    assert by_kind["5h"].used_fraction == pytest.approx(1 - 0.8611, abs=1e-4)


def test_quota_probe_returns_none_when_the_group_is_absent() -> None:
    adapter = AntigravityAdapter(_config(), quota_group="No Such Group")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["agy"], 0, stdout=_USAGE_ENVELOPE, stderr="")
    )
    with (
        patch("shutil.which", return_value="/usr/local/bin/agy"),
        patch("subprocess.run", mock_run),
    ):
        assert adapter.quota_probe() is None


@pytest.mark.live
def test_agy_argv_is_accepted_by_the_real_cli(tmp_path: Path) -> None:
    """The real CLI, rather than a mocked argv expectation, decides this contract."""
    require_cli("agy")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    adapter = AntigravityAdapter(_config())
    request = _request(tmp_path, effort="low")
    request.prompt = "Reply with exactly: OK"
    result = adapter.run(request)

    output = request.transcript_path.read_text(encoding="utf-8")
    assert "flag provided but not defined" not in output, output[:500]
    assert "unknown model" not in output.casefold(), output[:500]
    first_event = json.loads(output.splitlines()[0])
    assert first_event["event"] == "init"
    assert first_event["init"]["model"] == "gemini-3.8-flash-low"
    assert result.status in {"ok", "rate_limited"}
