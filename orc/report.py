"""Human-readable activity trail for the M1 run result."""

from __future__ import annotations

from orc.ledger import PoolStatus
from orc.router import TaskRun


def format_quota(statuses: list[PoolStatus]) -> str:
    """Render an advisory pool/window/remaining/exhausted-until table (SPEC §7)."""
    if not statuses:
        return "no pools configured"
    header = f"{'pool':<20}{'window':<10}{'est. remaining':<28}{'exhausted until':<20}"
    lines = [header, "-" * len(header)]
    for status in statuses:
        remaining = f"{status.remaining_units:g} / {status.budget_units:g} units (estimated)"
        exhausted = status.exhausted_until or "-"
        lines.append(f"{status.pool_id:<20}{status.window:<10}{remaining:<28}{exhausted:<20}")
    return "\n".join(lines)


def format_run(run: TaskRun) -> str:
    """Render decisions, actions, results, and the git-derived diff only."""
    lines = [f"task {run.task_id}  branch {run.branch}"]
    for attempt in run.attempts:
        result = "green" if attempt.verification.ok else "failed"
        lines.append(
            f"→ standard: claude attempt {attempt.number} @{attempt.effort}  "
            f"agent={attempt.result.status} verify={result}"
        )
        if attempt.verification.partial:
            lines.append("→ verify: no tests detected; lint/build only")
    lines.append(f"{'✓' if run.verified else '✗'} artifacts: {run.run_dir}")
    lines.append("\nDiff:\n" + (run.diff or "(no working-tree diff)"))
    return "\n".join(lines)
