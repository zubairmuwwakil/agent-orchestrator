from pathlib import Path

from orc.config import PoolConfig
from orc.ledger import Ledger


def _pool(budget_units: float = 20.0, flat_run_estimate: float = 1.0) -> PoolConfig:
    return PoolConfig.model_validate(
        {"window": "weekly", "budget_units": budget_units, "flat_run_estimate": flat_run_estimate}
    )


def test_status_defaults_unspent_pool_to_full_budget(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")

    statuses = ledger.status({"claude_pro": _pool()})

    assert len(statuses) == 1
    status = statuses[0]
    assert status.pool_id == "claude_pro"
    assert status.window == "weekly"
    assert status.spent_units == 0.0
    assert status.remaining_units == 20.0
    assert status.exhausted_until is None


def test_status_reflects_recorded_spend(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")
    pool = _pool(budget_units=5.0, flat_run_estimate=1.0)

    ledger.record_run("claude_pro", pool)
    ledger.record_run("claude_pro", pool)

    status = ledger.status({"claude_pro": pool})[0]
    assert status.spent_units == 2.0
    assert status.remaining_units == 3.0


def test_status_never_reports_negative_remaining_when_overspent(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")
    pool = _pool(budget_units=1.0, flat_run_estimate=5.0)

    ledger.record_run("claude_pro", pool)

    status = ledger.status({"claude_pro": pool})[0]
    assert status.remaining_units == 0.0


def test_status_covers_every_configured_pool_even_when_ledger_is_empty(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")

    statuses = ledger.status({"claude_pro": _pool(), "codex_plus": _pool(budget_units=10.0)})

    assert {status.pool_id for status in statuses} == {"claude_pro", "codex_plus"}


def test_mark_exhausted_sets_boundary_and_is_exhausted(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")
    pool = _pool()

    assert not ledger.is_exhausted("claude_pro", pool)

    reset_time = ledger.mark_exhausted("claude_pro", pool)
    assert ledger.is_exhausted("claude_pro", pool)

    status = ledger.status({"claude_pro": pool})[0]
    assert status.exhausted_until == reset_time.isoformat()
    assert status.remaining_units == 0.0


def test_is_exhausted_clears_expired_boundary(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    ledger = Ledger(tmp_path / ".orc" / "ledger.json")
    pool = _pool()

    past_time = datetime.now(UTC) - timedelta(hours=1)
    ledger.mark_exhausted("claude_pro", pool, until=past_time)

    assert not ledger.is_exhausted("claude_pro", pool)
    status = ledger.status({"claude_pro": pool})[0]
    assert status.exhausted_until is None
