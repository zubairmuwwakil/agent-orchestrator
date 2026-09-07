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
    assert (tmp_path / ".orc" / "log.jsonl").is_file()


def test_triage_heuristics() -> None:
    from orc.router import triage_failure

    plan = VerificationPlan(["pytest -q"], [], [])
    v_fail = VerificationResult(False, False, True, "failed", plan)

    # 1. Skipped tests -> lazy
    res_no_test = AgentResult("ok", "done", None, Path("/tmp/t"), 3, ["git status"])
    assert triage_failure(res_no_test, v_fail, files_edited=True, verify_attempts=1) == "lazy"

    # 2. Tool calls below floor -> lazy
    res_few_tools = AgentResult("ok", "done", None, Path("/tmp/t"), 1, ["pytest -q"])
    assert (
        triage_failure(
            res_few_tools, v_fail, files_edited=True, verify_attempts=1, min_tool_calls=2
        )
        == "lazy"
    )

    # 3. Success claimed with no diff -> lazy
    res_no_diff = AgentResult("ok", "done", None, Path("/tmp/t"), 5, ["pytest -q"])
    assert triage_failure(res_no_diff, v_fail, files_edited=False, verify_attempts=1) == "lazy"

    # 4. Genuinely iterated (verify >= 2, files edited) -> dumb
    res_iterated = AgentResult("fail", "tried", None, Path("/tmp/t"), 5, ["pytest -q"])
    assert triage_failure(res_iterated, v_fail, files_edited=True, verify_attempts=2) == "dumb"


class ReroutingFakeAdapter(AgentAdapter):
    def __init__(self, name: str, status: str = "ok") -> None:
        self.name = name
        self._status = status
        self.requests: list[AgentRequest] = []

    def available(self) -> bool:
        return True

    def run(self, req: AgentRequest) -> AgentResult:
        self.requests.append(req)
        assert req.transcript_path is not None
        req.transcript_path.write_text("transcript\n", encoding="utf-8")
        return AgentResult(self._status, "result", None, req.transcript_path, 2, ["pytest -q"])


def test_rate_limited_result_reroutes_to_other_vendor_and_marks_pool_exhausted(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    config = OrcConfig.model_validate(
        {
            "adapters": {
                "claude": {"command": "claude", "pool": "claude_pro"},
                "codex": {"command": "codex", "pool": "codex_plus"},
            },
            "pools": {
                "claude_pro": {"window": "weekly", "budget_units": 10, "flat_run_estimate": 1},
                "codex_plus": {"window": "weekly", "budget_units": 10, "flat_run_estimate": 1},
            },
            "lanes": {
                "standard": {"candidates": ["claude:sonnet@high", "codex:gpt-5.6-terra@high"]}
            },
            "ladder": {
                "order": ["standard"],
                "effort_order": ["high", "xhigh"],
                "retry_count": 2,
                "max_total_attempts": 3,
            },
        }
    )

    claude_adapter = ReroutingFakeAdapter("claude", status="rate_limited")
    codex_adapter = ReroutingFakeAdapter("codex", status="ok")

    verify_result = VerificationResult(True, False, True, "passed", VerificationPlan([], [], []))

    run = run_task(
        "solve it",
        tmp_path,
        config,
        adapters={"claude": claude_adapter, "codex": codex_adapter},
        verify_runner=lambda _target, _plan, _timeout: verify_result,
    )

    assert run.verified
    assert len(claude_adapter.requests) == 1
    assert len(codex_adapter.requests) == 1

    ledger_data = json.loads((tmp_path / ".orc" / "ledger.json").read_text(encoding="utf-8"))
    assert ledger_data["pools"]["claude_pro"]["exhausted_until"] is not None
    assert ledger_data["pools"]["codex_plus"]["spent_units"] == 1


def test_ladder_escalation_on_dumb_triage(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    config = OrcConfig.model_validate(
        {
            "adapters": {
                "claude": {"command": "claude", "pool": "claude_pro"},
                "codex": {"command": "codex", "pool": "codex_plus"},
            },
            "pools": {
                "claude_pro": {"window": "weekly", "budget_units": 10, "flat_run_estimate": 1},
                "codex_plus": {"window": "weekly", "budget_units": 10, "flat_run_estimate": 1},
            },
            "lanes": {
                "standard": {"candidates": ["claude:sonnet@high", "codex:gpt-5.6-terra@high"]}
            },
            "ladder": {
                "order": ["standard"],
                "effort_order": ["high", "xhigh"],
                "retry_count": 2,
                "max_total_attempts": 4,
            },
        }
    )

    class EditingFakeAdapter(AgentAdapter):
        def __init__(self, name: str, status: str = "ok") -> None:
            self.name = name
            self._status = status
            self.requests: list[AgentRequest] = []

        def available(self) -> bool:
            return True

        def run(self, req: AgentRequest) -> AgentResult:
            self.requests.append(req)
            # simulate editing a file
            (req.cwd / "app.py").write_text(f"# modified by {self.name}\n", encoding="utf-8")
            assert req.transcript_path is not None
            req.transcript_path.write_text("transcript\n", encoding="utf-8")
            return AgentResult(
                self._status, "attempted", None, req.transcript_path, 3, ["pytest -q"]
            )

    claude_adapter = EditingFakeAdapter("claude")
    codex_adapter = EditingFakeAdapter("codex")

    plan = VerificationPlan(["pytest -q"], [], [])
    # 2 failures for claude, then pass for codex
    results = iter(
        [
            VerificationResult(False, False, True, "failed 1", plan),
            VerificationResult(False, False, True, "failed 2", plan),
            VerificationResult(True, False, True, "passed", plan),
        ]
    )

    run = run_task(
        "escalate on dumb",
        tmp_path,
        config,
        adapters={"claude": claude_adapter, "codex": codex_adapter},
        verify_runner=lambda _target, _plan, _timeout: next(results),
    )

    assert run.verified
    # Claude was triaged lazy first (effort high -> xhigh), then dumb on attempt 2 (iterated twice with file edits)
    assert len(claude_adapter.requests) == 2
    # Escalated to Codex
    assert len(codex_adapter.requests) == 1


def test_rescue_output_on_unverified_task() -> None:
    from orc.report import format_run
    from orc.router import Attempt, TaskRun

    run = TaskRun(
        task_id="abc123",
        branch="orc/test-abc123",
        run_dir=Path("/tmp"),
        attempts=[
            Attempt(
                1,
                "high",
                AgentResult("fail", "err", None, Path("/tmp"), 1, []),
                VerificationResult(False, False, True, "failed", VerificationPlan([], [], [])),
            )
        ],
        diff="",
    )
    output = format_run(run)
    assert "⚠ Task did not pass independent verification." in output
    assert "git switch orc/test-abc123" in output
