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
    assert "19" in output
    assert "(estimated)" in output
    assert "codex" in output
    assert "2026-08-20T00:00:00+00:00" in output


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
