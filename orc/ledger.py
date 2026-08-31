"""Small advisory quota ledger used by the M1 single lane."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orc.config import PoolConfig


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

    def record_run(self, pool_id: str, pool: PoolConfig) -> None:
        """Decrement a pool by its configured flat M1 estimate."""
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
