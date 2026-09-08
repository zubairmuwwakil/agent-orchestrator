from pathlib import Path

from orc.ledger import PoolStatus
from orc.report import format_quota


def test_format_quota_lists_each_pool_with_estimated_remaining() -> None:
    statuses = [
        PoolStatus("claude", "weekly", 20.0, 1.0, None),
        PoolStatus("codex", "5h", 10.0, 10.0, "2026-08-20T00:00:00+00:00"),
    ]

    output = format_quota(statuses)

    assert "claude" in output
    assert "weekly" in output
    assert "5.0%" in output  # 1 of 20 units spent, expressed the same way telemetry is
    assert "estimated" in output
    assert "codex" in output
    assert "2026-08-20 00:00Z" in output


def test_quota_table_fits_a_standard_terminal() -> None:
    """The table wrapped at 80 columns, folding the source column onto its own line."""
    statuses = [
        PoolStatus(
            "antigravity_gemini",
            "weekly",
            0.0,
            0.0,
            None,
            windows=[
                {
                    "kind": "weekly",
                    "used_fraction": 0.347,
                    "resets_at": "2026-09-11T03:55:06+00:00",
                },
                {"kind": "5h", "used_fraction": 1.0, "resets_at": "2026-09-08T20:28:11+00:00"},
            ],
            source="session-file",
        ),
        PoolStatus("claude", "weekly", 20.0, 1.0, None),
    ]

    output = format_quota(statuses)

    assert max(len(line) for line in output.splitlines()) <= 80


def test_used_column_means_the_same_thing_for_observed_and_estimated_rows() -> None:
    """It previously held '20 / 20 units' in one row and a percentage in the next."""
    statuses = [
        PoolStatus(
            "codex",
            "5h",
            0.0,
            0.0,
            None,
            windows=[
                {"kind": "5h", "used_fraction": 0.30, "resets_at": "2026-09-08T20:28:17+00:00"}
            ],
            source="session-file",
        ),
        PoolStatus("claude", "weekly", 20.0, 5.0, None),
    ]

    output = format_quota(statuses)

    assert "30.0%" in output
    assert "25.0%" in output  # 5 of 20 units, not "15 / 20 units"
    assert "units" not in output


def test_reset_shows_how_long_until_the_window_frees_up() -> None:
    """A fixed clock, because the elapsed microseconds otherwise floor 3d 8h to 3d 7h."""
    from datetime import UTC, datetime, timedelta

    from orc.report import _format_reset

    now = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    assert _format_reset((now + timedelta(days=3, hours=8)).isoformat(), now=now) == (
        "2026-09-11 20:00Z (3d 8h)"
    )
    assert _format_reset((now + timedelta(hours=2, minutes=23)).isoformat(), now=now) == (
        "2026-09-08 14:23Z (2h 23m)"
    )
    assert _format_reset((now - timedelta(hours=1)).isoformat(), now=now).endswith("(now)")
    assert _format_reset(None) == "-"
    assert _format_reset("not-a-timestamp") == "-"


def test_the_table_carries_the_relative_reset_through(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    later = datetime.now(UTC) + timedelta(days=3, hours=8)
    output = format_quota(
        [
            PoolStatus(
                "codex",
                "weekly",
                0.0,
                0.0,
                None,
                windows=[{"kind": "weekly", "used_fraction": 0.9, "resets_at": later.isoformat()}],
                source="session-file",
            )
        ]
    )

    assert "(3d " in output


def test_format_quota_handles_no_pools() -> None:
    assert format_quota([]) == "no pools configured"


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
    assert "lazy" in output
    assert "xhigh" in output


def test_quota_table_labels_estimated_numbers_as_estimated() -> None:
    output = format_quota(
        [PoolStatus("copilot", "monthly", 20.0, 5.0, None, windows=[], source="estimated")]
    )

    assert "estimated" in output
