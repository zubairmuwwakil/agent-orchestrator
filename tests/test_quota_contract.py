from datetime import UTC, datetime

from orc.adapters.base import (
    AgentAdapter,
    AgentRequest,
    AgentResult,
    QuotaObservation,
    QuotaWindow,
    window_kind_from_minutes,
)


def test_window_kind_is_derived_from_the_vendors_window_length() -> None:
    # codex reports raw minutes: 300 for its short window, 10080 for its weekly one.
    assert window_kind_from_minutes(300) == "5h"
    assert window_kind_from_minutes(10080) == "weekly"
    assert window_kind_from_minutes(44640) == "monthly"


def test_agent_result_defaults_quota_to_none_so_old_call_sites_still_build() -> None:
    result = AgentResult("ok", "done", None, __import__("pathlib").Path("/tmp/t"), 1, [])
    assert result.quota is None


def test_an_adapter_without_telemetry_reports_none() -> None:
    class Bare(AgentAdapter):
        name = "bare"

        def available(self) -> bool:
            return True

        def run(self, req: AgentRequest) -> AgentResult:  # pragma: no cover - unused
            raise NotImplementedError

    assert Bare().quota_probe() is None


def test_observation_holds_several_simultaneous_windows() -> None:
    now = datetime.now(UTC)
    observation = QuotaObservation(
        windows=[
            QuotaWindow("5h", 0.39, now),
            QuotaWindow("weekly", 0.38, now),
        ],
        observed_at=now,
        source="session-file",
    )
    assert {window.kind for window in observation.windows} == {"5h", "weekly"}
