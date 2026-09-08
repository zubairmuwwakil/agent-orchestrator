"""Router: multi-lane ladder routing, lazy-vs-dumb triage, and verification loop."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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
from orc.ledger import Eligibility, Ledger
from orc.verify import VerificationPlan, VerificationResult, detect_commands, run_verification


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


@dataclass(slots=True)
class TaskRun:
    task_id: str
    branch: str
    run_dir: Path
    attempts: list[Attempt]
    diff: str
    # (candidate, verdict) for every rung the ledger refused before a run was spent.
    blocked: list[tuple[str, Eligibility]] = field(default_factory=list)

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
    use_reserve: bool = False,
    start_lane: str | None = None,
    agent: str | None = None,
    effort: str | None = None,
) -> TaskRun:
    """Run coding agent ladder on task in target, verifying and triaging each attempt."""
    from orc.gitops import ensure_safe_target

    task_started = time.monotonic()
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

    # (lane, candidate) pairs so a lane's timeout override is known. The fallback lane is
    # held apart: it is entered only when every ladder rung is quota-blocked, never as a rung.
    ladder_candidates, fallback_candidates = _candidate_ladder(
        config, start_lane=start_lane, agent=agent, effort=effort
    )
    candidate_list = list(ladder_candidates)
    if not candidate_list and "standard" in config.lanes:
        candidate_list = [("standard", c) for c in config.lanes["standard"].candidates]

    max_attempts = config.ladder.max_total_attempts
    n_ladder = len(candidate_list)  # captured before any fallback splice
    attempts: list[Attempt] = []
    blocked: list[tuple[str, Eligibility]] = []
    feedback = ""
    candidate_idx = 0
    current_effort: str | None = None
    candidate_attempts = 0
    fallback_entered = False
    runs_spent = 0  # every ad.run, including one that returns rate_limited — the hard cap
    ladder_blocked = 0

    while runs_spent < max_attempts:
        if candidate_idx >= len(candidate_list):
            # `volume` sits beside the ladder, not below it. It is reached only when every
            # ladder candidate was refused by the ledger before any run was spent (SPEC §6):
            # not when merely some were, and not after a run (a rate_limited spend included).
            if (
                runs_spent == 0
                and not fallback_entered
                and fallback_candidates
                and ladder_blocked == n_ladder
            ):
                candidate_list = candidate_list + list(fallback_candidates)
                fallback_entered = True
                continue
            break

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

        # No run is spent without an eligibility verdict. A vendor with no resolvable pool
        # cannot be metered against the reserve, so it is refused rather than run unmetered.
        verdict = (
            ledger.eligibility(pool_id, pool_cfg, use_reserve=use_reserve)
            if pool_cfg is not None
            else Eligibility(False, "no-pool")
        )
        if not verdict.ok:
            blocked.append((candidate_str, verdict))
            if not fallback_entered:
                ladder_blocked += 1
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

        started = time.monotonic()
        runs_spent += 1  # counts toward the hard cap and takes the volume fallback off the table
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
        elapsed = time.monotonic() - started

        # Telemetry is authoritative: a run that reported real utilization must not also be
        # estimated, or the pool is double-counted. Only the estimate path needs record_run.
        if result.quota is not None:
            ledger.record_observation(pool_id, result.quota)
        elif pool_cfg is not None:
            ledger.record_run(pool_id, pool_cfg)

        # Before anything else the attempt's output feeds — a test-file edit aborts the
        # run regardless of status, so a rate_limited reroute cannot re-baseline a tamper.
        assert_tests_unchanged(
            target, test_snapshot, config.safety.test_path_patterns, config.safety.skip_dirs
        )

        if result.status == "rate_limited":
            if pool_cfg is not None:
                ledger.mark_exhausted(pool_id, pool_cfg)
            candidate_idx += 1
            current_effort = None
            candidate_attempts = 0
            continue

        verification = invoke_verifier(target, plan, config.verify.timeout_s)
        (run_dir / f"verify-{attempt_number}.txt").write_text(verification.output, encoding="utf-8")
        attempts.append(
            Attempt(
                attempt_number,
                effort,
                result,
                verification,
                candidate_str,
                lane_name,
                wall_s=elapsed,
            )
        )

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
        attempts[-1].triage = triage
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

    task_run = TaskRun(task_id, branch, run_dir, attempts, git_diff(target, base), blocked)
    start_lane = candidate_list[0][0] if candidate_list else ""
    _log_task_run(target, task, task_run, start_lane, time.monotonic() - task_started)
    return task_run


def _candidate_ladder(
    config: OrcConfig,
    start_lane: str | None = None,
    agent: str | None = None,
    effort: str | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (ladder, fallback) as (lane, candidate) pairs.

    The fallback lane is entered only when every ladder candidate is quota-blocked,
    never as a rung: a cheap first rung would consume the fixed attempt budget and
    starve `quality` (SPEC §6).
    """
    if start_lane is not None and start_lane not in config.lanes:
        raise ValueError(f"unknown lane {start_lane!r}")
    if effort is not None and effort not in config.ladder.effort_order:
        raise ValueError(f"effort {effort!r} is not configured")

    ladder_order = config.ladder.order
    fallback_lane = config.ladder.fallback
    if start_lane is not None:
        if start_lane == fallback_lane:
            ladder_order = [start_lane]
            fallback_lane = None
        elif start_lane in ladder_order:
            ladder_order = ladder_order[ladder_order.index(start_lane) :]
        else:
            raise ValueError(f"lane {start_lane!r} is not a configured ladder rung or fallback")

    ladder = [
        (lane, candidate)
        for lane in ladder_order
        if lane in config.lanes
        for candidate in config.lanes[lane].candidates
    ]
    fallback: list[tuple[str, str]] = []
    if fallback_lane is not None and fallback_lane in config.lanes:
        fallback = [(fallback_lane, c) for c in config.lanes[fallback_lane].candidates]

    if agent is not None:
        if "@" in agent:
            raise ValueError("--agent must be vendor:model; set effort separately with --effort")
        candidates = ladder + fallback
        matching = [
            (lane, candidate)
            for lane, candidate in candidates
            if candidate.rsplit("@", 1)[0] == agent
        ]
        if not matching:
            raise ValueError(f"agent {agent!r} is not a configured lane candidate")
        lane, candidate = matching[0]
        selected_effort = effort or parse_candidate(candidate)[2]
        return [(lane, f"{agent}@{selected_effort}")], []

    if effort is not None:
        ladder = [(lane, f"{candidate.rsplit('@', 1)[0]}@{effort}") for lane, candidate in ladder]
        fallback = [
            (lane, f"{candidate.rsplit('@', 1)[0]}@{effort}") for lane, candidate in fallback
        ]
    return ladder, fallback


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


