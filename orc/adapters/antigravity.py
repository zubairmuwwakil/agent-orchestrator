"""Antigravity `agy` adapter, discovered against agy 1.1.27."""

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
)
from orc.config import AdapterConfig


class AntigravityAdapter(AgentAdapter):
    """Run Antigravity's `agy` CLI non-interactively."""

    name = "antigravity"

    def __init__(self, config: AdapterConfig, quota_group: str | None = None) -> None:
        self._command = config.command
        self._supported_efforts = list(config.supported_efforts)
        self._rate_limit_patterns = tuple(
            pattern.casefold() for pattern in config.rate_limit_patterns
        )
        self._quota_group = quota_group

    def available(self) -> bool:
        """Return whether `agy` is installed and authenticated enough to list models."""
        if shutil.which(self._command) is None:
            return False
        try:
            completed = subprocess.run(
                [self._command, "models"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0 and bool(completed.stdout.strip())

    def _model_slug(self, model: str, effort: str) -> str:
        """Compose agy's embedded reasoning suffix, clamping beyond its ladder."""
        if not self._supported_efforts:
            return model
        chosen = effort if effort in self._supported_efforts else self._supported_efforts[-1]
        for supported in self._supported_efforts:
            suffix = f"-{supported}"
            if model.endswith(suffix):
                model = model.removesuffix(suffix)
                break
        return f"{model}-{chosen}"

    def run(self, req: AgentRequest) -> AgentResult:
        """Execute one `agy --print` task and persist its stream-json transcript."""
        transcript_path = req.transcript_path or req.cwd / ".orc" / "agy-transcript.txt"
        transcript_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self._command,
            "--print",
            req.prompt,
            "--model",
            self._model_slug(req.model, req.effort),
            "--output-format",
            "stream-json",
            "--mode",
            "accept-edits",
            "--print-timeout",
            f"{req.timeout_s}s",
            "--add-dir",
            str(req.cwd),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=req.cwd,
                capture_output=True,
                text=True,
                timeout=req.timeout_s + 30,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as error:
            transcript_path.write_text(
                _as_text(error.stdout) + _as_text(error.stderr), encoding="utf-8"
            )
            return AgentResult("timeout", "agy timed out", None, transcript_path, None, [])
        except OSError as error:
            transcript_path.write_text(str(error), encoding="utf-8")
            return AgentResult("unavailable", str(error), None, transcript_path, None, [])

        raw_transcript = completed.stdout
        if completed.stderr:
            raw_transcript += f"\n--- stderr ---\n{completed.stderr}"
        transcript_path.write_text(raw_transcript, encoding="utf-8")

        parsed = _parse_stream(completed.stdout)
        status: AgentStatus = "ok" if completed.returncode == 0 else "fail"
        if _is_rate_limited(
            completed.stderr, parsed.text, completed.returncode, self._rate_limit_patterns
        ):
            status = "rate_limited"
        return AgentResult(
            status=status,
            text=parsed.text or raw_transcript,
            usage=parsed.usage,
            transcript_path=transcript_path,
            tool_call_count=parsed.tool_call_count,
            ran_commands=parsed.ran_commands,
        )

    def quota_probe(self) -> QuotaObservation | None:
        """Read the free `/usage` utilization command for this vendor-side group."""
        if self._quota_group is None or shutil.which(self._command) is None:
            return None
        try:
            completed = subprocess.run(
                [self._command, "--print", "/usage", "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            envelope = json.loads(completed.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return None
        if completed.returncode != 0 or not isinstance(envelope, dict):
            return None

        command = envelope.get("command")
        data = command.get("data") if isinstance(command, dict) else None
        groups = data.get("groups") if isinstance(data, dict) else None
        if not isinstance(groups, list):
            return None
        for group in groups:
            if not isinstance(group, dict) or group.get("name") != self._quota_group:
                continue
            windows: list[QuotaWindow] = []
            buckets = group.get("buckets")
            if not isinstance(buckets, list):
                continue
            for bucket in buckets:
                if not isinstance(bucket, dict):
                    continue
                kind = bucket.get("window")
                remaining = bucket.get("remaining_fraction")
                reset_time = bucket.get("reset_time")
                if kind not in {"5h", "weekly", "monthly"}:
                    continue
                if not isinstance(remaining, int | float) or not isinstance(reset_time, str):
                    continue
                try:
                    resets_at = datetime.fromisoformat(reset_time)
                except ValueError:
                    continue
                if resets_at.tzinfo is None:
                    continue
                windows.append(
                    QuotaWindow(
                        kind,
                        1.0 - float(remaining),
                        resets_at.astimezone(UTC),
                    )
                )
            if windows:
                return QuotaObservation(windows, datetime.now(UTC), "command")
        return None


@dataclass(slots=True)
class ParsedStream:
    """The triage-relevant information from an agy stream-json response."""

    text: str
    ran_commands: list[str]
    tool_call_count: int | None
    usage: dict[str, object] | None


def _parse_stream(stdout: str) -> ParsedStream:
    """Parse agy 1.1.27 stream-json events captured in the golden fixture.

    A tool emits multiple `step_update`s for one stable `step_index`; recording its
    active state prevents active and terminal updates from double-counting.
    """
    text_parts: list[str] = []
    ran_commands: list[str] = []
    tool_steps: set[str] = set()
    next_anonymous_step = 0
    usage: dict[str, object] | None = None

    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        if event.get("event") == "step_update":
            update = event.get("step_update")
            if not isinstance(update, dict):
                continue
            if update.get("step_type") == "tool":
                # A tool emits ACTIVE and terminal updates for one step. ACTIVE is
                # enough to establish an invocation and has a stable step_index.
                if update.get("state") != "ACTIVE":
                    continue
                step_index = update.get("step_index")
                if step_index is None:
                    step_id = f"anonymous-{next_anonymous_step}"
                    next_anonymous_step += 1
                else:
                    step_id = str(step_index)
                if step_id in tool_steps:
                    continue
                tool_steps.add(step_id)
                tool_info = update.get("tool_info")
                parameters = tool_info.get("parameters") if isinstance(tool_info, dict) else None
                shell = parameters.get("CommandLine") if isinstance(parameters, dict) else None
                if isinstance(shell, str):
                    ran_commands.append(shell)
            elif update.get("step_type") == "agent_response":
                response = update.get("response")
                if isinstance(response, str):
                    text_parts.append(response)
                reported_usage = update.get("usage")
                if isinstance(reported_usage, dict):
                    usage = dict(reported_usage)
        elif event.get("event") == "result":
            result = event.get("result")
            if not isinstance(result, dict):
                continue
            response = result.get("response")
            if isinstance(response, str) and response:
                text_parts.append(response)
            reported_usage = result.get("usage")
            if isinstance(reported_usage, dict):
                usage = dict(reported_usage)

    return ParsedStream(
        text="\n".join(text_parts).strip(),
        ran_commands=ran_commands,
        tool_call_count=len(tool_steps),
        usage=usage,
    )


def _as_text(value: str | bytes | None) -> str:
    """Normalize subprocess timeout output, which may be bytes despite text mode."""
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
