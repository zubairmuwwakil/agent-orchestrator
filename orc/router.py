"""M1's single-lane retry loop, with verification feedback."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from orc.adapters.base import AgentAdapter, AgentRequest, AgentResult
from orc.config import OrcConfig
from orc.gitops import (
    assert_tests_unchanged,
    create_branch,
    git_diff,
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


def run_task(
    task: str,
    target: Path,
    config: OrcConfig,
    adapter: AgentAdapter,
    allow_destructive: bool = False,
    verify_runner: Callable[[Path, VerificationPlan, int], VerificationResult] | None = None,
) -> TaskRun:
    """Create an isolated branch, run Claude, and retry on harness failures."""
    from orc.gitops import ensure_safe_target

    ensure_safe_target(target, task, allow_destructive)
    candidate = config.lanes["standard"].candidates[0]
    vendor, model, effort = parse_candidate(candidate)
    if vendor != adapter.name:
        raise ValueError(f"M1 adapter {adapter.name!r} cannot run candidate for {vendor!r}")
    task_id = new_task_id()
    branch = create_branch(target, task, task_id)
    run_dir = target / ".orc" / "runs" / task_id
    run_dir.mkdir(parents=True, exist_ok=True)
    plan = detect_commands(target, config.verify)
    ledger = Ledger(target / ".orc" / "ledger.json")
    pool = config.pools["claude_pro"]
    attempts: list[Attempt] = []
    feedback = ""
    invoke_verifier = verify_runner or run_verification

    for attempt_number in range(1, config.ladder.retry_count + 2):
        prompt = _agent_prompt(task, feedback)
        prompt_path = run_dir / f"prompt-{attempt_number}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        test_snapshot = test_file_snapshot(target)
        result = adapter.run(
            AgentRequest(
                prompt=prompt,
                mode="agent",
                model=model,
                effort=effort,
                cwd=target,
                timeout_s=config.verify.timeout_s,
                transcript_path=run_dir / f"attempt-{attempt_number}-transcript.txt",
            )
        )
        ledger.record_run("claude_pro", pool)
        assert_tests_unchanged(target, test_snapshot)
        verification = invoke_verifier(target, plan, config.verify.timeout_s)
        (run_dir / f"verify-{attempt_number}.txt").write_text(verification.output, encoding="utf-8")
        attempts.append(Attempt(attempt_number, effort, result, verification))
        if result.status == "ok" and verification.ok:
            break
        feedback = _feedback(verification, result)
        effort = config.ladder.next_effort(effort)
    return TaskRun(task_id, branch, run_dir, attempts, git_diff(target))


def _agent_prompt(task: str, feedback: str) -> str:
    return (
        "Work only in the current repository. Implement this task:\n\n"
        f"{task}\n\n"
        "Do not edit, skip, weaken, or delete tests. Fix the implementation instead. "
        "Do not modify .git or write outside this repository. Run the relevant checks before finishing."
        f"{feedback}"
    )


def _feedback(verification: VerificationResult, result: AgentResult) -> str:
    details = verification.output[-6000:] or result.text[-2000:]
    return f"\n\nThe previous attempt did not pass independent verification. Exact output follows:\n{details}\n"