def _usage_summary(task_run: TaskRun) -> dict[str, int]:
    """Count attempted runs by vendor without recording any prompt-derived data."""
    usage: dict[str, int] = {}
    for attempt in task_run.attempts:
        vendor = parse_candidate(attempt.candidate)[0] if attempt.candidate else "unknown"
        usage[vendor] = usage.get(vendor, 0) + 1
    return usage


def _observed_quota(task_run: TaskRun) -> list[dict[str, object]]:
    """Flatten per-attempt telemetry for the learned-routing log.

    An adapter observation is tied to its vendor's configured pool by the router;
    the candidate vendor is therefore the only stable identifier available here.
    """
    observations: list[dict[str, object]] = []
    for attempt in task_run.attempts:
        if attempt.result.quota is None:
            continue
        pool = parse_candidate(attempt.candidate)[0] if attempt.candidate else "unknown"
        for window in attempt.result.quota.windows:
            observations.append(
                {
                    "pool": pool,
                    "window": window.kind,
                    "used_fraction": window.used_fraction,
                    "source": attempt.result.quota.source,
                }
            )
    return observations


def _log_task_run(
    target: Path, task: str, task_run: TaskRun, start_lane: str, wall_s: float
) -> None:
    """Append one privacy-preserving task record per SPEC §13.

    Prompts belong only in the run artifact directory. The JSONL is intentionally
    safe to use as aggregate routing data, so it carries a digest rather than text.
    """
    log_file = target / ".orc" / "log.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "task_id": task_run.task_id,
        "prompt_hash": hashlib.sha256(task.encode("utf-8")).hexdigest(),
        "start_lane": start_lane,
        "attempts": [
            {
                "vendor": parse_candidate(a.candidate)[0] if a.candidate else "",
                "model": parse_candidate(a.candidate)[1] if a.candidate else "",
                "effort": a.effort,
                "lane": a.lane,
                "triage": a.triage,
                "verify": a.verification.ok,
                "wall_s": round(a.wall_s, 2),
            }
            for a in task_run.attempts
        ],
        "reviewer": None,
        "findings": {"p0": 0, "p1": 0, "p2": 0, "p3": 0},
        "outcome": "verified" if task_run.verified else "failed",
        "wall_s": round(wall_s, 2),
        "est_usage": _usage_summary(task_run),
        "quota": _observed_quota(task_run),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
