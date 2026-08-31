from orc.ledger import PoolStatus
from orc.report import format_quota


def test_format_quota_lists_each_pool_with_estimated_remaining() -> None:
    statuses = [
        PoolStatus("claude_pro", "weekly", 20.0, 1.0, None),
        PoolStatus("codex_plus", "5h", 10.0, 10.0, "2026-08-20T00:00:00+00:00"),
    ]

    output = format_quota(statuses)

    assert "claude_pro" in output
    assert "weekly" in output
    assert "19" in output
    assert "(estimated)" in output
    assert "codex_plus" in output
    assert "2026-08-20T00:00:00+00:00" in output


def test_format_quota_handles_no_pools() -> None:
    assert format_quota([]) == "no pools configured"
