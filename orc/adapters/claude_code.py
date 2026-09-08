"""Claude Code subprocess adapter discovered against the current CLI help."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

from orc.adapters.base import (
    AgentAdapter,
    AgentRequest,
    AgentResult,
    AgentStatus,
    QuotaObservation,
    QuotaWindow,
    WindowKind,
)
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
            "--print",
            req.prompt,
            "--model",
            req.model,
            "--effort",
            req.effort,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "acceptEdits",
            "--setting-sources",
            "project",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=req.cwd,
                capture_output=True,
                text=True,
                timeout=req.timeout_s,
                check=False,
                stdin=subprocess.DEVNULL,
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
        parsed = _parse_stream(completed.stdout)
        status: AgentStatus = "ok" if completed.returncode == 0 else "fail"
        if parsed.is_error:
            status = "fail"
        if _is_rate_limited(
            completed.stderr, parsed.text, completed.returncode, self._rate_limit_patterns
        ):
            status = "rate_limited"
        quota = (
            _observation_from_rate_limit(parsed.rate_limit_info) if parsed.rate_limit_info else None
        )
        return AgentResult(
            status=status,
            text=parsed.text or raw_transcript,
            usage=parsed.usage,
            transcript_path=transcript_path,
            tool_call_count=parsed.tool_call_count,
            ran_commands=parsed.ran_commands,
            quota=quota,
        )


@dataclass(slots=True)
class ParsedStream:
    """Everything the router needs from one Claude Code stream-json run."""

    text: str
    ran_commands: list[str]
    tool_call_count: int | None
    usage: dict[str, object] | None
    is_error: bool | None
    rate_limit_info: dict[str, object] | None


def _parse_stream(stdout: str) -> ParsedStream:
    """Parse `--output-format stream-json` NDJSON. Verified against the CLI, 2026-09-07."""
    text_parts: list[str] = []
    ran_commands: list[str] = []
    tool_calls = 0
    usage: dict[str, object] | None = None
    is_error: bool | None = None
    rate_limit_info: dict[str, object] | None = None
    final_text: str | None = None

    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        if event_type == "assistant":
            message = event.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            for block in blocks or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    tool_calls += 1
                    if block.get("name") == "Bash":
                        tool_input = block.get("input")
                        shell = tool_input.get("command") if isinstance(tool_input, dict) else None
                        if isinstance(shell, str) and shell not in ran_commands:
                            ran_commands.append(shell)
                elif block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
        elif event_type == "rate_limit_event":
            info = event.get("rate_limit_info")
            if isinstance(info, dict):
                rate_limit_info = info
        elif event_type == "result":
            error_flag = event.get("is_error")
            is_error = bool(error_flag) if error_flag is not None else None
            result_text = event.get("result")
            if isinstance(result_text, str):
                final_text = result_text
            usage = {
                key: event[key]
                for key in ("total_cost_usd", "duration_ms", "duration_api_ms", "num_turns")
                if key in event
            } or None

    return ParsedStream(
        text=final_text if final_text is not None else "\n".join(text_parts).strip(),
        ran_commands=ran_commands,
        tool_call_count=tool_calls or None,
        usage=usage,
        is_error=is_error,
        rate_limit_info=rate_limit_info,
    )


# Claude names its windows; codex reports minutes. An unrecognized name is skipped
# rather than guessed — a mislabeled window would block the wrong lane.
_CLAUDE_WINDOW_KINDS: dict[str, WindowKind] = {
    "five_hour": "5h",
    "seven_day": "weekly",
    "monthly": "monthly",
}


def _observation_from_rate_limit(info: dict[str, object]) -> QuotaObservation | None:
    """Normalize one `rate_limit_event` into the vendor-neutral shape. Claude reports
    `utilization` (already fraction used), so no polarity inversion is needed here."""
    kind = _CLAUDE_WINDOW_KINDS.get(str(info.get("rateLimitType", "")))
    utilization = info.get("utilization")
    resets_at = info.get("resetsAt")
    if (
        kind is None
        or not isinstance(utilization, int | float)
        or not isinstance(resets_at, int | float)
    ):
        return None
    return QuotaObservation(
        windows=[
            QuotaWindow(kind, float(utilization), datetime.fromtimestamp(float(resets_at), UTC))
        ],
        observed_at=datetime.now(UTC),
        source="stream",
    )


def _as_text(value: str | bytes | None) -> str:
    """Normalize subprocess timeout output, whose type is bytes | str | None."""
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _is_rate_limited(
    stderr: str, agent_text: str, returncode: int, patterns: tuple[str, ...]
) -> bool:
    """Treat stderr as authoritative; inspect agent prose only after a failed run."""
    if any(pattern in stderr.casefold() for pattern in patterns):
        return True
    return returncode != 0 and any(pattern in agent_text.casefold() for pattern in patterns)
