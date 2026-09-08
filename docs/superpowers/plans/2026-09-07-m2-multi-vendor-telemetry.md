# M2: Multi-Vendor Ladder with Telemetry-Driven Routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `orc` route coding tasks across three working vendor CLIs, choosing lanes from real quota telemetry instead of guesses, and fix the twelve defects in the `1a14ed9` baseline.

**Architecture:** Each adapter wraps one official CLI as a subprocess and normalizes that vendor's quota mechanism into a `QuotaObservation`; `ledger.py` consumes only that normalized shape and knows nothing vendor-specific. The router filters lane candidates by ledger eligibility before spending a run, walks the configured ladder on failure using lazy-vs-dumb triage, and falls through to the `volume` lane only when every rung is quota-blocked. Verification is independent of the agent and decides pass/fail; the agent never asserts success.

**Tech Stack:** Python ≥3.12, `uv`, `typer`, `pydantic`, `rich`. Tests `pytest`, lint `ruff`, types `mypy`. No new runtime dependencies.

**Spec:** [`SPEC.md`](../../../SPEC.md) — authoritative. Rationale and probe evidence: [`docs/superpowers/specs/2026-09-07-m2-design.md`](../specs/2026-09-07-m2-design.md).

## Global Constraints

- Build only M2. Do not start M3 (review protocol, consult mode) or M4 (Copilot).
- Never weaken, skip, or delete a test to make it pass, in this repo or a target repo.
- Check command, must pass before every commit: `uv run pytest -q && uv run ruff check . && uv run mypy orc`
- Format with `uv run ruff format .`
- Runtime dependencies are limited to Typer, Pydantic, Rich. Ask before adding any other runtime or test dependency.
- Keep subprocess calls inside `orc/adapters/`, `orc/verify.py`, or `orc/gitops.py`.
- Keep model names, effort levels, lane orders, estimates, quota group names, and error patterns in `orc.toml`, never in Python.
- A missing or unauthenticated CLI is a warning and a skipped lane, never a crash.
- Store runtime state only under the target repo's `.orc/`. The single permitted write inside `.git` is appending `.orc/` to `.git/info/exclude`.
- Type hints throughout. Small modules matching the `SPEC.md` §4 layout.
- Tests must not call live APIs, except tests marked `@pytest.mark.live`, which are deselected by default.
- Never commit secrets. Adapters use each CLI's own authentication.

## Cross-Vendor Review Gates

Per [`AGENTS.md`](../../../AGENTS.md), high-blast-radius tasks are reviewed by a vendor other than their author. The reviewer receives the task statement, `git diff`, and verification output — **never** the author's transcript.

| Task | Blast radius | Reviewer |
|---|---|---|
| 4 | writes inside `.git` | codex |
| 5 | decides what orc believes an agent did, and what it refuses | codex |
| 9 | quota arithmetic every later routing decision trusts | codex |
| 10 | decides what gets spent | codex |
| 11 | a new vendor contract | agy |

Tasks 2 and 3 are deliberately **not** reviewed: their failure mode is an argv the CLI rejects, and the `live` test settles that question in a way no reviewer can be talked out of. Tasks 1, 6, 7, 8, 12, 13, 14 and 15 are low blast radius.

Reviewer command template (substitute the task number):

```bash
git diff main...HEAD > /tmp/orc-review-diff.txt
codex exec "You are reviewing a diff for a quota-aware coding-agent orchestrator.
Read /tmp/orc-review-diff.txt. Report findings as JSON:
[{\"severity\":\"P0|P1|P2|P3\",\"file\":\"...\",\"line\":0,\"issue\":\"...\",\"why\":\"...\",\"suggestion\":\"...\"}]
Focus on correctness and safety, not style. Report nothing if the diff is sound." \
  --json --approve-for-me --skip-git-repo-check -C . < /dev/null
```

Fix P0 and P1 findings before committing. Record P2/P3 in the commit message.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `orc/adapters/base.py` | Adapter ABC, `AgentRequest`/`AgentResult`, `QuotaWindow`/`QuotaObservation` | modify |
| `orc/adapters/claude_code.py` | Claude Code CLI: run, stream-json parsing, quota from `rate_limit_event` | modify |
| `orc/adapters/codex.py` | Codex CLI: run, JSONL parsing, quota from the session rollout file | modify |
| `orc/adapters/antigravity.py` | `agy` CLI: run, stream-json parsing, quota from `/usage` | **create** |
| `orc/config.py` | `orc.toml` schema and discovery | modify |
| `orc/ledger.py` | Normalized quota state, reserve policy, window reset | modify |
| `orc/router.py` | Lane eligibility, ladder walk, triage, volume fallback, run logging | modify |
| `orc/gitops.py` | Branch, exclude, base-commit diffing, test-file enumeration | modify |
| `orc/report.py` | Activity trail and quota table | modify |
| `orc/cli.py` | Typer app: bare task, `quota`, `log` | modify |
| `orc.toml` | Pools, lanes, ladder, reserve, quota group names | modify |
| `tests/fixtures/*.jsonl` | Real recorded CLI output | **create** |
| `tests/conftest.py` | `live` marker registration and skip logic | **create** |
| `pyproject.toml` | Register the `live` marker, deselect it by default | modify |

---

## Phase 1 — Make the adapters actually work

Nothing downstream is verifiable until codex runs at all and Claude's triage signals exist. These come first.

### Task 1: Test infrastructure — `live` marker and real fixtures

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/fixtures/codex-exec-events.jsonl`
- Create: `tests/fixtures/claude-stream-json.jsonl`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing.
- Produces: `@pytest.mark.live` marker, deselected unless `-m live`. Fixture files readable via `Path(__file__).parent / "fixtures" / "<name>"`.

- [ ] **Step 1: Register the marker and deselect it by default**

Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "live: runs a real vendor CLI; deselected by default, enable with -m live",
]
addopts = "-m 'not live'"
```

- [ ] **Step 2: Write `tests/conftest.py`**

```python
"""Shared fixtures. Live tests need a real, authenticated CLI on PATH."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(name: str) -> str:
    """Return recorded CLI output captured from a real vendor CLI."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def require_cli(command: str) -> None:
    """Skip a live test when its CLI is absent, rather than failing."""
    if shutil.which(command) is None:
        pytest.skip(f"{command} is not installed; live test skipped")
```

- [ ] **Step 3: Record the codex fixture**

Create `tests/fixtures/codex-exec-events.jsonl` with this exact content, captured from `codex exec --json --approve-for-me` on 2026-09-07:

```
{"type":"thread.started","thread_id":"01a07d00-c866-79c3-9645-877ccb9aae60"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"I'll run the requested shell command, then make only the specified append to a.py."}}
{"type":"item.started","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc \"pytest -q\"","aggregated_output":"","exit_code":null,"status":"in_progress"}}
{"type":"item.completed","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc \"pytest -q\"","aggregated_output":"1 failed","exit_code":1,"status":"completed"}}
{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"Done."}}
{"type":"turn.completed","usage":{"input_tokens":40157,"cached_input_tokens":30208,"cache_write_input_tokens":0,"output_tokens":156,"reasoning_output_tokens":0}}
```

- [ ] **Step 4: Record the claude fixture**

Create `tests/fixtures/claude-stream-json.jsonl`. This is the real event shape from `claude --print --output-format stream-json --verbose`, trimmed to the fields the adapter reads:

```
{"type":"system","subtype":"init","session_id":"ac9229b1-f558-4058-8274-d3b57a817cfc","tools":["Bash","Edit","Read"]}
{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_015T","name":"Bash","input":{"command":"pytest -q","description":"Run tests"}}]}}
{"type":"user","message":{"content":[{"tool_use_id":"toolu_015T","type":"tool_result","content":"1 failed","is_error":false}]}}
{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_016T","name":"Edit","input":{"file_path":"app.py"}}]}}
{"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning","resetsAt":1789214400,"rateLimitType":"seven_day","utilization":0.59,"isUsingOverage":false}}
{"type":"assistant","message":{"content":[{"type":"text","text":"Fixed the failing test."}]}}
{"type":"result","is_error":false,"duration_api_ms":4761,"num_turns":2,"total_cost_usd":0.2405,"usage":{"input_tokens":4,"output_tokens":101}}
```

- [ ] **Step 5: Verify the marker deselects live tests**

Run: `uv run pytest -q`
Expected: `24 passed` — the same count as before, proving `addopts` did not change collection of existing tests.

Run: `uv run pytest -q -m live --collect-only`
Expected: `no tests ran` — no live tests exist yet.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml tests/conftest.py tests/fixtures/
git commit -m "test: add live marker and real recorded CLI fixtures"
```

---

### Task 2: D1 — codex adapter sends an argv the CLI rejects

The baseline passes `-s workspace-write` **and** `--approve-for-me`. The CLI's own argument parser refuses that pair, so every codex run exits 2 before any work happens. The existing tests mock `subprocess.run`, so they never see it.

**Files:**
- Modify: `orc/adapters/codex.py`
- Test: `tests/test_codex.py`

**Interfaces:**
- Consumes: `read_fixture`, `require_cli` from `tests/conftest.py` (Task 1).
- Produces: `CodexAdapter.run()` returning `AgentResult` with accurate `tool_call_count` (distinct completed tool items), `ran_commands` (shell command strings), `text` (agent messages only), and `usage` (from `turn.completed`). Module-level `_parse_events(stdout: str) -> ParsedRun`.

- [ ] **Step 1: Write the failing tests**

Replace `test_codex_run_parses_jsonl_events_and_commands` in `tests/test_codex.py` and add three tests:

```python
from tests.conftest import read_fixture, require_cli


def test_codex_parses_real_recorded_events(tmp_path: Path) -> None:
    adapter = CodexAdapter(_codex_config())
    stdout = read_fixture("codex-exec-events.jsonl")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["codex"], 0, stdout=stdout, stderr="")
    )
    req = AgentRequest(
        prompt="fix it",
        mode="agent",
        model="gpt-5.6-terra",
        effort="high",
        cwd=tmp_path,
        timeout_s=30,
        transcript_path=tmp_path / "t.txt",
    )
    with patch("subprocess.run", mock_run):
        result = adapter.run(req)

    # item.started and item.completed share an id; counting keywords double-counts.
    assert result.tool_call_count == 1
    assert result.ran_commands == ['/bin/zsh -lc "pytest -q"']
    assert (
        result.text
        == "I'll run the requested shell command, then make only the specified append to a.py.\nDone."
    )
    assert result.usage is not None
    assert result.usage["input_tokens"] == 40157


def test_codex_never_combines_sandbox_and_approve_for_me(tmp_path: Path) -> None:
    """The CLI rejects that pair outright. Assert the invariant, not the whole argv."""
    adapter = CodexAdapter(_codex_config())
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["codex"], 0, stdout="", stderr="")
    )
    req = AgentRequest(
        prompt="fix it",
        mode="agent",
        model="gpt-5.6-terra",
        effort="high",
        cwd=tmp_path,
        timeout_s=30,
        transcript_path=tmp_path / "t.txt",
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
        prompt="fix it",
        mode="agent",
        model="gpt-5.6-terra",
        effort="high",
        cwd=tmp_path,
        timeout_s=30,
        transcript_path=tmp_path / "t.txt",
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
        prompt="Reply with exactly: OK",
        mode="agent",
        model="gpt-5.6-terra",
        effort="low",
        cwd=tmp_path,
        timeout_s=180,
        transcript_path=transcript,
    )
    result = adapter.run(req)

    output = transcript.read_text(encoding="utf-8")
    assert "cannot be used with" not in output, output[:500]
    assert "unexpected argument" not in output, output[:500]
    assert result.status in {"ok", "rate_limited"}
