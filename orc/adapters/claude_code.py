"""Claude Code subprocess adapter discovered against the current CLI help."""

from __future__ import annotations

import json
import shutil
import subprocess

from orc.adapters.base import AgentAdapter, AgentRequest, AgentResult, AgentStatus
from orc.config import AdapterConfig


class ClaudeCodeAdapter(AgentAdapter):
    """Run Claude Code in non-interactive JSON mode."""

    name = "claude"

    def __init__(self, config: AdapterConfig) -> None:
        self._command = config.command
        self._rate_limit_patterns = tuple(
            pattern.casefold() for pattern in config.rate_limit_patterns
        )

    def available(self) -> bool:
        """Check PATH and Claude Code's machine-readable authentication status."""
        if shutil.which(self._command) is None:
            return False
        try:
            completed = subprocess.run(
                [self._command, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            status = json.loads(completed.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return False
        return completed.returncode == 0 and bool(status.get("loggedIn"))

    def run(self, req: AgentRequest) -> AgentResult:
        """Execute one Claude Code print-mode task and persist its raw transcript."""
        transcript_path = req.transcript_path or req.cwd / ".orc" / "claude-transcript.txt"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self._command,
            "--safe-mode",
            "--print",
            req.prompt,
            "--model",
            req.model,
            "--effort",
            req.effort,
            "--output-format",
            "json",
            "--permission-mode",
            "acceptEdits",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=req.cwd,
                capture_output=True,
                text=True,
                timeout=req.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            output = _as_text(error.stdout) + _as_text(error.stderr)
            transcript_path.write_text(output, encoding="utf-8")
            return AgentResult("timeout", "Claude Code timed out", None, transcript_path, None, [])
        except OSError as error:
            transcript_path.write_text(str(error), encoding="utf-8")
            return AgentResult("unavailable", str(error), None, transcript_path, None, [])

        raw_transcript = completed.stdout
        if completed.stderr:
            raw_transcript += f"\n--- stderr ---\n{completed.stderr}"
        transcript_path.write_text(raw_transcript, encoding="utf-8")
        combined_output = raw_transcript.casefold()
        status: AgentStatus = "ok" if completed.returncode == 0 else "fail"
        if any(pattern in combined_output for pattern in self._rate_limit_patterns):
            status = "rate_limited"

        payload: dict[str, object] = {}
        try:
            parsed = json.loads(completed.stdout)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            if status == "ok":
                status = "error"

        if payload.get("is_error") is True and status == "ok":
            status = "fail"
        result_text = payload.get("result")
        text = result_text if isinstance(result_text, str) else raw_transcript
        usage_data: dict[str, object] = {
            key: payload[key]
            for key in ("total_cost_usd", "duration_ms", "duration_api_ms", "num_turns")
            if key in payload
        }
        usage = usage_data or None
        return AgentResult(status, text, usage, transcript_path, None, [])


def _as_text(value: str | bytes | None) -> str:
    """Normalize subprocess timeout output, whose type is bytes | str | None."""
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value
