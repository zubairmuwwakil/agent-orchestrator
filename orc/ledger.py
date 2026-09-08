"""Quota ledger: telemetry-driven eligibility with an estimate fallback (SPEC §7)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from orc.adapters.base import QuotaObservation
from orc.config import PoolConfig

_WINDOW_SECONDS = {"5h": 5 * 3600, "weekly": 7 * 86400, "monthly": 30 * 86400}


def _as_utc(value: datetime) -> datetime:
    """Assume a naive datetime is already UTC; leave an aware one untouched."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _parse_iso(raw: object) -> datetime | None:
    """Parse a persisted ISO timestamp, or return None if it is not usable.

    None is returned for non-strings, unparseable values, and — deliberately —
    timezone-naive values: a naive timestamp names no unambiguous instant, so a
    caller must not treat it as authoritative (comparing it with an aware `now`
    also raises TypeError). Every timestamp orc writes is aware UTC; see
    `record_observation` and `mark_exhausted`. Callers fail closed on None.
    """
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def calculate_reset_boundary(window: str, now: datetime | None = None) -> datetime:
    """Calculate the next boundary when a pool resets."""
    current = now or datetime.now(UTC)
    if window == "5h":
        return current + timedelta(hours=5)
    if window == "weekly":
        days = 7 - current.weekday()
        if days == 0:
            days = 7
        next_date = (current + timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return next_date
    if window == "monthly":
        year = current.year + 1 if current.month == 12 else current.year
        month = 1 if current.month == 12 else current.month + 1
        return datetime(year, month, 1, 0, 0, 0, tzinfo=UTC)
    return current + timedelta(hours=24)


@dataclass(slots=True)
class PoolStatus:
    """Remaining budget for one pool, from telemetry where present, else estimated."""

    pool_id: str
    window: str
    budget_units: float
    spent_units: float
    exhausted_until: str | None
    windows: list[dict[str, Any]] = field(default_factory=list)
    source: str = "estimated"

    @property
    def remaining_units(self) -> float:
        boundary = _parse_iso(self.exhausted_until)
        if boundary is not None and boundary > datetime.now(UTC):
            return 0.0
        return max(self.budget_units - self.spent_units, 0.0)


@dataclass(slots=True)
class Eligibility:
    """Why a pool may or may not be spent right now."""

    ok: bool
    reason: str  # "", "reserve", "budget", or "rate_limited"
    blocked_window: str | None = None
    resets_at: datetime | None = None
    source: str = "estimated"


class Ledger:
    """Persist normalized quota readings and abstract run-credit spend below a target."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def status(self, pools: dict[str, PoolConfig]) -> list[PoolStatus]:
        """Return status for every configured pool, unspent pools included."""
        recorded = self._load().get("pools", {})
        statuses: list[PoolStatus] = []
        for pool_id, pool in pools.items():
            entry = recorded.get(pool_id, {})
            spent = float(entry.get("spent_units", 0.0))
            exhausted_until = entry.get("exhausted_until")
            windows = entry.get("windows")
            if not isinstance(windows, list):
                windows = []
            source = str(entry.get("source", "estimated"))
            statuses.append(
                PoolStatus(
                    pool_id,
                    pool.windows[0],
                    pool.budget_units,
                    spent,
                    exhausted_until,
                    windows,
                    source,
                )
            )
        return statuses

    def record_observation(self, pool_id: str, observation: QuotaObservation) -> None:
        """Store a normalized reading. Telemetry supersedes the estimate for this pool.

        Timestamps are persisted as aware UTC; a naive value from an adapter is assumed
        UTC here, so the read path can trust every persisted timestamp it parses.
        """
        state = self._load()
        entry = state.setdefault("pools", {}).setdefault(pool_id, {})
        entry["observed_at"] = _as_utc(observation.observed_at).isoformat()
        entry["source"] = observation.source
        entry["windows"] = [
            {
                "kind": window.kind,
                "used_fraction": window.used_fraction,
                "resets_at": _as_utc(window.resets_at).isoformat(),
            }
            for window in observation.windows
        ]
        self._save(state)

    def eligibility(
        self,
        pool_id: str,
        pool: PoolConfig,
        use_reserve: bool = False,
        now: datetime | None = None,
    ) -> Eligibility:
        """Decide before spending a run. Telemetry is authoritative; estimates are fallback."""
        current = now or datetime.now(UTC)
        entry = self._load().get("pools", {}).get(pool_id) or {}

        boundary = _parse_iso(entry.get("exhausted_until"))
        if boundary is not None and boundary > current:
            return Eligibility(False, "rate_limited", None, boundary, "observed")

        windows = entry.get("windows")
        if isinstance(windows, list) and windows:
            # `--use-reserve` spends the headroom, never past a real vendor limit.
            ceiling = 1.0 if use_reserve else 1.0 - pool.reserve_fraction
            for window in windows:
                if not isinstance(window, dict):
                    continue
                resets_at = _parse_iso(window.get("resets_at"))
                if resets_at is not None and resets_at <= current:
                    continue  # this window has provably rolled over; the reading is stale
                # An unparseable reset time is not proof of a reset: fall through and
                # let the ceiling decide, so a maxed window still blocks (fails closed).
                used = float(window.get("used_fraction", 0.0))
                if used >= ceiling:
                    return Eligibility(
                        False,
                        "reserve",
                        str(window.get("kind")),
                        resets_at,
                        str(entry.get("source", "observed")),
                    )
            return Eligibility(True, "", None, None, str(entry.get("source", "observed")))

        spent = self._spent_after_window_reset(entry, pool, current)
        if spent >= pool.budget_units:
            return Eligibility(False, "budget", pool.windows[0], None, "estimated")
        return Eligibility(True, "", None, None, "estimated")

    def is_exhausted(self, pool_id: str, pool: PoolConfig, now: datetime | None = None) -> bool:
        """Check if a pool is currently exhausted due to budget or rate limit."""
        current = now or datetime.now(UTC)
        state = self._load()
        entry = state.get("pools", {}).get(pool_id)
        if not entry:
            return False
        boundary = _parse_iso(entry.get("exhausted_until"))
        if boundary is not None:
            if boundary > current:
                return True
            # Boundary has passed; clear expired exhaustion
            entry["exhausted_until"] = None
            self._save(state)
        spent = float(entry.get("spent_units", 0.0))
        return spent >= pool.budget_units

    def mark_exhausted(
        self, pool_id: str, pool: PoolConfig, until: datetime | None = None
    ) -> datetime:
        """Mark a pool exhausted until a reset boundary.

        A generic rate-limit error names no window, so without an explicit `until` this
        blocks through the *longest* configured window: a weekly cap must not be cleared
        after five hours just because a 5h window is also configured.
        """
        longest = max(pool.windows, key=lambda kind: _WINDOW_SECONDS[kind])
        reset_time = until or calculate_reset_boundary(longest)
        state = self._load()
        pools = state.setdefault("pools", {})
        entry = pools.setdefault(pool_id, self._fresh_entry(pool))
        entry["exhausted_until"] = reset_time.isoformat()
        self._save(state)
        return reset_time

    def record_run(self, pool_id: str, pool: PoolConfig) -> None:
        """Decrement a pool by its configured flat estimate, resetting a rolled window."""
        state = self._load()
        pools = state.setdefault("pools", {})
        entry = pools.setdefault(pool_id, self._fresh_entry(pool))
        spent = self._spent_after_window_reset(entry, pool, datetime.now(UTC))
        if spent == 0.0:
            entry["window_started"] = datetime.now(UTC).isoformat()
        entry["spent_units"] = spent + pool.flat_run_estimate
        self._save(state)

    def _fresh_entry(self, pool: PoolConfig) -> dict[str, Any]:
        return {
            "window": pool.windows[0],
            "window_started": datetime.now(UTC).isoformat(),
            "budget_units": pool.budget_units,
            "spent_units": 0.0,
            "exhausted_until": None,
        }

    def _spent_after_window_reset(
        self, entry: dict[str, Any], pool: PoolConfig, current: datetime
    ) -> float:
        """Zero the estimate once the shortest configured window has rolled over.

        An unknown window start is also treated as rolled over: a persisted estimate
        without a valid `window_started` must not stay exhausted forever (D7).
        """
        started = _parse_iso(entry.get("window_started"))
        if started is None:
            return 0.0
        shortest = min(_WINDOW_SECONDS[window] for window in pool.windows)
        if (current - started).total_seconds() >= shortest:
            return 0.0
        return float(entry.get("spent_units", 0.0))

    def _save(self, state: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"pools": {}}
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"pools": {}}
        return loaded if isinstance(loaded, dict) else {"pools": {}}
