"""Human-readable activity trail for a run and its quota status."""

from __future__ import annotations

from orc.ledger import PoolStatus
from orc.router import TaskRun, parse_candidate


def format_quota(statuses: list[PoolStatus]) -> str:
    """Render per-window observed utilization or the estimated fallback."""
    if not statuses:
        return "no pools configured"
    header = f"{'pool':<22}{'window':<10}{'utilization':<30}{'reset':<28}{'source'}"
    lines = [header, "-" * len(header)]
    for status in statuses:
        if status.windows:
            for window in status.windows:
                used = float(window.get("used_fraction", 0.0))
                reset = str(window.get("resets_at", "-"))
                kind = str(window.get("kind", status.window))
                lines.append(
                    f"{status.pool_id:<22}{kind:<10}{used:.1%}{'':<25}{reset:<28}{status.source}"
                )
            continue
        remaining = f"{status.remaining_units:g} / {status.budget_units:g} units (estimated)"
        exhausted = status.exhausted_until or "-"
        lines.append(
            f"{status.pool_id:<22}{status.window:<10}{remaining:<30}{exhausted:<28}estimated"
        )
    return "\n".join(lines)


def _usage_summary(run: TaskRun) -> str:
    counts: dict[str, int] = {}
    for attempt in run.attempts:
        vendor = parse_candidate(attempt.candidate)[0] if attempt.candidate else "unknown"
        counts[vendor] = counts.get(vendor, 0) + 1
    return ", ".join(
        f"{vendor} {count} run{'s' if count != 1 else ''}" for vendor, count in counts.items()
    )


def format_run(run: TaskRun) -> str:
    """Render decisions, actions, results, and the git-derived diff honestly."""
    lines = [f"task {run.task_id}  branch {run.branch}"]
    for attempt in run.attempts:
        result = "green" if attempt.verification.ok else "failed"
        agent_label = attempt.candidate or "unknown"
        lines.append(
            f"→ {attempt.lane or 'unknown'}: {agent_label} attempt {attempt.number} "
            f"agent={attempt.result.status} verify={result} {attempt.wall_s:.1f}s"
        )
        if attempt.triage is not None:
            lines.append(f"→ triage: {attempt.triage}")
        if attempt.verification.partial:
            lines.append("→ verify: no tests detected; lint/build only")
    for candidate, verdict in run.blocked:
        reset = verdict.resets_at.isoformat() if verdict.resets_at is not None else "unknown"
        lines.append(
            f"→ quota: {candidate} held back ({verdict.reason}, {verdict.source}; reset {reset})"
        )
    if run.attempts:
        lines.append(f"→ usage: {_usage_summary(run)}")
    lines.append(f"{'✓' if run.verified else '✗'} artifacts: {run.run_dir}")
    lines.append("\nDiff:\n" + (run.diff or "(no working-tree diff)"))
    if not run.verified:
        lines.append("\n" + "─" * 60)
        lines.append("⚠ Task did not pass independent verification.")
        lines.append(f"To inspect or resume manually on branch '{run.branch}':")
        lines.append(f"  git switch {run.branch}")
        lines.append("  git diff")
        lines.append("─" * 60)
    return "\n".join(lines)
