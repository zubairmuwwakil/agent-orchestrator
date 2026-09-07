"""Codex CLI subprocess adapter discovered against `codex --help` and `codex exec`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

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
            "-s",
            "workspace-write",
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

        text_parts: list[str] = []
        ran_commands: list[str] = []
        tool_call_count = 0
        usage: dict[str, object] | None = None

        for line in completed.stdout.splitlines():
            line_str = line.strip()
            if not line_str or not (line_str.startswith("{") and line_str.endswith("}")):
                continue
            try:
                event = json.loads(line_str)
            except json.JSONDecodeError:
                continue

            if not isinstance(event, dict):
                continue

            _extract_event_info(event, text_parts, ran_commands, usage)
            if _is_tool_call(event):
                tool_call_count += 1

        final_text = "\n".join(text_parts).strip() if text_parts else raw_transcript
        return AgentResult(
            status=status,
            text=final_text,
            usage=usage,
            transcript_path=transcript_path,
            tool_call_count=tool_call_count if tool_call_count > 0 else None,
            ran_commands=ran_commands,
        )


def _is_tool_call(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type") or event.get("event") or "").casefold()
    if any(k in event_type for k in ("tool", "call", "command", "function")):
        return True
    item = event.get("item")
    if isinstance(item, dict):
        item_type = str(item.get("type") or "").casefold()
        if any(k in item_type for k in ("tool", "call", "command", "function")):
            return True
    return False


def _extract_event_info(
    event: dict[str, Any],
    text_parts: list[str],
    ran_commands: list[str],
    usage_collector: dict[str, object] | None,
) -> None:
    # Command extraction
    for key in ("command", "cmd"):
        val = event.get(key)
        if isinstance(val, str) and val not in ran_commands:
            ran_commands.append(val)
    item = event.get("item")
    if isinstance(item, dict):
        for key in ("command", "cmd"):
            val = item.get(key)
            if isinstance(val, str) and val not in ran_commands:
                ran_commands.append(val)

    # Text extraction
    for key in ("text", "content", "message"):
        val = event.get(key)
        if isinstance(val, str):
            text_parts.append(val)
    if isinstance(item, dict):
        for key in ("text", "content", "message"):
            val = item.get(key)
            if isinstance(val, str):
                text_parts.append(val)

    # Usage extraction
    if "usage" in event and isinstance(event["usage"], dict) and usage_collector is not None:
        usage_collector.update(event["usage"])


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value
