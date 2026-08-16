"""Human-readable activity trail for the M1 run result."""

from __future__ import annotations

from orc.router import TaskRun


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
