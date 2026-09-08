"""Router: multi-lane ladder routing, lazy-vs-dumb triage, and verification loop."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from orc.adapters.base import AgentAdapter, AgentRequest, AgentResult
from orc.config import OrcConfig
from orc.gitops import (
    assert_tests_unchanged,
    base_commit,
    create_branch,
    git_diff,
    git_diff_stat,
    new_task_id,
    test_file_snapshot,
)
from orc.ledger import Ledger
from orc.verify import VerificationPlan, VerificationResult, detect_commands, run_verification


@dataclass(slots=True)
class Attempt:
    number: int
    effort: str
    result: AgentResult
    verification: VerificationResult
    candidate: str = ""


@dataclass(slots=True)
class TaskRun:
    task_id: str
    branch: str
    run_dir: Path
    attempts: list[Attempt]
    diff: str

    @property
    def verified(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].verification.ok


def parse_candidate(candidate: str) -> tuple[str, str, str]:
    """Parse a config-owned `vendor:model@effort` candidate string."""
    try:
        vendor, model_and_effort = candidate.split(":", maxsplit=1)
        model, effort = model_and_effort.rsplit("@", maxsplit=1)
    except ValueError as error:
        raise ValueError(f"invalid lane candidate: {candidate!r}") from error
    return vendor, model, effort


def resolve_pool_id(vendor: str, config: OrcConfig) -> str:
    """Find the configured pool for a given adapter vendor."""
    adapter_cfg = config.adapters.get(vendor)
    if adapter_cfg and adapter_cfg.pool and adapter_cfg.pool in config.pools:
        return adapter_cfg.pool
    for suffix in ("_pro", "_plus", "_paid"):
        candidate_pool = f"{vendor}{suffix}"
        if candidate_pool in config.pools:
            return candidate_pool
    for pool_id in config.pools:
        if pool_id.startswith(vendor):
            return pool_id
    return next(iter(config.pools.keys()))


def triage_failure(
    result: AgentResult,
    verification: VerificationResult,
    files_edited: bool,
    verify_attempts: int,
    min_tool_calls: int = 2,
) -> Literal["lazy", "dumb"]:
    """Triage failed attempt as lazy (effort +1) or dumb (next candidate/rung)."""
    # 1. Did the agent run verification commands?
    plan_commands = verification.plan.tests + verification.plan.lint + verification.plan.build
    if plan_commands and result.ran_commands:
        ran_any_verify = any(
            any(cmd in ran_cmd for ran_cmd in result.ran_commands) for cmd in plan_commands
        )
        if not ran_any_verify:
            return "lazy"
    elif plan_commands and not result.ran_commands:
        return "lazy"

    # 2. Tool call count below floor
    if result.tool_call_count is not None and result.tool_call_count < min_tool_calls:
        return "lazy"

    # 3. Success claimed while verification failed
    if result.status == "ok" and not verification.ok and not files_edited:
        return "lazy"

    # 4. Dumb signals: verified ran >= 2 times, files were edited, still failing
    if verify_attempts >= 2 and files_edited:
        return "dumb"

    # Ambiguous -> treat as lazy first (SPEC §2.3)
    return "lazy"


def run_task(
    task: str,
    target: Path,
    config: OrcConfig,
    adapter: AgentAdapter | dict[str, AgentAdapter] | None = None,
    adapters: dict[str, AgentAdapter] | None = None,
    allow_destructive: bool = False,
    verify_runner: Callable[[Path, VerificationPlan, int], VerificationResult] | None = None,
) -> TaskRun:
    """Run coding agent ladder on task in target, verifying and triaging each attempt."""
    from orc.gitops import ensure_safe_target

    ensure_safe_target(target, task, allow_destructive)

    # Normalize adapters
    adapter_map: dict[str, AgentAdapter] = {}
    if isinstance(adapters, dict):
        adapter_map.update(adapters)
    if isinstance(adapter, AgentAdapter):
        adapter_map[adapter.name] = adapter
    elif isinstance(adapter, dict):
        adapter_map.update(adapter)

    task_id = new_task_id()
    branch = create_branch(target, task, task_id)
    base = base_commit(target)
    run_dir = target / ".orc" / "runs" / task_id
    run_dir.mkdir(parents=True, exist_ok=True)
    plan = detect_commands(target, config.verify)
    ledger = Ledger(target / ".orc" / "ledger.json")
    invoke_verifier = verify_runner or run_verification

    # Collect candidate ladder as (lane, candidate) so a lane's timeout override is known.
    candidate_list: list[tuple[str, str]] = []
    ladder_order = config.ladder.order
    for rung in ladder_order:
        if rung in config.lanes:
            candidate_list.extend((rung, c) for c in config.lanes[rung].candidates)
    if not candidate_list and "standard" in config.lanes:
        candidate_list.extend(("standard", c) for c in config.lanes["standard"].candidates)

    max_attempts = config.ladder.max_total_attempts
    attempts: list[Attempt] = []
    feedback = ""
    candidate_idx = 0
    current_effort: str | None = None
    candidate_attempts = 0

    while len(attempts) < max_attempts and candidate_idx < len(candidate_list):
        lane_name, candidate_str = candidate_list[candidate_idx]
        vendor, model, base_effort = parse_candidate(candidate_str)
        pool_id = resolve_pool_id(vendor, config)
        pool_cfg = config.pools.get(pool_id)

        ad = adapter_map.get(vendor)
        if ad is None or not ad.available():
            candidate_idx += 1
            current_effort = None
            candidate_attempts = 0
            continue

        if pool_cfg and ledger.is_exhausted(pool_id, pool_cfg):
            candidate_idx += 1
            current_effort = None
            candidate_attempts = 0
            continue

        effort = current_effort or base_effort
        attempt_number = len(attempts) + 1

        lane_cfg = config.lanes.get(lane_name)
        agent_timeout = (lane_cfg.timeout_s if lane_cfg else None) or config.agents.timeout_s

        prompt = _agent_prompt(task, feedback)
        prompt_path = run_dir / f"prompt-{attempt_number}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        test_snapshot = test_file_snapshot(
            target, config.safety.test_path_patterns, config.safety.skip_dirs
        )

        result = ad.run(
            AgentRequest(
                prompt=prompt,
                mode="agent",
                model=model,
                effort=effort,
                cwd=target,
                timeout_s=agent_timeout,
                transcript_path=run_dir / f"attempt-{attempt_number}-transcript.txt",
            )
        )

        if pool_cfg:
            ledger.record_run(pool_id, pool_cfg)

        # Before anything else the attempt's output feeds — a test-file edit aborts the
        # run regardless of status, so a rate_limited reroute cannot re-baseline a tamper.
        assert_tests_unchanged(
            target, test_snapshot, config.safety.test_path_patterns, config.safety.skip_dirs
        )

        if result.status == "rate_limited":
            if pool_cfg:
                ledger.mark_exhausted(pool_id, pool_cfg)
            candidate_idx += 1
            current_effort = None
            candidate_attempts = 0
            continue

        verification = invoke_verifier(target, plan, config.verify.timeout_s)
        (run_dir / f"verify-{attempt_number}.txt").write_text(verification.output, encoding="utf-8")
        attempts.append(Attempt(attempt_number, effort, result, verification, candidate_str))

        if result.status == "ok" and verification.ok:
            break

        # Verification failed -> triage
        candidate_attempts += 1
        diff_exists = bool(git_diff(target, base))
        triage = triage_failure(
            result,
            verification,
            files_edited=diff_exists,
            verify_attempts=candidate_attempts,
            min_tool_calls=config.triage.min_tool_calls,
        )
        feedback = _feedback(verification, result, target, base)

        if triage == "lazy":
            if not config.ladder.is_max_effort(effort):
                current_effort = config.ladder.next_effort(effort)
            else:
                candidate_idx += 1
                current_effort = None
                candidate_attempts = 0
        else:  # dumb
            candidate_idx += 1
            current_effort = None
            candidate_attempts = 0

    task_run = TaskRun(task_id, branch, run_dir, attempts, git_diff(target, base))
    _log_task_run(target, task, task_run)
    return task_run


def _agent_prompt(task: str, feedback: str) -> str:
    return (
        "Work only in the current repository. Implement this task:\n\n"
        f"{task}\n\n"
        "Do not edit, skip, weaken, or delete tests. Fix the implementation instead. "
        "Do not modify .git or write outside this repository. Run the relevant checks before finishing."
        f"{feedback}"
    )


def _feedback(
    verification: VerificationResult, result: AgentResult, target: Path, base: str
) -> str:
    diff_stat = git_diff_stat(target, base)
    details = verification.output[-6000:] or result.text[-2000:]
    parts = [
        "\n\nThe previous attempt did not pass independent verification.",
        f"Exact output follows:\n{details}",
    ]
    if diff_stat.strip():
        parts.append(f"\nFiles modified in working tree so far:\n{diff_stat}")
    parts.append(
        "\nFix the implementation so all verification checks pass. "
        "Do not edit, delete, or weaken tests."
    )
    return "\n".join(parts) + "\n"


def _log_task_run(target: Path, task: str, task_run: TaskRun) -> None:
    """Record task run entry into .orc/log.jsonl per SPEC §13."""
    log_file = target / ".orc" / "log.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "task_id": task_run.task_id,
        "task": task,
        "branch": task_run.branch,
        "attempts": [
            {
                "attempt": a.number,
                "candidate": a.candidate,
                "effort": a.effort,
                "status": a.result.status,
                "verified": a.verification.ok,
            }
            for a in task_run.attempts
        ],
        "outcome": "verified" if task_run.verified else "failed",
        "timestamp": datetime.now(UTC).isoformat(),
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
