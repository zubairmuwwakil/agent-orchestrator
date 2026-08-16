"""Small advisory quota ledger used by the M1 single lane."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orc.config import PoolConfig


class Ledger:
    """Persist abstract run-credit consumption below a target repository."""

    def __init__(self, path: Path) -> None:
        self._path = path

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
