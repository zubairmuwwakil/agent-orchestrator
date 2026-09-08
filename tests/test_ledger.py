import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orc.adapters.base import QuotaObservation, QuotaWindow
from orc.config import PoolConfig
from orc.ledger import Ledger, calculate_reset_boundary


def _pool(budget_units: float = 20.0, flat_run_estimate: float = 1.0) -> PoolConfig:
    return PoolConfig.model_validate(
        {
            "windows": ["weekly"],
            "budget_units": budget_units,
            "flat_run_estimate": flat_run_estimate,
        }
    )


def test_status_defaults_unspent_pool_to_full_budget(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")

    statuses = ledger.status({"claude": _pool()})

    assert len(statuses) == 1
    status = statuses[0]
    assert status.pool_id == "claude"
    assert status.window == "weekly"
    assert status.spent_units == 0.0
    assert status.remaining_units == 20.0
    assert status.exhausted_until is None


def test_status_reflects_recorded_spend(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")
    pool = _pool(budget_units=5.0, flat_run_estimate=1.0)

    ledger.record_run("claude", pool)
    ledger.record_run("claude", pool)

    status = ledger.status({"claude": pool})[0]
    assert status.spent_units == 2.0
    assert status.remaining_units == 3.0


def test_status_never_reports_negative_remaining_when_overspent(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")
    pool = _pool(budget_units=1.0, flat_run_estimate=5.0)

    ledger.record_run("claude", pool)

    status = ledger.status({"claude": pool})[0]
    assert status.remaining_units == 0.0


def test_status_covers_every_configured_pool_even_when_ledger_is_empty(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")

    statuses = ledger.status({"claude": _pool(), "codex": _pool(budget_units=10.0)})

    assert {status.pool_id for status in statuses} == {"claude", "codex"}


def test_mark_exhausted_sets_boundary_and_is_exhausted(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")
    pool = _pool()

    assert not ledger.is_exhausted("claude", pool)

    reset_time = ledger.mark_exhausted("claude", pool)
    assert ledger.is_exhausted("claude", pool)

    status = ledger.status({"claude": pool})[0]
    assert status.exhausted_until == reset_time.isoformat()
    assert status.remaining_units == 0.0


def test_is_exhausted_clears_expired_boundary(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / ".orc" / "ledger.json")
    pool = _pool()

    past_time = datetime.now(UTC) - timedelta(hours=1)
    ledger.mark_exhausted("claude", pool, until=past_time)

    assert not ledger.is_exhausted("claude", pool)
    status = ledger.status({"claude": pool})[0]
    assert status.exhausted_until is None


def _telemetry_pool(reserve: float = 0.15) -> PoolConfig:
    return PoolConfig.model_validate(
        {
            "windows": ["5h", "weekly"],
            "budget_units": 20,
            "flat_run_estimate": 1,
            "reserve_fraction": reserve,
        }
    )


def _observation(used_5h: float, used_week: float) -> QuotaObservation:
    later = datetime.now(UTC) + timedelta(hours=3)
    return QuotaObservation(
        windows=[QuotaWindow("5h", used_5h, later), QuotaWindow("weekly", used_week, later)],
        observed_at=datetime.now(UTC),
        source="session-file",
    )


def test_a_pool_below_the_reserve_line_is_eligible(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    pool = _telemetry_pool()
    ledger.record_observation("codex", _observation(0.39, 0.38))

    assert ledger.eligibility("codex", pool).ok


def test_any_window_over_the_reserve_line_blocks_the_pool(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    pool = _telemetry_pool()
    # Weekly is healthy; the 5h window is not. One bad window is enough.
    ledger.record_observation("codex", _observation(0.90, 0.20))

    verdict = ledger.eligibility("codex", pool)
    assert not verdict.ok
    assert verdict.reason == "reserve"
    assert verdict.blocked_window == "5h"
    assert verdict.resets_at is not None


def test_use_reserve_spends_into_the_headroom_but_not_past_full(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    pool = _telemetry_pool()
    ledger.record_observation("codex", _observation(0.90, 0.20))
    assert ledger.eligibility("codex", pool, use_reserve=True).ok

    ledger.record_observation("codex", _observation(1.0, 0.20))
    assert not ledger.eligibility("codex", pool, use_reserve=True).ok


def test_a_stale_window_no_longer_blocks_once_it_has_reset(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    pool = _telemetry_pool()
    past = datetime.now(UTC) - timedelta(hours=1)
    ledger.record_observation(
        "codex",
        QuotaObservation([QuotaWindow("5h", 0.99, past)], datetime.now(UTC), "session-file"),
    )

    assert ledger.eligibility("codex", pool).ok


def test_without_telemetry_the_estimate_still_applies(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.json")
    pool = PoolConfig.model_validate(
        {"windows": ["weekly"], "budget_units": 2, "flat_run_estimate": 1}
    )
    ledger.record_run("copilot", pool)
    assert ledger.eligibility("copilot", pool).ok
    ledger.record_run("copilot", pool)

    verdict = ledger.eligibility("copilot", pool)
    assert not verdict.ok
    assert verdict.reason == "budget"


def test_estimated_spend_resets_when_the_window_rolls_over(tmp_path: Path) -> None:
    """D7: window_started was written and never read, so spend accrued forever."""
    ledger = Ledger(tmp_path / "ledger.json")
    pool = PoolConfig.model_validate(
        {"windows": ["5h"], "budget_units": 2, "flat_run_estimate": 1}
    )
    ledger.record_run("copilot", pool)
    ledger.record_run("copilot", pool)
    assert not ledger.eligibility("copilot", pool).ok

    later = datetime.now(UTC) + timedelta(hours=6)
    assert ledger.eligibility("copilot", pool, now=later).ok


def test_eligibility_does_not_crash_on_a_naive_persisted_timestamp(tmp_path: Path) -> None:
    """A hand-edited or externally-written ledger must degrade, not raise TypeError."""
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps(
            {
                "pools": {
                    "codex": {
                        "source": "session-file",
                        "windows": [
                            {
                                "kind": "5h",
                                "used_fraction": 0.99,
                                "resets_at": "2999-01-01T00:00:00",  # naive: no offset
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    verdict = Ledger(path).eligibility("codex", _telemetry_pool())

    assert not verdict.ok
    assert verdict.reason == "reserve"
    assert verdict.blocked_window == "5h"


def test_estimate_resets_when_window_started_is_missing(tmp_path: Path) -> None:
    """D7 hardening: a corrupt estimate entry with no window_started must not stay stuck."""
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps({"pools": {"copilot": {"spent_units": 99.0}}}), encoding="utf-8"
    )
    pool = PoolConfig.model_validate(
        {"windows": ["5h"], "budget_units": 2, "flat_run_estimate": 1}
    )

    assert Ledger(path).eligibility("copilot", pool).ok


def test_a_capped_telemetry_window_with_an_unusable_reset_time_blocks(tmp_path: Path) -> None:
    """A window we cannot prove has reset must fail closed, not silently unblock a maxed pool."""
    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps(
            {
                "pools": {
                    "codex": {
                        "source": "session-file",
                        "windows": [
                            {"kind": "weekly", "used_fraction": 0.97, "resets_at": "garbage"}
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    verdict = Ledger(path).eligibility("codex", _telemetry_pool())

    assert not verdict.ok
    assert verdict.reason == "reserve"
    assert verdict.blocked_window == "weekly"


def test_rate_limit_exhaustion_covers_the_longest_configured_window(tmp_path: Path) -> None:
    """A generic rate-limit error carries no boundary; block through the longest window,
    never the shortest — a weekly cap must not clear in five hours."""
    ledger = Ledger(tmp_path / "ledger.json")
    pool = PoolConfig.model_validate(
        {"windows": ["5h", "weekly"], "budget_units": 5, "flat_run_estimate": 1}
    )

    reset_time = ledger.mark_exhausted("codex", pool)

    assert reset_time == calculate_reset_boundary("weekly")
