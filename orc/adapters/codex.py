"""Codex CLI subprocess adapter discovered against `codex --help` and `codex exec`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from orc.adapters.base import AgentAdapter, AgentRequest, AgentResult, AgentStatus
from orc.config import AdapterConfig


class CodexAdapter(AgentAdapter):
    """Run Codex non-interactively in JSON mode."""

    name = "codex"

    def __init__(self, config: AdapterConfig) -> None:
        self._command = config.command
        self._rate_limit_patterns = tuple(
            pattern.casefold() for pattern in config.rate_limit_patterns
        )

    def available(self) -> bool:
        """Check PATH and authentication status."""
        if shutil.which(self._command) is None:
            return False
        try:
            completed = subprocess.run(
                [self._command, "login", "status"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if completed.returncode == 0 and "logged in" in completed.stdout.casefold():
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass

        # Offline fallback: check ~/.codex/auth.json
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        auth_file = codex_home / "auth.json"
        if auth_file.is_file():
            try:
                auth = json.loads(auth_file.read_text(encoding="utf-8"))
                if bool(auth.get("tokens")) or bool(auth.get("OPENAI_API_KEY")):
                    return True
            except (OSError, json.JSONDecodeError):
                pass
        return bool(os.environ.get("OPENAI_API_KEY"))

    def run(self, req: AgentRequest) -> AgentResult:
        """Execute one Codex non-interactive task and persist its transcript."""
        transcript_path = req.transcript_path or req.cwd / ".orc" / "codex-transcript.txt"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self._command,
            "exec",
            req.prompt,
            "--json",
            "-m",
            req.model,
            "-C",
            str(req.cwd),
            "--approve-for-me",
        ]
        if req.effort:
            command.extend(["-c", f'model_reasoning_effort="{req.effort}"'])

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
            return AgentResult("timeout", "Codex timed out", None, transcript_path, None, [])
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

        parsed = _parse_events(completed.stdout)
        return AgentResult(
            status=status,
            text=parsed.text or raw_transcript,
            usage=parsed.usage,
            transcript_path=transcript_path,
            tool_call_count=parsed.tool_call_count,
            ran_commands=parsed.ran_commands,
        )


_TOOL_ITEM_TYPES = frozenset(
    {"command_execution", "file_change", "patch_apply", "mcp_tool_call", "web_search"}
)


@dataclass(slots=True)
class ParsedRun:
    """Everything the router needs from one Codex JSONL stream."""

    text: str
    ran_commands: list[str]
    tool_call_count: int | None
    usage: dict[str, object] | None
    thread_id: str | None


def _parse_events(stdout: str) -> ParsedRun:
    """Parse `codex exec --json` NDJSON. Schema verified against codex 0.x, 2026-09-07."""
    text_parts: list[str] = []
    ran_commands: list[str] = []
    tool_item_ids: set[str] = set()
    usage: dict[str, object] | None = None
    thread_id: str | None = None

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
        if event_type == "thread.started":
            candidate = event.get("thread_id")
            thread_id = candidate if isinstance(candidate, str) else None
        elif event_type == "turn.completed":
            reported = event.get("usage")
            if isinstance(reported, dict):
                usage = dict(reported)
        elif event_type == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            elif item_type in _TOOL_ITEM_TYPES:
                item_id = item.get("id")
                tool_item_ids.add(
                    str(item_id) if item_id is not None else str(len(tool_item_ids))
                )
                command = item.get("command")
                if isinstance(command, str) and command not in ran_commands:
                    ran_commands.append(command)

    return ParsedRun(
        text="\n".join(text_parts).strip(),
        ran_commands=ran_commands,
        tool_call_count=len(tool_item_ids) or None,
        usage=usage,
        thread_id=thread_id,
    )


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value
