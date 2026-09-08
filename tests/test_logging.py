import hashlib
import json
from pathlib import Path

from orc.router import run_task
from orc.verify import VerificationPlan, VerificationResult
from tests.test_router import FakeAdapter, _config, _init_repo


def test_log_records_the_hash_and_never_the_prompt(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    task = "fix the flaky auth test in the payments service"
    verify_ok = VerificationResult(True, False, True, "ok", VerificationPlan([], [], []))

    run_task(task, tmp_path, _config(), FakeAdapter(), verify_runner=lambda _t, _p, _to: verify_ok)

    line = (tmp_path / ".orc" / "log.jsonl").read_text(encoding="utf-8").strip()
    entry = json.loads(line)

    assert "task" not in entry
    assert task not in line
    assert entry["prompt_hash"] == hashlib.sha256(task.encode("utf-8")).hexdigest()
    assert entry["start_lane"] == "standard"
    assert entry["outcome"] == "verified"
    assert isinstance(entry["wall_s"], float)
    assert entry["attempts"][0].keys() >= {"vendor", "model", "effort", "triage", "verify"}


def test_the_full_prompt_is_still_available_in_the_run_directory(tmp_path: Path) -> None:
    """§13 excludes prompts from the log because §10 already persists them."""
    _init_repo(tmp_path)
    task = "fix the flaky auth test"
    verify_ok = VerificationResult(True, False, True, "ok", VerificationPlan([], [], []))

    run = run_task(
        task, tmp_path, _config(), FakeAdapter(), verify_runner=lambda _t, _p, _to: verify_ok
    )

    assert task in (run.run_dir / "prompt-1.txt").read_text(encoding="utf-8")