```

Add `import pytest` at the top of `tests/test_codex.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_codex.py -q`
Expected: FAIL — `test_codex_parses_real_recorded_events` reports `tool_call_count == 2` (double-counted) and `text` containing the command string; `test_codex_never_combines_sandbox_and_approve_for_me` fails because both are present; `test_codex_passes_no_stdin` raises `KeyError: 'stdin'`.

- [ ] **Step 3: Fix the argv and rewrite the parser**

In `orc/adapters/codex.py`, replace the command construction in `run()`:

```python
        command = [
            self._command,
            "exec",
            req.prompt,
            "--json",
            "-m",
            req.model,
            "-C",
            str(req.cwd),
            "--approve-for-me",  # implies workspace-write; cannot be combined with -s
        ]
        if req.effort:
            command.extend(["-c", f'model_reasoning_effort="{req.effort}"'])
```

and add `stdin=subprocess.DEVNULL` to the `subprocess.run(...)` call.

Replace `_is_tool_call` and `_extract_event_info` entirely with:

```python
# Item types that represent a tool invocation. Counted once, on completion.
_TOOL_ITEM_TYPES = frozenset(
    {"command_execution", "file_change", "patch_apply", "mcp_tool_call", "web_search"}
)


@dataclass(slots=True)
class ParsedRun:
    """Everything the router needs from one codex JSONL stream."""

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
                # item.started and item.completed carry the same id; a set deduplicates.
                tool_item_ids.add(str(item_id) if item_id is not None else str(len(tool_item_ids)))
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
```

Add `from dataclasses import dataclass` to the imports. In `run()`, replace the inline loop with:

```python
        parsed = _parse_events(completed.stdout)
        return AgentResult(
            status=status,
            text=parsed.text or raw_transcript,
            usage=parsed.usage,
            transcript_path=transcript_path,
            tool_call_count=parsed.tool_call_count,
            ran_commands=parsed.ran_commands,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_codex.py -q`
Expected: PASS, with the live test deselected.

Run: `uv run pytest -q -m live tests/test_codex.py`
Expected: PASS — this spends roughly one trivial codex run. If it fails on argv, the message names the offending flag.

- [ ] **Step 5: Run the full check**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy orc`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add orc/adapters/codex.py tests/test_codex.py
git commit -m "fix: codex adapter sent mutually exclusive sandbox and approval flags

-s workspace-write cannot be combined with --approve-for-me; the CLI's
argument parser rejected the pair, so every codex run exited 2 before doing
any work. Mocked subprocess tests could not see it, so the parser is now
tested against real recorded output and the argv against the real CLI.

Also counts tool calls by distinct completed item id rather than by keyword
(item.started and item.completed share an id), captures turn.completed.usage
which was previously discarded, and passes stdin=DEVNULL."
```

---

### Task 3: D2 — claude adapter is blind to its own tool calls

The baseline uses `--output-format json`, which returns only a final envelope. So `tool_call_count` is always `None` and `ran_commands` always empty, which makes triage rule 1 fire every time: Claude is permanently classified "lazy" and burns every effort rung before the ladder escalates. It also passes `--safe-mode`, which disables the target repo's `CLAUDE.md` while `codex exec` reads `AGENTS.md` natively.

**Files:**
- Modify: `orc/adapters/claude_code.py`
- Test: `tests/test_claude_code.py` (create)

**Interfaces:**
- Consumes: `read_fixture`, `require_cli` from `tests/conftest.py`.
- Produces: `ClaudeCodeAdapter.run()` returning accurate `tool_call_count` and `ran_commands`. Module-level `_parse_stream(stdout: str) -> ParsedStream` with fields `text`, `ran_commands`, `tool_call_count`, `usage`, `is_error`, `rate_limit_info`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_claude_code.py`:

```python
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
        prompt="fix it",
        mode="agent",
        model="sonnet",
        effort="high",
        cwd=tmp_path,
        timeout_s=900,
        transcript_path=tmp_path / "t.txt",
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

    assert result.tool_call_count == 2  # one Bash, one Edit
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_claude_code.py -q`
Expected: FAIL — `tool_call_count` is `None`, `ran_commands` is `[]`, and `--safe-mode` is present in argv.

- [ ] **Step 3: Switch to stream-json and parse the events**

In `orc/adapters/claude_code.py`, replace the command list in `run()`:

```python
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
            "--verbose",  # required by the CLI for stream-json in print mode
            "--permission-mode",
            "acceptEdits",
            "--setting-sources",
            "project",  # repo CLAUDE.md in, the user's global settings and hooks out
        ]
```

Add `stdin=subprocess.DEVNULL` to `subprocess.run(...)`, then replace the JSON envelope parsing with:

```python
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
                rate_limit_info = info  # last one wins: the most recent reading
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
```

Then in `run()`, after writing the transcript:

```python
        parsed = _parse_stream(completed.stdout)
        status: AgentStatus = "ok" if completed.returncode == 0 else "fail"
        if parsed.is_error:
            status = "fail"
        if any(pattern in raw_transcript.casefold() for pattern in self._rate_limit_patterns):
            status = "rate_limited"
        return AgentResult(
            status=status,
            text=parsed.text or raw_transcript,
            usage=parsed.usage,
            transcript_path=transcript_path,
            tool_call_count=parsed.tool_call_count,
            ran_commands=parsed.ran_commands,
        )
```

Add `from dataclasses import dataclass` to the imports.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_claude_code.py -q`
Expected: PASS.

Run: `uv run pytest -q -m live tests/test_claude_code.py`
Expected: PASS — spends roughly one trivial Claude run.

- [ ] **Step 5: Run the full check**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy orc`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add orc/adapters/claude_code.py tests/test_claude_code.py
git commit -m "fix: claude adapter could not see its own tool calls

--output-format json returns only a final envelope, so tool_call_count was
always None and ran_commands always empty. Triage rule 1 therefore fired on
every Claude attempt, classifying it lazy and burning every effort rung
before the ladder could escalate — the opposite of quota-aware.

Switches to stream-json and counts tool_use blocks, extracting Bash commands
for triage. Also drops --safe-mode for --setting-sources project: safe-mode
hid the target repo's CLAUDE.md while codex read AGENTS.md natively, biasing
every cross-vendor comparison against Claude."
```

---

## Phase 2 — Git safety and correctness

These change what `orc` writes and what it believes an agent did. Both carry review gates.

### Task 4: D3 — `.orc/` makes every target repo dirty

`orc` writes `.orc/` into the target, then refuses to start the next run because `git status --porcelain` reports `?? .orc/`. Worse, a supervised agent running `git add -A` can stage orc's own artifacts into the user's branch — reproduced.

**Files:**
- Modify: `orc/gitops.py`
- Test: `tests/test_gitops.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `ensure_orc_excluded(target: Path) -> None`, `git_dir(target: Path) -> Path`. `ensure_safe_target` ignores `.orc/` when judging cleanliness.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gitops.py`:

```python
import subprocess
from pathlib import Path

import pytest

from orc.gitops import SafetyError, ensure_orc_excluded, ensure_safe_target, git_dir


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path


def test_second_run_starts_even_though_orc_wrote_artifacts(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    (tmp_path / ".orc" / "runs").mkdir(parents=True)
    (tmp_path / ".orc" / "runs" / "x.txt").write_text("artifact\n", encoding="utf-8")

    ensure_safe_target(tmp_path, "do a thing", allow_destructive=False)  # must not raise


def test_exclude_prevents_git_add_all_from_staging_orc(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    (tmp_path / ".orc").mkdir()
    (tmp_path / ".orc" / "ledger.json").write_text("{}\n", encoding="utf-8")

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert ".orc" not in staged


def test_exclude_is_idempotent_and_writes_nothing_else_in_git(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    ensure_orc_excluded(tmp_path)

    exclude = git_dir(tmp_path) / "info" / "exclude"
    assert exclude.read_text(encoding="utf-8").count(".orc/") == 1


def test_real_dirt_still_blocks_the_run(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")

    with pytest.raises(SafetyError, match="not clean"):
        ensure_safe_target(tmp_path, "do a thing", allow_destructive=False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_gitops.py -q`
Expected: FAIL with `ImportError: cannot import name 'ensure_orc_excluded'`.

- [ ] **Step 3: Implement**

Add to `orc/gitops.py`:

```python
_EXCLUDE_ENTRY = ".orc/"
_EXCLUDE_HEADER = "# added by orc: run artifacts, never committed"


def git_dir(target: Path) -> Path:
    """Resolve the repository's git directory, correct for worktrees and submodules."""
    resolved = _git(target, "rev-parse", "--absolute-git-dir")
    if resolved.returncode != 0:
        raise SafetyError(f"target is not a git repository: {target}")
    return Path(resolved.stdout.strip())


def ensure_orc_excluded(target: Path) -> None:
    """Exclude `.orc/` locally so it neither dirties the tree nor can be staged.

    `.git/info/exclude` is the only permitted write inside `.git` (SPEC §10). It is
    local-only and never committed, so the user's repository is untouched.
    """
    exclude_path = git_dir(target) / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.is_file() else ""
    if any(line.strip() == _EXCLUDE_ENTRY for line in existing.splitlines()):
        return
    separator = "" if existing.endswith("\n") or not existing else "\n"
    exclude_path.write_text(
        f"{existing}{separator}{_EXCLUDE_HEADER}\n{_EXCLUDE_ENTRY}\n", encoding="utf-8"
    )
```

Then in `ensure_safe_target`, after the `rev-parse` check and before the cleanliness check, call `ensure_orc_excluded(target)`, and filter the porcelain output:

```python
    ensure_orc_excluded(target)
    status = _git(target, "status", "--porcelain")
    if status.returncode != 0:
        raise SafetyError("could not inspect target git status")
    dirty = [
        line
        for line in status.stdout.splitlines()
        if line.strip() and not line[3:].lstrip('"').startswith(".orc/")
    ]
    if dirty:
        raise SafetyError("target git repository is not clean; commit or stash changes first")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_gitops.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full check**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy orc`

- [ ] **Step 6: Review gate — writes inside `.git`**

Run the reviewer command from the header. This diff writes into `.git`, so a P0 here is expensive. Fix P0/P1 before committing.

- [ ] **Step 7: Commit**

```bash
git add orc/gitops.py tests/test_gitops.py
git commit -m "fix: exclude .orc/ locally so a second run can start

Untracked .orc/ left every target repo dirty, so ensure_safe_target refused
the second run on any repo that had not already gitignored it. A supervised
agent running 'git add -A' could also stage orc's own artifacts into the
user's branch — reproduced in a scratch repo.

Appends .orc/ to .git/info/exclude, the single carve-out SPEC §10 permits:
local-only, never committed, and it leaves the user's tracked files alone."
```

---

### Task 5: D5 and D10 — orc misreads what the agent did

`git_diff` runs plain `git diff`, which shows only unstaged tracked changes. When an agent commits its work — codex routinely does — the diff is empty, so `files_edited` is `False`, triage misclassifies a real attempt as lazy, and the user is shown "(no working-tree diff)". Separately, `test_file_snapshot` walks every `*.py` under the target, including `.venv/` and `node_modules/`, and is Python-only.

**Files:**
- Modify: `orc/gitops.py`, `orc/router.py`, `orc/config.py`
- Test: `tests/test_gitops.py`

**Interfaces:**
- Consumes: `git_dir` (Task 4).
- Produces: `base_commit(target: Path) -> str`, `git_diff(target: Path, base: str) -> str`, `git_diff_stat(target: Path, base: str) -> str`, `test_file_snapshot(target: Path, patterns: list[str]) -> dict[str, str]`. `SafetyConfig.test_path_patterns: list[str]` in `orc/config.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gitops.py`:

```python
from orc.gitops import base_commit, git_diff, test_file_snapshot


def test_diff_sees_work_the_agent_committed(tmp_path: Path) -> None:
    _repo(tmp_path)
    base = base_commit(tmp_path)
    (tmp_path / "app.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "agent work"], cwd=tmp_path, check=True)

    assert "x = 2" in git_diff(tmp_path, base)


def test_diff_sees_files_the_agent_created(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    base = base_commit(tmp_path)
    (tmp_path / "new_module.py").write_text("y = 3\n", encoding="utf-8")

    diff = git_diff(tmp_path, base)
    assert "new_module.py" in diff
    assert "y = 3" in diff


def test_diff_never_includes_orc_artifacts(tmp_path: Path) -> None:
    _repo(tmp_path)
    ensure_orc_excluded(tmp_path)
    base = base_commit(tmp_path)
    (tmp_path / ".orc" / "runs").mkdir(parents=True)
    (tmp_path / ".orc" / "runs" / "x.txt").write_text("artifact\n", encoding="utf-8")

    assert ".orc" not in git_diff(tmp_path, base)


def test_snapshot_uses_the_index_not_a_filesystem_walk(tmp_path: Path) -> None:
    _repo(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_real.py").write_text("def test_x(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "add tests"], cwd=tmp_path, check=True)

    # An untracked virtualenv must not be walked, and must not appear in the snapshot.
    venv_tests = tmp_path / ".venv" / "lib" / "site-packages" / "pkg"
    venv_tests.mkdir(parents=True)
    (venv_tests / "test_vendored.py").write_text("def test_y(): pass\n", encoding="utf-8")

    snapshot = test_file_snapshot(tmp_path, ["tests/**", "test_*.py", "*_test.py"])
    assert set(snapshot) == {"tests/test_real.py"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_gitops.py -q`
Expected: FAIL with `ImportError: cannot import name 'base_commit'`.

- [ ] **Step 3: Implement in `orc/gitops.py`**

```python
import fnmatch


def base_commit(target: Path) -> str:
    """Record HEAD at branch creation so the whole run's work can be diffed later."""
    resolved = _git(target, "rev-parse", "HEAD")
    if resolved.returncode != 0:
        raise SafetyError("could not resolve HEAD; the target repository has no commits")
    return resolved.stdout.strip()


def git_diff(target: Path, base: str) -> str:
    """Diff the working tree against the run's base commit.

    `git diff` alone shows only unstaged tracked changes, so work an agent committed or
    staged is invisible. Intent-to-add makes new files visible without staging content;
    `.orc/` is excluded by Task 4, so artifacts never appear.
    """
    _git(target, "add", "--intent-to-add", "--all")
    diff = _git(target, "diff", "--no-ext-diff", base)
    if diff.returncode != 0:
        raise SafetyError(diff.stderr.strip() or "could not collect git diff")
    return diff.stdout


def git_diff_stat(target: Path, base: str) -> str:
    """Return the diff stat against the run's base commit."""
    _git(target, "add", "--intent-to-add", "--all")
    stat = _git(target, "diff", "--stat", base)
    return stat.stdout.strip() if stat.returncode == 0 else ""


def test_file_snapshot(target: Path, patterns: list[str]) -> dict[str, str]:
    """Hash tracked test files so a test-altering patch can be refused.

    Enumerated from the git index, never a filesystem walk: rglob descends into .venv/
    and node_modules/, which is both slow and a source of false positives.
    """
    listed = _git(target, "ls-files", "-z")
    if listed.returncode != 0:
        raise SafetyError("could not list tracked files")
    snapshot: dict[str, str] = {}
    for relative in listed.stdout.split("\0"):
        if not relative:
            continue
        if not any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
            continue
        path = target / relative
        if path.is_file():
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot
```

Note: `fnmatch` treats `tests/**` as matching any path beginning `tests/`, which is the intent. `assert_tests_unchanged(target, before, patterns)` gains the same `patterns` argument and keeps raising `SafetyError` — the hard abort stays, by decision.

- [ ] **Step 4: Add the config surface**

In `orc/config.py`:

```python
class SafetyConfig(BaseModel):
    test_path_patterns: list[str] = Field(
        default_factory=lambda: [
            "tests/**",
            "test/**",
            "spec/**",
            "test_*.py",
            "*_test.py",
            "*_test.go",
            "*.test.ts",
            "*.test.js",
            "*.spec.ts",
            "src/test/**",
        ]
    )
```

and add `safety: SafetyConfig = Field(default_factory=SafetyConfig)` to `OrcConfig`.

- [ ] **Step 5: Thread it through `orc/router.py`**

In `run_task`, capture `base = base_commit(target)` immediately after `create_branch`, pass `config.safety.test_path_patterns` to both `test_file_snapshot` and `assert_tests_unchanged`, and replace every `git_diff(target)` / `git_diff_stat(target)` call with the two-argument form. `_feedback` takes `base` as a parameter.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: PASS, all suites.

- [ ] **Step 7: Run the full check**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy orc`

- [ ] **Step 8: Review gate — data-safety change**

Run the reviewer command. This diff changes what orc believes an agent did and what it will refuse; a wrong answer either hides work or accepts a tampered patch.

- [ ] **Step 9: Commit**

```bash
git add orc/gitops.py orc/router.py orc/config.py tests/test_gitops.py
git commit -m "fix: diff against the run's base commit and enumerate tests from git

Plain 'git diff' shows only unstaged tracked changes, so when an agent
committed its work — codex routinely does — orc saw an empty diff, judged
files_edited False, misclassified a real attempt as lazy, and printed
'(no working-tree diff)' to the user.

Also enumerates test files from 'git ls-files' against configurable path
patterns instead of rglob('*.py'), which walked .venv/ and node_modules/ and
only ever understood Python. Test tampering still aborts the run."
```

---

### Task 6: D4 and dead config — agents get 120 seconds

`run_task` passes `config.verify.timeout_s` as the agent timeout. That budget is meant for a test suite; 120 seconds for an agentic coding run guarantees a timeout, and `agy`'s own default for a single headless prompt is 5 minutes. `ladder.retry_count` is also declared, validated, and never read.

**Files:**
- Modify: `orc/config.py`, `orc/router.py`, `orc.toml`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AgentsConfig.timeout_s: int = 900`, `LaneConfig.timeout_s: int | None = None`. `OrcConfig.agents: AgentsConfig`. `LadderConfig.retry_count` removed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_router.py`:

```python
def test_agent_timeout_is_independent_of_the_verify_timeout(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    fake = FakeAdapter()
    config = OrcConfig.model_validate(
        {
            "adapters": {"claude": {"command": "claude"}},
            "pools": {"claude": {"windows": ["weekly"], "budget_units": 5, "flat_run_estimate": 1}},
            "lanes": {"standard": {"candidates": ["claude:sonnet@high"]}},
            "ladder": {"effort_order": ["high", "xhigh"]},
            "agents": {"timeout_s": 900},
            "verify": {"timeout_s": 120},
        }
    )
    verify_result = VerificationResult(True, False, True, "ok", VerificationPlan([], [], []))

    run_task(
        "do it",
        tmp_path,
        config,
        fake,
        verify_runner=lambda _t, _p, _to: verify_result,
    )

    assert fake.requests[0].timeout_s == 900
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_router.py::test_agent_timeout_is_independent_of_the_verify_timeout -q`
Expected: FAIL — `assert 120 == 900`.

- [ ] **Step 3: Implement**

In `orc/config.py`:

```python
class AgentsConfig(BaseModel):
    timeout_s: int = Field(default=900, gt=0)
```

Add `agents: AgentsConfig = Field(default_factory=AgentsConfig)` to `OrcConfig`, add `timeout_s: int | None = None` to `LaneConfig`, and delete `retry_count` from `LadderConfig`.

In `orc/router.py`, build the request with the agent budget, preferring a lane override:

```python
        lane_timeout = config.lanes[rung_name].timeout_s if rung_name in config.lanes else None
        agent_timeout = lane_timeout or config.agents.timeout_s
```

and pass `timeout_s=agent_timeout` to `AgentRequest`. The verifier keeps `config.verify.timeout_s`.

- [ ] **Step 4: Update `orc.toml`**

```toml
[agents]
# A coding run is not a test run. agy's own default for one headless prompt is 5m.
timeout_s = 900
```

and delete `retry_count` from `[ladder]`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy orc`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add orc/config.py orc/router.py orc.toml tests/test_router.py
git commit -m "fix: give agents their own timeout budget

run_task passed [verify].timeout_s (120s) as the agent timeout. That budget
sizes a test suite, not an agentic coding run; agy's own default for a single
headless prompt is 5 minutes. Adds [agents].timeout_s (default 900) with an
optional per-lane override, and drops the never-read ladder.retry_count."
```

---

## Phase 3 — Quota telemetry

The subsystem M2 exists for. Each vendor reports utilization differently; the adapter normalizes, the ledger applies policy, the router acts on it.

### Task 7: The `QuotaObservation` contract

**Files:**
- Modify: `orc/adapters/base.py`
- Test: `tests/test_quota_contract.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `QuotaWindow(kind, used_fraction, resets_at)`, `QuotaObservation(windows, observed_at, source)`, `window_kind_from_minutes(minutes: float) -> WindowKind`, `AgentResult.quota: QuotaObservation | None = None`, `AgentAdapter.quota_probe() -> QuotaObservation | None` defaulting to `None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quota_contract.py`:

```python
from datetime import UTC, datetime

from orc.adapters.base import (
    AgentAdapter,
    AgentRequest,
    AgentResult,
    QuotaObservation,
    QuotaWindow,
    window_kind_from_minutes,
)


def test_window_kind_is_derived_from_the_vendors_window_length() -> None:
    # codex reports raw minutes: 300 for its short window, 10080 for its weekly one.
    assert window_kind_from_minutes(300) == "5h"
    assert window_kind_from_minutes(10080) == "weekly"
    assert window_kind_from_minutes(44640) == "monthly"


def test_agent_result_defaults_quota_to_none_so_old_call_sites_still_build() -> None:
    result = AgentResult("ok", "done", None, __import__("pathlib").Path("/tmp/t"), 1, [])
    assert result.quota is None


def test_an_adapter_without_telemetry_reports_none() -> None:
    class Bare(AgentAdapter):
        name = "bare"

        def available(self) -> bool:
            return True

        def run(self, req: AgentRequest) -> AgentResult:  # pragma: no cover - unused
            raise NotImplementedError

    assert Bare().quota_probe() is None


def test_observation_holds_several_simultaneous_windows() -> None:
    now = datetime.now(UTC)
    observation = QuotaObservation(
        windows=[
            QuotaWindow("5h", 0.39, now),
            QuotaWindow("weekly", 0.38, now),
        ],
        observed_at=now,
        source="session-file",
    )
    assert {window.kind for window in observation.windows} == {"5h", "weekly"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_quota_contract.py -q`
Expected: FAIL with `ImportError: cannot import name 'QuotaWindow'`.

- [ ] **Step 3: Implement in `orc/adapters/base.py`**

```python
from datetime import datetime

WindowKind = Literal["5h", "weekly", "monthly"]
QuotaSource = Literal["stream", "session-file", "command"]

_MINUTES_PER_DAY = 60 * 24


def window_kind_from_minutes(minutes: float) -> WindowKind:
    """Classify a vendor-reported window length. Codex reports 300 and 10080."""
    if minutes <= _MINUTES_PER_DAY:
        return "5h"
    if minutes <= _MINUTES_PER_DAY * 14:
        return "weekly"
    return "monthly"


@dataclass(slots=True)
class QuotaWindow:
    """One rate-limit window, normalized to fraction *used* regardless of vendor polarity."""

    kind: WindowKind
    used_fraction: float
    resets_at: datetime


@dataclass(slots=True)
class QuotaObservation:
    """A reading of one pool's utilization. A pool may have several simultaneous windows."""

    windows: list[QuotaWindow]
    observed_at: datetime
    source: QuotaSource
```

Add `quota: QuotaObservation | None = None` as the last field of `AgentResult` — defaulted so existing positional constructions keep working — and add to `AgentAdapter`:

```python
    def quota_probe(self) -> QuotaObservation | None:
        """Return current utilization without spending a run, when the CLI allows it.

        Adapters without a telemetry mechanism return None and the ledger estimates.
        """
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy orc`

- [ ] **Step 5: Commit**

```bash
git add orc/adapters/base.py tests/test_quota_contract.py
git commit -m "feat: add the QuotaObservation adapter contract

Adapters own their vendor's quota mechanism and normalize it; ledger.py
consumes only this shape. used_fraction is explicit because the three
vendors disagree on polarity: claude reports utilization, codex reports
used_percent, agy reports remaining_fraction."
```

---

### Task 8: Claude and Codex emit quota

Claude's reading arrives mid-run in the event stream. Codex's lives in the session rollout file its own stream points at, so it is also readable *before* a run.

**Files:**
- Modify: `orc/adapters/claude_code.py`, `orc/adapters/codex.py`
- Test: `tests/test_claude_code.py`, `tests/test_codex.py`

**Interfaces:**
- Consumes: `QuotaObservation`, `QuotaWindow`, `window_kind_from_minutes` (Task 7); `_parse_stream` (Task 3); `_parse_events` (Task 2).
- Produces: `AgentResult.quota` populated by both adapters; `CodexAdapter.quota_probe()` reading the newest rollout.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_code.py`:

```python
def test_claude_reports_quota_from_the_event_stream(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter(_config())
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            ["claude"], 0, stdout=read_fixture("claude-stream-json.jsonl"), stderr=""
        )
    )
    with patch("subprocess.run", mock_run):
        result = adapter.run(_request(tmp_path))

    assert result.quota is not None
    assert result.quota.source == "stream"
    weekly = [w for w in result.quota.windows if w.kind == "weekly"]
    assert len(weekly) == 1
    assert weekly[0].used_fraction == pytest.approx(0.59)
    assert weekly[0].resets_at.timestamp() == 1789214400
```

Append to `tests/test_codex.py`:

```python
import os
from datetime import UTC


def _write_rollout(codex_home: Path, thread_id: str, used_5h: float, used_week: float) -> Path:
    session_dir = codex_home / "sessions" / "2026" / "09" / "07"
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / f"rollout-2026-09-07T13-53-18-{thread_id}.jsonl"
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": thread_id}})
        + "\n"
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_codex.py tests/test_claude_code.py -q`
Expected: FAIL — `result.quota is None`; `quota_probe()` returns `None` for the populated rollout.

- [ ] **Step 3: Implement Claude's mapping**

In `orc/adapters/claude_code.py`:

```python
# Claude names its windows; codex reports minutes. An unrecognized name is skipped
# rather than guessed — a mislabeled window would block the wrong lane.
_CLAUDE_WINDOW_KINDS: dict[str, WindowKind] = {
    "five_hour": "5h",
    "seven_day": "weekly",
    "monthly": "monthly",
}


def _observation_from_rate_limit(info: dict[str, object]) -> QuotaObservation | None:
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
```

Pass `quota=_observation_from_rate_limit(parsed.rate_limit_info) if parsed.rate_limit_info else None` when constructing the `AgentResult`.

- [ ] **Step 4: Implement Codex's rollout reader**

In `orc/adapters/codex.py`:

```python
def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def _newest_rollout(sessions_root: Path, thread_id: str | None = None) -> Path | None:
    if not sessions_root.is_dir():
        return None
    pattern = f"**/rollout-*{thread_id}.jsonl" if thread_id else "**/rollout-*.jsonl"
    candidates = [path for path in sessions_root.glob(pattern) if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _observation_from_rollout(path: Path) -> QuotaObservation | None:
    """Read the last rate_limits record. Codex writes one per turn; the last is current."""
    latest: dict[str, object] | None = None
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if '"rate_limits"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            found = _find_rate_limits(record)
            if found is not None:
                latest = found
    except OSError:
        return None
    if latest is None:
        return None

    windows: list[QuotaWindow] = []
    for key in ("primary", "secondary"):
        bucket = latest.get(key)
        if not isinstance(bucket, dict):
            continue
        used = bucket.get("used_percent")
        minutes = bucket.get("window_minutes")
        resets = bucket.get("resets_at")
        if not isinstance(used, int | float) or not isinstance(minutes, int | float):
            continue
        if not isinstance(resets, int | float):
            continue
        windows.append(
            QuotaWindow(
                window_kind_from_minutes(float(minutes)),
                float(used) / 100.0,
                datetime.fromtimestamp(float(resets), UTC),
            )
        )
    if not windows:
        return None
    return QuotaObservation(windows, datetime.now(UTC), "session-file")


def _find_rate_limits(node: object) -> dict[str, object] | None:
    """rate_limits is nested inside a per-turn record whose shape varies by version."""
    if isinstance(node, dict):
        found = node.get("rate_limits")
        if isinstance(found, dict):
            return found
        for value in node.values():
            nested = _find_rate_limits(value)
            if nested is not None:
                return nested
    elif isinstance(node, list):
        for value in node:
            nested = _find_rate_limits(value)
            if nested is not None:
                return nested
    return None
```

Add the method to `CodexAdapter`:

```python
    def quota_probe(self) -> QuotaObservation | None:
        """Read utilization from the newest session rollout without spending a run."""
        rollout = _newest_rollout(_codex_home() / "sessions")
        return _observation_from_rollout(rollout) if rollout else None
```

and in `run()`, after parsing, prefer the run's own thread:

```python
        quota = None
        rollout = _newest_rollout(_codex_home() / "sessions", parsed.thread_id)
        if rollout is not None:
            quota = _observation_from_rollout(rollout)
```

passing `quota=quota` into the `AgentResult`.

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy orc`

- [ ] **Step 6: Commit**

```bash
git add orc/adapters/ tests/test_codex.py tests/test_claude_code.py
git commit -m "feat: claude and codex report real quota utilization

SPEC §7's original premise — that no product exposes usage — no longer
holds. Claude emits rate_limit_event mid-stream; codex records rate_limits
in the session rollout its own thread_id names, which makes it readable
before a run as well as after. Both normalize to QuotaObservation."
```

---

### Task 9: D7 — the ledger learns to read telemetry and reserve headroom

The baseline never resets a window: `window_started` is written and never read, so `spent_units` accrues forever and a pool is permanently "exhausted" after `budget_units` runs. This task replaces guessing with reading, and adds the reserve that stops an unattended ladder from spending the owner out of their own CLI.

**Files:**
- Modify: `orc/ledger.py`, `orc/config.py`, `orc.toml`
- Test: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `QuotaObservation`, `QuotaWindow` (Task 7).
- Produces: `PoolConfig.windows: list[WindowKind]`, `PoolConfig.reserve_fraction: float = 0.15`; `Eligibility(ok, reason, blocked_window, resets_at, source)`; `Ledger.record_observation(pool_id, observation)`, `Ledger.eligibility(pool_id, pool, use_reserve=False, now=None) -> Eligibility`. `PoolStatus` gains `windows` and `source`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger.py`:

```python
from datetime import UTC, datetime, timedelta

from orc.adapters.base import QuotaObservation, QuotaWindow
from orc.ledger import Ledger


def _telemetry_pool(reserve: float = 0.15) -> PoolConfig:
    return PoolConfig.model_validate(
        {
            "windows": ["5h", "weekly"],
            "budget_units": 20,
            "flat_run_estimate": 1,
            "reserve_fraction": reserve,
        }
    )


def _observation(used_5h: float, used_week: float) -> QuotaObservation:
    later = datetime.now(UTC) + timedelta(hours=3)
    return QuotaObservation(
        windows=[QuotaWindow("5h", used_5h, later), QuotaWindow("weekly", used_week, later)],
        observed_at=datetime.now(UTC),
        source="session-file",
    )


def test_a_pool_below_the_reserve_line_is_eligible(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    pool = _telemetry_pool()
    ledger.record_observation("codex", _observation(0.39, 0.38))

    assert ledger.eligibility("codex", pool).ok


def test_any_window_over_the_reserve_line_blocks_the_pool(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    pool = _telemetry_pool()
    # Weekly is healthy; the 5h window is not. One bad window is enough.
    ledger.record_observation("codex", _observation(0.90, 0.20))

    verdict = ledger.eligibility("codex", pool)
    assert not verdict.ok
    assert verdict.reason == "reserve"
    assert verdict.blocked_window == "5h"
    assert verdict.resets_at is not None


def test_use_reserve_spends_into_the_headroom_but_not_past_full(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    pool = _telemetry_pool()
    ledger.record_observation("codex", _observation(0.90, 0.20))
    assert ledger.eligibility("codex", pool, use_reserve=True).ok

    ledger.record_observation("codex", _observation(1.0, 0.20))
    assert not ledger.eligibility("codex", pool, use_reserve=True).ok


def test_a_stale_window_no_longer_blocks_once_it_has_reset(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    pool = _telemetry_pool()
    past = datetime.now(UTC) - timedelta(hours=1)
    ledger.record_observation(
        "codex",
        QuotaObservation([QuotaWindow("5h", 0.99, past)], datetime.now(UTC), "session-file"),
    )

    assert ledger.eligibility("codex", pool).ok


def test_without_telemetry_the_estimate_still_applies(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    pool = PoolConfig.model_validate(
        {"windows": ["weekly"], "budget_units": 2, "flat_run_estimate": 1}
    )
    ledger.record_run("copilot", pool)
    assert ledger.eligibility("copilot", pool).ok
    ledger.record_run("copilot", pool)

    verdict = ledger.eligibility("copilot", pool)
    assert not verdict.ok
    assert verdict.reason == "budget"


def test_estimated_spend_resets_when_the_window_rolls_over(tmp_path: Path) -> None:
    """D7: window_started was written and never read, so spend accrued forever."""
    ledger = Ledger(tmp_path / "ledger.json")
    pool = PoolConfig.model_validate({"windows": ["5h"], "budget_units": 2, "flat_run_estimate": 1})
    ledger.record_run("copilot", pool)
    ledger.record_run("copilot", pool)
    assert not ledger.eligibility("copilot", pool).ok

    later = datetime.now(UTC) + timedelta(hours=6)
    assert ledger.eligibility("copilot", pool, now=later).ok
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ledger.py -q`
Expected: FAIL — `PoolConfig` rejects `windows`; `record_observation` and `eligibility` do not exist.

- [ ] **Step 3: Change the config schema**

In `orc/config.py`, replace `PoolConfig`:

```python
class PoolConfig(BaseModel):
    # A pool has several simultaneous windows: codex reports a 5h and a weekly one.
    windows: list[Literal["5h", "weekly", "monthly"]] = Field(min_length=1)
    budget_units: float  # fallback only, used when no telemetry is available
    flat_run_estimate: float  # fallback only
    reserve_fraction: float = Field(default=0.15, ge=0.0, lt=1.0)
    quota_group: str | None = None  # vendor-side group name, when one CLI serves two pools
```

- [ ] **Step 4: Implement the ledger**

In `orc/ledger.py`:

```python
_WINDOW_SECONDS = {"5h": 5 * 3600, "weekly": 7 * 86400, "monthly": 30 * 86400}


@dataclass(slots=True)
class Eligibility:
    """Why a pool may or may not be spent right now."""

    ok: bool
    reason: str  # "", "reserve", "budget", or "rate_limited"
    blocked_window: str | None = None
    resets_at: datetime | None = None
    source: str = "estimated"


class Ledger:
    def record_observation(self, pool_id: str, observation: QuotaObservation) -> None:
        """Store a normalized reading. Telemetry supersedes the estimate for this pool."""
        state = self._load()
        entry = state.setdefault("pools", {}).setdefault(pool_id, {})
        entry["observed_at"] = observation.observed_at.isoformat()
        entry["source"] = observation.source
        entry["windows"] = [
            {
                "kind": window.kind,
                "used_fraction": window.used_fraction,
                "resets_at": window.resets_at.isoformat(),
            }
            for window in observation.windows
        ]
        self._save(state)

    def eligibility(
        self,
        pool_id: str,
        pool: PoolConfig,
        use_reserve: bool = False,
        now: datetime | None = None,
    ) -> Eligibility:
        """Decide before spending a run. Telemetry is authoritative; estimates are fallback."""
        current = now or datetime.now(UTC)
        entry = self._load().get("pools", {}).get(pool_id) or {}

        exhausted_until = entry.get("exhausted_until")
        if isinstance(exhausted_until, str):
            try:
                boundary = datetime.fromisoformat(exhausted_until)
            except ValueError:
                boundary = None
            if boundary is not None and boundary > current:
                return Eligibility(False, "rate_limited", None, boundary, "observed")

        windows = entry.get("windows")
        if isinstance(windows, list) and windows:
            # `--use-reserve` spends the headroom, never past a real vendor limit.
            ceiling = 1.0 if use_reserve else 1.0 - pool.reserve_fraction
            for window in windows:
                if not isinstance(window, dict):
                    continue
                try:
                    resets_at = datetime.fromisoformat(str(window.get("resets_at")))
                except ValueError:
                    continue
                if resets_at <= current:
                    continue  # this window has already rolled over; the reading is stale
                used = float(window.get("used_fraction", 0.0))
                if used >= ceiling:
                    return Eligibility(
                        False,
                        "reserve",
                        str(window.get("kind")),
                        resets_at,
                        str(entry.get("source", "observed")),
                    )
            return Eligibility(True, "", None, None, str(entry.get("source", "observed")))

        spent = self._spent_after_window_reset(entry, pool, current)
        if spent >= pool.budget_units:
            return Eligibility(False, "budget", pool.windows[0], None, "estimated")
        return Eligibility(True, "", None, None, "estimated")

    def _spent_after_window_reset(
        self, entry: dict[str, Any], pool: PoolConfig, current: datetime
    ) -> float:
        """Zero the estimate once the shortest configured window has rolled over."""
        started_raw = entry.get("window_started")
        if isinstance(started_raw, str):
            try:
                started = datetime.fromisoformat(started_raw)
            except ValueError:
                return float(entry.get("spent_units", 0.0))
            shortest = min(_WINDOW_SECONDS[window] for window in pool.windows)
            if (current - started).total_seconds() >= shortest:
                return 0.0
        return float(entry.get("spent_units", 0.0))
```

`record_run` additionally resets `spent_units` to `0.0` and stamps a fresh `window_started` when `_spent_after_window_reset` returns `0.0`. `status()` returns `PoolStatus` with the extra `windows: list[dict]` and `source: str` fields so the report can label numbers honestly.

- [ ] **Step 5: Update `orc.toml`**

```toml
[pools.claude]
windows = ["5h", "weekly"]
budget_units = 20
flat_run_estimate = 1
reserve_fraction = 0.15

[pools.codex]
windows = ["5h", "weekly"]
budget_units = 30
flat_run_estimate = 1
reserve_fraction = 0.15
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy orc`
Expected: all pass. Existing `test_ledger.py` and `test_router.py` fixtures need `window` changed to `windows: [...]`.

- [ ] **Step 7: Review gate — quota accounting**

Run the reviewer command. Every later routing decision trusts this arithmetic without re-deriving it, which is exactly the blast radius `AGENTS.md` names.

- [ ] **Step 8: Commit**

```bash
git add orc/ledger.py orc/config.py orc.toml tests/test_ledger.py tests/test_router.py
git commit -m "feat: telemetry-driven eligibility with a reserve, and window resets

A pool now holds several simultaneous windows and is ineligible once any of
them reaches 1 - reserve_fraction, until that window's own reset time.
--use-reserve spends the headroom but never past a real vendor limit.

Fixes D7: window_started was written and never read, so estimated spend
accrued forever and a pool went permanently exhausted after budget_units
runs. Estimates remain the fallback for pools without telemetry."
```

---

### Task 10: Router — spend nothing on a blocked lane, degrade instead of failing

**Files:**
- Modify: `orc/router.py`
- Test: `tests/test_router.py`

**Interfaces:**
- Consumes: `Eligibility`, `Ledger.eligibility`, `Ledger.record_observation` (Task 9); `base_commit`, `git_diff` (Task 5).
- Produces: `Attempt` gains `triage: str | None` and `wall_s: float`; `TaskRun` gains `blocked: list[Eligibility]`; `run_task(..., use_reserve: bool = False)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_router.py`:

```python
def _two_vendor_config(**overrides: object) -> OrcConfig:
    base: dict[str, object] = {
        "adapters": {
            "claude": {"command": "claude", "pool": "claude"},
            "codex": {"command": "codex", "pool": "codex"},
            "antigravity": {"command": "agy", "pool": "antigravity_gemini"},
        },
        "pools": {
            "claude": {"windows": ["weekly"], "budget_units": 10, "flat_run_estimate": 1},
            "codex": {"windows": ["weekly"], "budget_units": 10, "flat_run_estimate": 1},
            "antigravity_gemini": {
                "windows": ["weekly"],
                "budget_units": 50,
                "flat_run_estimate": 1,
            },
        },
        "lanes": {
            "standard": {"candidates": ["claude:sonnet@high"]},
            "quality": {"candidates": ["codex:gpt-5.6-sol@high"]},
            "volume": {"candidates": ["antigravity:gemini-3.8-flash-medium@medium"]},
        },
        "ladder": {
            "order": ["standard", "quality"],
            "fallback": "volume",
            "effort_order": ["high", "xhigh"],
            "max_total_attempts": 4,
        },
    }
    base.update(overrides)
    return OrcConfig.model_validate(base)


def test_a_reserve_blocked_pool_is_skipped_before_a_run_is_spent(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    config = _two_vendor_config()
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")
    later = datetime.now(UTC) + timedelta(days=1)
    ledger.record_observation(
        "claude",
        QuotaObservation([QuotaWindow("weekly", 0.92, later)], datetime.now(UTC), "stream"),
    )

    claude_adapter = ReroutingFakeAdapter("claude")
    codex_adapter = ReroutingFakeAdapter("codex")
    verify_ok = VerificationResult(True, False, True, "ok", VerificationPlan([], [], []))

    run = run_task(
        "do it",
        tmp_path,
        config,
        adapters={"claude": claude_adapter, "codex": codex_adapter},
        verify_runner=lambda _t, _p, _to: verify_ok,
    )

    assert run.verified
    assert claude_adapter.requests == []  # never spent
    assert len(codex_adapter.requests) == 1


def test_every_rung_blocked_degrades_to_the_volume_lane(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    config = _two_vendor_config()
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")
    later = datetime.now(UTC) + timedelta(days=1)
    for pool in ("claude", "codex"):
        ledger.record_observation(
            pool,
            QuotaObservation([QuotaWindow("weekly", 0.95, later)], datetime.now(UTC), "stream"),
        )

    agy_adapter = ReroutingFakeAdapter("antigravity")
    verify_ok = VerificationResult(True, False, True, "ok", VerificationPlan([], [], []))

    run = run_task(
        "do it",
        tmp_path,
        config,
        adapters={
            "claude": ReroutingFakeAdapter("claude"),
            "codex": ReroutingFakeAdapter("codex"),
            "antigravity": agy_adapter,
        },
        verify_runner=lambda _t, _p, _to: verify_ok,
    )

    assert len(agy_adapter.requests) == 1
    assert run.verified


def test_use_reserve_spends_a_blocked_pool(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    config = _two_vendor_config()
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")
    later = datetime.now(UTC) + timedelta(days=1)
    ledger.record_observation(
        "claude",
        QuotaObservation([QuotaWindow("weekly", 0.92, later)], datetime.now(UTC), "stream"),
    )
    claude_adapter = ReroutingFakeAdapter("claude")
    verify_ok = VerificationResult(True, False, True, "ok", VerificationPlan([], [], []))

    run_task(
        "do it",
        tmp_path,
        config,
        adapters={"claude": claude_adapter, "codex": ReroutingFakeAdapter("codex")},
        verify_runner=lambda _t, _p, _to: verify_ok,
        use_reserve=True,
    )

    assert len(claude_adapter.requests) == 1


def test_the_run_records_why_it_escalated(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    config = _two_vendor_config()
    plan = VerificationPlan(["pytest -q"], [], [])
    results = iter(
        [
            VerificationResult(False, False, True, "failed", plan),
            VerificationResult(True, False, True, "passed", plan),
        ]
    )
    run = run_task(
        "do it",
        tmp_path,
        config,
        adapters={"claude": ReroutingFakeAdapter("claude"), "codex": ReroutingFakeAdapter("codex")},
        verify_runner=lambda _t, _p, _to: next(results),
    )

    assert run.attempts[0].triage in {"lazy", "dumb"}
    assert run.attempts[0].wall_s >= 0.0
    assert run.attempts[-1].triage is None  # the successful attempt was not triaged
```

Add the imports these need at the top of `tests/test_router.py`: `from datetime import UTC, datetime, timedelta`, `from orc.adapters.base import QuotaObservation, QuotaWindow`, `from orc.ledger import Ledger`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_router.py -q`
Expected: FAIL — claude is spent despite being over its reserve; no `fallback` handling; `Attempt` has no `triage`.

- [ ] **Step 3: Implement**

In `orc/router.py`:

```python
@dataclass(slots=True)
class Attempt:
    number: int
    effort: str
    result: AgentResult
    verification: VerificationResult
    candidate: str = ""
    lane: str = ""
    triage: str | None = None
    wall_s: float = 0.0
```

Build the candidate list as `(lane_name, candidate)` pairs so the report and log can name the lane, appending the fallback lane last but marking it:

```python
def _candidate_ladder(config: OrcConfig) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (ladder, fallback). The fallback is entered only when the ladder is blocked."""
    ladder = [
        (lane, candidate)
        for lane in config.ladder.order
        if lane in config.lanes
        for candidate in config.lanes[lane].candidates
    ]
    fallback_lane = config.ladder.fallback
    fallback = [
        (fallback_lane, candidate)
        for candidate in (
            config.lanes[fallback_lane].candidates if fallback_lane in config.lanes else []
        )
    ]
    return ladder, fallback
```

Before running each candidate, consult the ledger and record the refusal:

```python
        verdict = ledger.eligibility(pool_id, pool_cfg, use_reserve=use_reserve)
        if not verdict.ok:
            blocked.append((candidate_str, verdict))
            candidate_idx += 1
            current_effort = None
            candidate_attempts = 0
            continue
```

After the ladder loop, if `not attempts` and every candidate was blocked, retry the same loop over `fallback` — the `volume` lane is entered only here, never as a rung. After each `ad.run(...)`, record telemetry and timing:

```python
        started = time.monotonic()
        result = ad.run(...)
        elapsed = time.monotonic() - started
        if result.quota is not None:
            ledger.record_observation(pool_id, result.quota)
        elif pool_cfg:
            ledger.record_run(pool_id, pool_cfg)
```

Note the `elif`: a run with real telemetry must not also be estimated, or the pool is double-counted. Store the triage verdict on the `Attempt` it explains, and add `blocked` to `TaskRun` so the report can say which pools were held back and when they reset.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy orc`

- [ ] **Step 5: Review gate — routing and spend decisions**

Run the reviewer command. Ask specifically whether any path can spend a pool the ledger judged ineligible, and whether the fallback can be entered while a ladder rung is still viable.

- [ ] **Step 6: Commit**

```bash
git add orc/router.py tests/test_router.py
git commit -m "feat: filter lanes by quota before spending, degrade instead of failing

Eligibility is now checked before a run, so a pool past its reserve costs
nothing to skip — previously orc learned a pool was exhausted by spending an
attempt on it and reading the error. When every ladder rung is blocked, the
run falls through to the volume lane rather than stopping.

Attempts record their triage verdict and wall time so the report can explain
an escalation and log.jsonl can feed v2 routing."
```

---

## Phase 4 — Third vendor

### Task 11: The Antigravity (`agy`) adapter

Verified against `agy` 1.1.27. Three properties differ from the other two CLIs and shape the implementation: there is **no working-directory flag** (only `--add-dir`), the permission model is `--mode accept-edits`, and **the reasoning tier is carried by the model slug** — `agy models` lists `gemini-3.8-flash-high` and no bare `gemini-3.8-flash`.

**Files:**
- Create: `orc/adapters/antigravity.py`
- Create: `tests/test_antigravity.py`
- Create: `tests/fixtures/agy-stream-json.jsonl`

**Interfaces:**
- Consumes: `AgentAdapter`, `AgentRequest`, `AgentResult` (Task 7); `read_fixture`, `require_cli` (Task 1).
- Produces: `AntigravityAdapter(name="antigravity")` with `available()`, `run()`, and (Task 12) `quota_probe()`. `AdapterConfig.supported_efforts: list[str]`.

- [ ] **Step 1: Capture the real fixture**

The parser must be written against real output, never invented JSON. Run:

```bash
mkdir -p /tmp/agy-probe && cd /tmp/agy-probe && git init -q .
printf 'x = 1\n' > a.py
agy -p "Run the shell command 'echo hello-from-agy', then stop." \
  --model gemini-3.8-flash-low --output-format stream-json --mode accept-edits \
  --print-timeout 120s < /dev/null > events.jsonl 2>err.txt
cat events.jsonl
```

Copy the resulting NDJSON into `tests/fixtures/agy-stream-json.jsonl`. **Write the assertions in Step 2 against what you actually captured** — the event names below (`init`, `step_update`, `result`) come from the vendor docs and must be reconciled with the real stream before the parser is written.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_antigravity.py`, adjusting the field names to the captured fixture:

```python
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from orc.adapters.antigravity import AntigravityAdapter
from orc.adapters.base import AgentRequest
from orc.config import AdapterConfig
from tests.conftest import read_fixture, require_cli


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
    adapter = AntigravityAdapter(_config())
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            ["agy"], 0, stdout=read_fixture("agy-stream-json.jsonl"), stderr=""
        )
    )
    with patch("subprocess.run", mock_run):
        result = adapter.run(_request(tmp_path))

    assert result.status == "ok"
    assert result.tool_call_count is not None and result.tool_call_count >= 1
    assert any("hello-from-agy" in command for command in result.ran_commands)


@pytest.mark.live
def test_agy_argv_is_accepted_by_the_real_cli(tmp_path: Path) -> None:
    require_cli("agy")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    adapter = AntigravityAdapter(_config())
    request = _request(tmp_path, effort="low")
    request.prompt = "Reply with exactly: OK"
    result = adapter.run(request)

    output = request.transcript_path.read_text(encoding="utf-8")
    assert "flag provided but not defined" not in output, output[:500]
    assert "unknown model" not in output.casefold(), output[:500]
    assert result.status in {"ok", "rate_limited"}
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_antigravity.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'orc.adapters.antigravity'`.

- [ ] **Step 4: Add `supported_efforts` to `AdapterConfig`**

In `orc/config.py`, add to `AdapterConfig`:

```python
    supported_efforts: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: Implement the adapter**

Create `orc/adapters/antigravity.py`:

```python
"""Antigravity `agy` adapter, discovered against agy 1.1.27 on 2026-09-07."""

from __future__ import annotations

import shutil
import subprocess

from orc.adapters.base import AgentAdapter, AgentRequest, AgentResult, AgentStatus
from orc.config import AdapterConfig


class AntigravityAdapter(AgentAdapter):
    """Run Antigravity's CLI non-interactively."""

    name = "antigravity"

    def __init__(self, config: AdapterConfig) -> None:
        self._command = config.command
        self._supported_efforts = list(config.supported_efforts)
        self._rate_limit_patterns = tuple(p.casefold() for p in config.rate_limit_patterns)

    def available(self) -> bool:
        """`agy models` fails fast when the CLI is unauthenticated."""
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
        """agy carries the reasoning tier in the slug: `gemini-3.8-flash-high`.

        The ladder may bump past what this vendor offers, so clamp rather than fail.
        """
        if not self._supported_efforts:
            return model
        chosen = effort if effort in self._supported_efforts else self._supported_efforts[-1]
        return f"{model}-{chosen}"

    def run(self, req: AgentRequest) -> AgentResult:
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
            "--add-dir",  # agy has no -C; the workspace root comes from cwd plus this
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

        raw = completed.stdout + (
            f"\n--- stderr ---\n{completed.stderr}" if completed.stderr else ""
        )
        transcript_path.write_text(raw, encoding="utf-8")
        status: AgentStatus = "ok" if completed.returncode == 0 else "fail"
        if any(pattern in raw.casefold() for pattern in self._rate_limit_patterns):
            status = "rate_limited"

        parsed = _parse_stream(completed.stdout)
        return AgentResult(
            status=status,
            text=parsed.text or raw,
            usage=parsed.usage,
            transcript_path=transcript_path,
            tool_call_count=parsed.tool_call_count,
            ran_commands=parsed.ran_commands,
        )
```

Write `_parse_stream` and `_as_text` to match the fixture captured in Step 1, following the shape of `codex.py::_parse_events`: count distinct completed tool items, collect shell command strings, join agent text, and read the usage envelope from the final `result` event.

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_antigravity.py -q`
Expected: PASS.

Run: `uv run pytest -q -m live tests/test_antigravity.py`
Expected: PASS.

- [ ] **Step 7: Review gate — a new vendor contract**

Run the reviewer command from the header, using `agy` as the reviewer since the diff concerns Antigravity's own CLI:

```bash
git diff main...HEAD > /tmp/orc-review-diff.txt
agy -p "Review /tmp/orc-review-diff.txt, an adapter wrapping the agy CLI. Report
JSON findings [{severity,file,line,issue,why,suggestion}]. Focus on whether the argv
and the stream parsing match how agy actually behaves." \
  --model gemini-3.8-flash-high --output-format text --print-timeout 180s < /dev/null
```

- [ ] **Step 8: Full check and commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy orc
git add orc/adapters/antigravity.py tests/test_antigravity.py tests/fixtures/agy-stream-json.jsonl
git commit -m "feat: add the Antigravity (agy) adapter

Verified against agy 1.1.27. Three properties differ from the other CLIs:
there is no working-directory flag, so cwd comes from the subprocess plus
--add-dir; permissions are --mode accept-edits; and the reasoning tier lives
in the model slug rather than --effort, so a candidate's @effort composes
into the slug and clamps when the ladder bumps past what agy offers."
```

---

### Task 12: `agy` quota probe and three-vendor configuration

`agy -p "/usage" --output-format json` returns structured per-group buckets for **zero turns and zero tokens** — the only free pre-flight probe of the three vendors. It returns two groups, which is why `antigravity_gemini` and `antigravity_claude` are separate pools.

**Files:**
- Modify: `orc/adapters/antigravity.py`, `orc.toml`, `FLEET.md`
- Test: `tests/test_antigravity.py`

**Interfaces:**
- Consumes: `QuotaObservation`, `QuotaWindow` (Task 7); `PoolConfig.quota_group` (Task 9).
- Produces: `AntigravityAdapter.quota_probe()`; `AntigravityAdapter(config, quota_group: str | None)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_antigravity.py`:

```python
import json

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


def test_quota_probe_selects_its_group_and_inverts_the_polarity() -> None:
    """agy reports remaining; QuotaWindow stores used."""
    adapter = AntigravityAdapter(_config(), quota_group="Gemini Models")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["agy"], 0, stdout=_USAGE_ENVELOPE, stderr="")
    )
    with patch("subprocess.run", mock_run):
        observation = adapter.quota_probe()

    assert observation is not None
    assert observation.source == "command"
    by_kind = {w.kind: w for w in observation.windows}
    assert by_kind["weekly"].used_fraction == pytest.approx(1 - 0.6857, abs=1e-4)
    assert by_kind["5h"].used_fraction == pytest.approx(1 - 0.8611, abs=1e-4)


def test_quota_probe_returns_none_when_the_group_is_absent() -> None:
    adapter = AntigravityAdapter(_config(), quota_group="No Such Group")
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["agy"], 0, stdout=_USAGE_ENVELOPE, stderr="")
    )
    with patch("subprocess.run", mock_run):
        assert adapter.quota_probe() is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_antigravity.py -q`
Expected: FAIL — `AntigravityAdapter` takes no `quota_group`.

- [ ] **Step 3: Implement**

```python
def __init__(self, config: AdapterConfig, quota_group: str | None = None) -> None:
    ...
    self._quota_group = quota_group


def quota_probe(self) -> QuotaObservation | None:
    """Read utilization for this pool's group. Costs zero turns and zero tokens."""
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
    if not isinstance(envelope, dict):
        return None

    command = envelope.get("command")
    data = command.get("data") if isinstance(command, dict) else None
    groups = data.get("groups") if isinstance(data, dict) else None
    for group in groups or []:
        if not isinstance(group, dict) or group.get("name") != self._quota_group:
            continue
        windows: list[QuotaWindow] = []
        for bucket in group.get("buckets") or []:
            if not isinstance(bucket, dict):
                continue
            kind = bucket.get("window")
            remaining = bucket.get("remaining_fraction")
            reset_time = bucket.get("reset_time")
            if kind not in {"5h", "weekly", "monthly"}:
                continue
            if not isinstance(remaining, int | float) or not isinstance(reset_time, str):
                continue
            windows.append(
                QuotaWindow(
                    kind,
                    1.0 - float(remaining),  # agy reports remaining, orc stores used
                    datetime.fromisoformat(reset_time.replace("Z", "+00:00")),
                )
            )
        if windows:
            return QuotaObservation(windows, datetime.now(UTC), "command")
    return None
```

- [ ] **Step 4: Wire the third vendor into `orc.toml`**

```toml
[adapters.antigravity]
command = "agy"
pool = "antigravity_gemini"
supported_efforts = ["low", "medium", "high"]
rate_limit_patterns = ["rate limit", "quota exceeded", "usage limit"]

[pools.antigravity_gemini]
windows = ["5h", "weekly"]
budget_units = 100
flat_run_estimate = 1
reserve_fraction = 0.15
quota_group = "Gemini Models"   # the group name agy /usage reports

[lanes.volume]
# `agy models` lists no bare slug; the adapter composes model + effort.
candidates = ["antigravity:gemini-3.8-flash@medium", "codex:gpt-5.6-luna@medium"]

[ladder]
order = ["standard", "quality"]
fallback = "volume"
effort_order = ["low", "medium", "high", "xhigh", "max"]
max_total_attempts = 4
```

Construct the adapter in `orc/cli.py` with its pool's `quota_group`, and confirm `tests/test_fleet.py` still passes — `antigravity_gemini` is already listed in `FLEET.md`.

- [ ] **Step 5: Full check and commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy orc
git add orc/adapters/antigravity.py orc.toml FLEET.md tests/test_antigravity.py
git commit -m "feat: read Antigravity quota from agy /usage and wire the volume lane

agy -p /usage --output-format json returns per-group buckets for zero turns
and zero tokens, making it the only free pre-flight probe of the three
vendors. It reports two groups, confirming SPEC §3's split of
antigravity_gemini from antigravity_claude, and reports remaining rather
than used — inverted on the way into QuotaWindow."
```

---

## Phase 5 — Logging, discovery, and the CLI surface

### Task 13: D6 — `log.jsonl` records the prompt, which SPEC §13 forbids

`_log_task_run` writes `"task": task`. §13 requires `prompt_hash` and states "no code contents, no prompts", and lists fields the baseline omits entirely.

**Files:**
- Modify: `orc/router.py`
- Test: `tests/test_logging.py` (create)

**Interfaces:**
- Consumes: `Attempt.triage`, `Attempt.wall_s`, `TaskRun.blocked` (Task 10).
- Produces: `_log_task_run(target, task, task_run, start_lane, wall_s)` writing the §13 schema.

- [ ] **Step 1: Write the failing test**

Create `tests/test_logging.py`:

```python
import hashlib
import json
from pathlib import Path

from orc.router import run_task
from orc.verify import VerificationPlan, VerificationResult
from tests.test_router import FakeAdapter, _config, _init_repo


def test_log_records_the_hash_and_never_the_prompt(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    task = "fix the flaky auth test in the payments service"
    verify_ok = VerificationResult(True, False, True, "ok", VerificationPlan([], [], []))

    run_task(task, tmp_path, _config(), FakeAdapter(), verify_runner=lambda _t, _p, _to: verify_ok)

    line = (tmp_path / ".orc" / "log.jsonl").read_text(encoding="utf-8").strip()
    entry = json.loads(line)

    assert "task" not in entry
    assert task not in line
    assert entry["prompt_hash"] == hashlib.sha256(task.encode("utf-8")).hexdigest()
    assert entry["start_lane"] == "standard"
    assert entry["outcome"] == "verified"
    assert isinstance(entry["wall_s"], float)
    assert entry["attempts"][0].keys() >= {"vendor", "model", "effort", "triage", "verify"}


def test_the_full_prompt_is_still_available_in_the_run_directory(tmp_path: Path) -> None:
    """§13 excludes prompts from the log because §10 already persists them."""
    _init_repo(tmp_path)
    task = "fix the flaky auth test"
    verify_ok = VerificationResult(True, False, True, "ok", VerificationPlan([], [], []))

    run = run_task(
        task, tmp_path, _config(), FakeAdapter(), verify_runner=lambda _t, _p, _to: verify_ok
    )

    assert task in (run.run_dir / "prompt-1.txt").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_logging.py -q`
Expected: FAIL — `assert "task" not in entry`.

- [ ] **Step 3: Implement**

Replace `_log_task_run` in `orc/router.py`:

```python
def _log_task_run(
    target: Path, task: str, task_run: TaskRun, start_lane: str, wall_s: float
) -> None:
    """Append one line per task per SPEC §13. No prompts, no code contents.

    The full prompt is already persisted under .orc/runs/<id>/prompt-N.txt by §10,
    so `orc log` reads the run directory when a human needs to see it.
    """
    log_file = target / ".orc" / "log.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "task_id": task_run.task_id,
        "prompt_hash": hashlib.sha256(task.encode("utf-8")).hexdigest(),
        "start_lane": start_lane,
        "attempts": [
            {
                "vendor": parse_candidate(attempt.candidate)[0] if attempt.candidate else "",
                "model": parse_candidate(attempt.candidate)[1] if attempt.candidate else "",
                "effort": attempt.effort,
                "lane": attempt.lane,
                "triage": attempt.triage,
                "verify": attempt.verification.ok,
                "wall_s": round(attempt.wall_s, 2),
            }
            for attempt in task_run.attempts
        ],
        "reviewer": None,  # M3
        "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},  # M3
        "outcome": "verified" if task_run.verified else "failed",
        "wall_s": round(wall_s, 2),
        "est_usage": _usage_summary(task_run),
        "quota": [
            {
                "pool": pool,
                "window": verdict.blocked_window,
                "reason": verdict.reason,
                "source": verdict.source,
            }
            for pool, verdict in task_run.blocked
        ],
        "timestamp": datetime.now(UTC).isoformat(),
    }
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
```

Add `import hashlib`. `_usage_summary` counts attempts per vendor, recording observed utilization per attempt where present — that is what makes the reserve default calibratable from real data.

- [ ] **Step 4: Run, check, commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy orc
git add orc/router.py tests/test_logging.py
git commit -m "fix: log.jsonl recorded the raw prompt, which SPEC §13 forbids

Replaces the task text with prompt_hash and adds the fields §13 specifies
that the baseline omitted: start_lane, per-attempt triage and wall_s,
outcome, est_usage, and observed quota. No debuggability is lost because
§10 already persists the full prompt to .orc/runs/<id>/prompt-N.txt."
```

---

### Task 14: D9 and D11 — orc only runs here, and can exhaust its own pool

`find_config` searches upward from the target only, so `orc` cannot run on a repo outside this directory tree — it is unusable as a tool. Separately, rate-limit detection substring-scans the whole transcript, so a task *about* rate limiting marks its own pool exhausted.

**Files:**
- Modify: `orc/config.py`, `orc/adapters/claude_code.py`, `orc/adapters/codex.py`, `orc/adapters/antigravity.py`
- Test: `tests/test_config.py` (create), `tests/test_codex.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `find_config` searching `$ORC_CONFIG`, then upward from the target, then `~/.config/orc/orc.toml`. Adapters scan stderr always and the agent's own output only on failure.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
from pathlib import Path

import pytest

from orc.config import ConfigError, find_config


def test_a_repo_outside_this_tree_falls_back_to_the_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "elsewhere" / "somerepo"
    target.mkdir(parents=True)
    user_config = tmp_path / "home" / ".config" / "orc" / "orc.toml"
    user_config.parent.mkdir(parents=True)
    user_config.write_text("", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ORC_CONFIG", raising=False)

    assert find_config(target) == user_config


def test_the_env_var_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "custom.toml"
    explicit.write_text("", encoding="utf-8")
    target = tmp_path / "repo"
    target.mkdir()
    (target / "orc.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("ORC_CONFIG", str(explicit))

    assert find_config(target) == explicit


def test_the_error_names_both_places_a_config_may_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.delenv("ORC_CONFIG", raising=False)
    target = tmp_path / "repo"
    target.mkdir()

    with pytest.raises(ConfigError, match=r"\.config/orc/orc\.toml"):
        find_config(target)
```

Append to `tests/test_codex.py`:

```python
def test_a_task_about_rate_limiting_does_not_exhaust_the_pool(tmp_path: Path) -> None:
    """D11: substring-scanning the whole transcript let the agent's own words trip it."""
    adapter = CodexAdapter(_codex_config())
    stdout = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "id": "i0",
                "type": "agent_message",
                "text": "I added a rate limit to the login endpoint.",
            },
        }
    )
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(["codex"], 0, stdout=stdout, stderr="")
    )
    req = AgentRequest(
        prompt="add rate limiting",
        mode="agent",
        model="gpt-5.6-terra",
        effort="high",
        cwd=tmp_path,
        timeout_s=30,
        transcript_path=tmp_path / "t.txt",
    )
    with patch("subprocess.run", mock_run):
        result = adapter.run(req)

    assert result.status == "ok"


def test_a_real_rate_limit_on_stderr_is_still_detected(tmp_path: Path) -> None:
    adapter = CodexAdapter(_codex_config())
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            ["codex"], 1, stdout="", stderr="Error: you have exceeded your rate limit."
        )
    )
    req = AgentRequest(
        prompt="do it",
        mode="agent",
        model="gpt-5.6-terra",
        effort="high",
        cwd=tmp_path,
        timeout_s=30,
        transcript_path=tmp_path / "t.txt",
    )
    with patch("subprocess.run", mock_run):
        assert adapter.run(req).status == "rate_limited"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py tests/test_codex.py -q`
Expected: FAIL — `find_config` raises for the outside-tree repo; the rate-limiting task is misclassified as `rate_limited`.

- [ ] **Step 3: Implement discovery**

In `orc/config.py`:

```python
def user_config_path() -> Path:
    """Where a config lives for repos outside this tree. XDG first, then ~/.config."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "orc" / "orc.toml"


def find_config(target: Path) -> Path:
    """Resolve configuration: explicit env var, then the target's tree, then the user's."""
    explicit = os.environ.get("ORC_CONFIG")
    if explicit:
        candidate = Path(explicit).expanduser()
        if candidate.is_file():
            return candidate
        raise ConfigError(f"ORC_CONFIG points at a missing file: {candidate}")

    for directory in (target, *target.parents):
        candidate = directory / "orc.toml"
        if candidate.is_file():
            return candidate

    fallback = user_config_path()
    if fallback.is_file():
        return fallback
    raise ConfigError(
        f"no orc.toml found at or above {target}, and none at {fallback}. "
        f"Create {fallback}, or set ORC_CONFIG to a config file."
    )
```

Add `import os`.

- [ ] **Step 4: Scope rate-limit detection in all three adapters**

Replace the whole-transcript scan with a helper used by each adapter:

```python
def _is_rate_limited(
    stderr: str, agent_text: str, returncode: int, patterns: tuple[str, ...]
) -> bool:
    """Scan stderr always; scan the agent's own words only when the run failed.

    Otherwise a task about rate limiting marks its own pool exhausted.
    """
    if any(pattern in stderr.casefold() for pattern in patterns):
        return True
    if returncode == 0:
        return False
    return any(pattern in agent_text.casefold() for pattern in patterns)
```

- [ ] **Step 5: Run, check, commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy orc
git add orc/config.py orc/adapters/ tests/test_config.py tests/test_codex.py
git commit -m "fix: run on repos outside this tree, and stop self-inflicted exhaustion

find_config only searched upward from the target, so orc could not run on
any repo outside its own directory — unusable as a tool. Adds ORC_CONFIG and
a ~/.config/orc/orc.toml fallback, with an error that names both.

Rate-limit detection substring-scanned the entire transcript, so a task
about rate limiting would mark its own pool exhausted. stderr is scanned
always; the agent's own output only when the run actually failed."
```

---

### Task 15: D12 — CLI structure and an honest report

`main()` dispatches on `sys.argv[1:2] == ["quota"]`, which does not survive adding `log`. The report hardcodes `"→ standard:"` regardless of lane and never shows why an escalation happened.

**Files:**
- Modify: `orc/cli.py`, `orc/report.py`
- Test: `tests/test_cli.py` (create), `tests/test_report.py`

**Interfaces:**
- Consumes: `TaskRun.blocked`, `Attempt.lane`, `Attempt.triage` (Task 10); `PoolStatus.windows`, `PoolStatus.source` (Task 9).
- Produces: `orc "<task>"`, `orc quota`, `orc log [--last N]`; flags `--lane`, `--agent`, `--effort`, `--use-reserve`, `--target`, `--allow-destructive`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from orc.cli import app

runner = CliRunner()


def test_bare_task_and_subcommands_coexist() -> None:
    """SPEC §1's one-line UX and §11's subcommands must both work."""
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert "quota" in runner.invoke(app, ["--help"]).output
    assert "log" in runner.invoke(app, ["--help"]).output


def test_quota_does_not_need_a_target_repo(tmp_path) -> None:
    result = runner.invoke(app, ["quota", "--target", str(tmp_path)])
    assert result.exit_code in {0, 2}  # 2 only when no config is discoverable
    assert "Traceback" not in result.output
```

Append to `tests/test_report.py`:

```python
def test_the_trail_names_the_lane_and_explains_the_escalation() -> None:
    from pathlib import Path

    from orc.adapters.base import AgentResult
    from orc.report import format_run
    from orc.router import Attempt, TaskRun
    from orc.verify import VerificationPlan, VerificationResult

    plan = VerificationPlan(["pytest -q"], [], [])
    run = TaskRun(
        task_id="abc123",
        branch="orc/x-abc123",
        run_dir=Path("/tmp"),
        base="deadbeef",
        attempts=[
            Attempt(
                1,
                "high",
                AgentResult("ok", "t", None, Path("/tmp"), 1, []),
                VerificationResult(False, False, True, "2 failed", plan),
                "claude:sonnet@high",
                "standard",
                "lazy",
                12.5,
            ),
            Attempt(
                2,
                "xhigh",
                AgentResult("ok", "t", None, Path("/tmp"), 4, ["pytest -q"]),
                VerificationResult(True, False, True, "34 passed", plan),
                "claude:sonnet@xhigh",
                "standard",
                None,
                41.0,
            ),
        ],
        diff="",
        blocked=[],
    )
    output = format_run(run)

    assert "standard" in output
    assert "claude:sonnet@high" in output
    assert "lazy" in output  # the human can see WHY it escalated
    assert "xhigh" in output


def test_quota_table_labels_estimated_numbers_as_estimated() -> None:
    from orc.ledger import PoolStatus
    from orc.report import format_quota

    output = format_quota(
        [PoolStatus("copilot", ["monthly"], 20.0, 5.0, None, windows=[], source="estimated")]
    )
    assert "estimated" in output
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py tests/test_report.py -q`
Expected: FAIL — `quota` is not registered on `app`; `format_run` prints a hardcoded lane and no triage.

- [ ] **Step 3: Restructure the CLI**

In `orc/cli.py`, register real subcommands and let the callback own the bare task, deleting the `sys.argv` dispatch and the second Typer app:

```python
app = typer.Typer(add_completion=False, invoke_without_command=True)


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    task: Annotated[str | None, typer.Argument(help="Coding task to perform.")] = None,
    target: Annotated[Path, typer.Option(help="Git repository to work in.")] = Path("."),
    lane: Annotated[str | None, typer.Option(help="Start rung: volume, standard, quality.")] = None,
    agent: Annotated[str | None, typer.Option(help="Force one candidate, as vendor:model.")] = None,
    effort: Annotated[str | None, typer.Option(help="Force the starting effort level.")] = None,
    use_reserve: Annotated[
        bool, typer.Option(help="Spend into a pool's reserved headroom.")
    ] = False,
    allow_destructive: Annotated[bool, typer.Option(help="Allow a denylisted task.")] = False,
) -> None:
    """Run a coding agent ladder on TASK, then verify it independently."""
    if ctx.invoked_subcommand is not None:
        return
    if task is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)
    ...


@app.command()
def quota(target: Annotated[Path, typer.Option()] = Path(".")) -> None:
    """Print the quota ledger: pool, window, utilization, reset, and source."""
    ...


@app.command("log")
def show_log(
    target: Annotated[Path, typer.Option()] = Path("."),
    last: Annotated[int, typer.Option(help="How many recent runs to show.")] = 1,
) -> None:
    """Summarize recent runs from .orc/log.jsonl and their artifact directories."""
    ...


def main() -> None:
    app()
```

Construct all three adapters, passing each pool's `quota_group` to `AntigravityAdapter`. Apply `--lane` by starting the ladder at that rung, `--agent`/`--effort` by overriding the candidate list, and thread `--use-reserve` into `run_task`.

- [ ] **Step 4: Make the report honest**

In `orc/report.py`, print the real lane and candidate per attempt, the triage verdict that caused each escalation, wall time, any pools held back with their reset times, and a usage summary. In `format_quota`, print per-window utilization with the reset time and a `source` column, labeling estimated rows "estimated".

- [ ] **Step 5: Run, check, commit**

```bash
uv run pytest -q && uv run ruff check . && uv run mypy orc
git add orc/cli.py orc/report.py tests/test_cli.py tests/test_report.py
git commit -m "feat: real subcommands, routing overrides, and an honest activity trail

Replaces the sys.argv dispatch, which could not survive adding 'log', with
one Typer app whose callback owns the bare task string — so SPEC §1's
one-line UX and §11's subcommands both work. Adds --lane, --agent, --effort
and --use-reserve.

The trail now names the lane and candidate actually used and shows the
triage verdict behind each escalation; the quota table shows per-window
utilization, reset times, and whether each number was observed or estimated."
```

---

## Acceptance Mapping

Every `SPEC.md` §12 M2 checkbox, and the task that satisfies it.

| Acceptance criterion | Task |
|---|---|
| Mocked `rate_limited` reroutes and marks the pool exhausted | 10 (existing test retained) |
| Triage rules unit-tested with synthetic `AgentResult`s | 10 (existing test retained) |
| `orc quota` shows every pool with utilization, reset, and observed-or-estimated | 9, 15 |
| A pool past its reserve is skipped before a run is spent; `--use-reserve` overrides | 9, 10 |
| Every rung blocked degrades to `volume` rather than failing | 10 |
| Structured failure context on verification-failure retries | existing, rebased onto `base` in 5 |
| Exhaustion prints copy-pasteable rescue instructions | 15 |
| `log.jsonl` per §13, with `prompt_hash` and no prompt text | 13 |
| Each adapter has fixture parser tests plus an opt-in `live` argv test | 1, 2, 3, 11 |
| `orc` runs against a repo outside its own tree | 14 |

## Defect Register Mapping

| Defect | Task | Defect | Task |
|---|---|---|---|
| D1 codex argv | 2 | D7 ledger never resets | 9 |
| D2 claude triage blind | 3 | D8 codex usage discarded | 2 |
| D3 `.orc/` dirties the tree | 4 | D9 config discovery | 14 |
| D4 agent timeout | 6 | D10 test enumeration | 5 |
| D5 diff misses committed work | 5 | D11 rate-limit false positive | 14 |
| D6 log records the prompt | 13 | D12 CLI, report, stdin, dead config | 2, 3, 6, 15 |

## Self-Review Notes

- **Spec coverage:** every M2 acceptance criterion and all twelve defects map to a task above. The `volume` fallback, reserve policy, and `QuotaObservation` contract each trace to an amended `SPEC.md` section.
- **Type consistency:** `QuotaWindow(kind, used_fraction, resets_at)` and `QuotaObservation(windows, observed_at, source)` are defined in Task 7 and used unchanged in Tasks 8, 9, 10 and 12. `Eligibility(ok, reason, blocked_window, resets_at, source)` is defined in Task 9 and consumed in Tasks 10, 13 and 15. `Attempt` gains `lane`, `triage` and `wall_s` in Task 10 and is read with those names in 13 and 15. `git_diff(target, base)` is two-argument from Task 5 onward everywhere.
- **Known ordering constraint:** Task 9 changes `PoolConfig.window` to `windows`, so every test fixture built before it needs updating in the same commit. This is called out in Task 9 Step 6.
- **Deliberately not fixed:** test tampering keeps its hard abort (owner's decision); only its enumeration scope changes, in Task 5.
