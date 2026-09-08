"""Human-readable activity trail for a run and its quota status."""

from __future__ import annotations

from datetime import UTC, datetime

from orc.ledger import PoolStatus
from orc.router import TaskRun, parse_candidate

# Widths chosen so the widest row fits an 80-column terminal; the table previously
# ran to 96 and folded `source` onto its own line.
_QUOTA_COLUMNS: tuple[tuple[str, int], ...] = (
    ("pool", 20),
    ("window", 8),
    ("used", 8),
    ("resets", 27),
    ("source", 13),
)


def _parse_moment(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _format_reset(value: str | None, now: datetime | None = None) -> str:
    """Show the reset instant and how far off it is; the delay is the decision-relevant part."""
    moment = _parse_moment(value)
    if moment is None:
        return "-"
    stamp = moment.strftime("%Y-%m-%d %H:%MZ")
    remaining = int((moment - (now or datetime.now(UTC))).total_seconds())
    if remaining <= 0:
        return f"{stamp} (now)"
    days, rest = divmod(remaining, 86400)
    hours, rest = divmod(rest, 3600)
    relative = f"{days}d {hours}h" if days else f"{hours}h {rest // 60}m"
    return f"{stamp} ({relative})"


def _quota_rows(status: PoolStatus) -> list[tuple[str, ...]]:
    """One row per window. `used` is a fraction of the window in every row, observed or not."""
    if status.windows:
        return [
            (
                status.pool_id,
                str(window.get("kind", status.window)),
                f"{float(window.get('used_fraction', 0.0)):.1%}",
                _format_reset(str(window.get("resets_at")) or None),
                status.source,
            )
            for window in status.windows
        ]
    spent_fraction = status.spent_units / status.budget_units if status.budget_units else 0.0
    return [
        (
            status.pool_id,
            status.window,
            f"{min(spent_fraction, 1.0):.1%}",
            _format_reset(status.exhausted_until),
            "estimated",
        )
    ]


def format_quota(statuses: list[PoolStatus]) -> str:
    """Render per-window utilization, or the estimated fallback, in one column meaning."""
    if not statuses:
        return "no pools configured"

    def row(cells: tuple[str, ...]) -> str:
        return "".join(
            f"{cell:<{width}}" for cell, (_, width) in zip(cells, _QUOTA_COLUMNS, strict=True)
        ).rstrip()

    header = row(tuple(name for name, _ in _QUOTA_COLUMNS))
    lines = [header, "-" * len(header)]
    for status in statuses:
        lines.extend(row(cells) for cells in _quota_rows(status))
    lines.append("")
    lines.append("used = fraction of that window consumed. Estimated rows are advisory only.")
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
