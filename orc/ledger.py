"""Small advisory quota ledger used by the M1 single lane."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from orc.config import PoolConfig


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
    """Advisory, self-estimated remaining budget for one pool."""

    pool_id: str
    window: str
    budget_units: float
    spent_units: float
    exhausted_until: str | None

    @property
    def remaining_units(self) -> float:
        if self.exhausted_until:
            try:
                if datetime.fromisoformat(self.exhausted_until) > datetime.now(UTC):
                    return 0.0
            except ValueError:
                pass
        return max(self.budget_units - self.spent_units, 0.0)


class Ledger:
    """Persist abstract run-credit consumption below a target repository."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def status(self, pools: dict[str, PoolConfig]) -> list[PoolStatus]:
        """Return advisory status for every configured pool, unspent pools included."""
        recorded = self._load().get("pools", {})
        statuses: list[PoolStatus] = []
        for pool_id, pool in pools.items():
            entry = recorded.get(pool_id, {})
            spent = float(entry.get("spent_units", 0.0))
            exhausted_until = entry.get("exhausted_until")
            statuses.append(
                PoolStatus(pool_id, pool.window, pool.budget_units, spent, exhausted_until)
            )
        return statuses

    def is_exhausted(self, pool_id: str, pool: PoolConfig, now: datetime | None = None) -> bool:
        """Check if a pool is currently exhausted due to budget or rate limit."""
        current = now or datetime.now(UTC)
        state = self._load()
        entry = state.get("pools", {}).get(pool_id)
        if not entry:
            return False
        exhausted_until = entry.get("exhausted_until")
        if exhausted_until:
            try:
                boundary = datetime.fromisoformat(exhausted_until)
                if boundary > current:
                    return True
                # Boundary has passed; clear expired exhaustion
                entry["exhausted_until"] = None
                self._save(state)
            except ValueError:
                pass
        spent = float(entry.get("spent_units", 0.0))
        return spent >= pool.budget_units

    def mark_exhausted(
        self, pool_id: str, pool: PoolConfig, until: datetime | None = None
    ) -> datetime:
        """Mark a pool exhausted until the next window reset boundary."""
        reset_time = until or calculate_reset_boundary(pool.window)
        state = self._load()
        pools = state.setdefault("pools", {})
        entry = pools.setdefault(
            pool_id,
            {
                "window": pool.window,
                "window_started": datetime.now(UTC).isoformat(),
                "budget_units": pool.budget_units,
                "spent_units": 0.0,
                "exhausted_until": None,
            },
        )
        entry["exhausted_until"] = reset_time.isoformat()
        self._save(state)
        return reset_time

    def record_run(self, pool_id: str, pool: PoolConfig) -> None:
        """Decrement a pool by its configured flat estimate."""
        state = self._load()
        pools = state.setdefault("pools", {})
        entry = pools.setdefault(
            pool_id,
            {
                "window": pool.window,
                "window_started": datetime.now(UTC).isoformat(),
                "budget_units": pool.budget_units,
                "spent_units": 0.0,
                "exhausted_until": None,
            },
        )
        entry["spent_units"] = float(entry["spent_units"]) + pool.flat_run_estimate
        self._save(state)

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
