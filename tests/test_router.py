import json
import subprocess
from pathlib import Path

from orc.adapters.base import AgentAdapter, AgentRequest, AgentResult
from orc.config import OrcConfig
from orc.router import run_task
from orc.verify import VerificationPlan, VerificationResult


class FakeAdapter(AgentAdapter):
    name = "claude"

    def __init__(self) -> None:
        self.requests: list[AgentRequest] = []

    def available(self) -> bool:
        return True

    def run(self, req: AgentRequest) -> AgentResult:
        self.requests.append(req)
        assert req.transcript_path is not None
        req.transcript_path.write_text("fake transcript\n", encoding="utf-8")
        return AgentResult("ok", "I made a change", None, req.transcript_path, None, [])


def _config() -> OrcConfig:
    return OrcConfig.model_validate(
        {
            "adapters": {"claude": {"command": "claude"}},
            "pools": {
                "claude_pro": {"window": "weekly", "budget_units": 5, "flat_run_estimate": 1}
            },
            "lanes": {"standard": {"candidates": ["claude:sonnet-5@high"]}},
            "ladder": {"effort_order": ["low", "medium", "high", "xhigh"], "retry_count": 2},
        }
    )


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitignore").write_text(".orc/\n", encoding="utf-8")
    (path / "app.py").write_text("answer = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)


def test_retry_includes_exact_failure_feedback_and_bumps_effort(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    fake = FakeAdapter()
    results = iter(
        [
            VerificationResult(
                False,
                False,
                True,
                "$ pytest -q\nE       assert 1 == 2",
                VerificationPlan(["pytest -q"], [], []),
            ),
            VerificationResult(
                True, False, True, "$ pytest -q\n1 passed", VerificationPlan([], [], [])
            ),
        ]
    )

    run = run_task(
        "make the tests pass",
        tmp_path,
        _config(),
        fake,
        verify_runner=lambda _target, _plan, _timeout: next(results),
    )

    assert run.verified
    assert [request.effort for request in fake.requests] == ["high", "xhigh"]
    assert "Exact output follows" in fake.requests[1].prompt
    assert "assert 1 == 2" in fake.requests[1].prompt
    ledger = json.loads((tmp_path / ".orc" / "ledger.json").read_text(encoding="utf-8"))
    assert ledger["pools"]["claude_pro"]["spent_units"] == 2
