import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_every_configured_pool_exists_in_fleet() -> None:
    config = tomllib.loads((ROOT / "orc.toml").read_text(encoding="utf-8"))
    fleet = (ROOT / "FLEET.md").read_text(encoding="utf-8")
    roster_pool_ids = set(re.findall(r"^\| `([^`]+)` \|", fleet, flags=re.MULTILINE))

    missing = set(config.get("pools", {})) - roster_pool_ids

    assert not missing, f"configured pools missing from FLEET.md: {sorted(missing)}"
