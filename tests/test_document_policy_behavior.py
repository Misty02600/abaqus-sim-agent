from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.evaluation.document_policy_behavior import (
    BehaviorBatch,
    BehaviorCase,
    ControlledEvidencePort,
    InjectedResumeInterruption,
    RecordingModel,
    caused_by,
)
from abaqus_sim_agent.retrieval.evidence_contracts import QuestionQuery, SearchRequest
from tests.test_runtime_evidence_service import BINDING, evidence


def case(**updates):
    values = {
        "id": "case-test",
        "goal": "test",
        "seed_files": {},
        "needs": (),
        "responses": (("units",), ("angles",)),
        "expected_lookups": 2,
        "expected_status": "completed",
        "review_criteria": ("test",),
    }
    return BehaviorCase(**(values | updates))


def query():
    return SearchRequest(
        query=QuestionQuery(question="units"), constraints_origin="host_typed"
    )


def test_frozen_eight_scenarios_cover_the_planned_branches():
    path = (
        Path(__file__).parents[1]
        / "benchmarks/coding/document_policy_behavior_cases_v1.json"
    )
    batch = BehaviorBatch.model_validate_json(path.read_bytes())
    assert len(batch.cases) == 8
    assert sum(c.inject_resume for c in batch.cases) == 1
    assert sum(c.physical_input for c in batch.cases) == 1
    partial = next(c for c in batch.cases if c.id == "partial-followup")
    stopped = next(c for c in batch.cases if c.id == "unresolved-stop")
    assert partial.goal == stopped.goal
    assert partial.responses == (("units",), ("angles",))
    assert stopped.responses == (("units",), ("units",))


@pytest.mark.asyncio
async def test_controlled_delivery_preserves_units_and_does_not_rank_query(tmp_path):
    a = evidence()
    b = a.model_copy(update={"id": "b" * 64})
    port = ControlledEvidencePort(
        case(),
        SimpleNamespace(evidence_binding=BINDING),
        {"units": a, "angles": b},
        lambda text: len(text.split()),
        tmp_path,
    )
    first = await port.search(query(), search_id="one", deadline=float("inf"))
    second = await port.search(query(), search_id="two", deadline=float("inf"))
    assert first.evidence == (a,)
    assert second.evidence == (b,)
    assert first.cost.scored_count == 0
    assert "not live retrieval ranking" in first.message
    assert port.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "status"),
    [("missing_release", "not_found"), ("technical_failure", "unavailable")],
)
async def test_injected_technical_or_release_failure_never_has_partial_evidence(
    tmp_path, kind, status
):
    port = ControlledEvidencePort(
        case(response_kind=kind),
        SimpleNamespace(evidence_binding=BINDING),
        {},
        lambda s: 2,
        tmp_path,
    )
    result = await port.search(query(), search_id="one", deadline=float("inf"))
    assert result.status == status
    assert result.evidence == ()


def test_seed_path_cannot_escape_workspace():
    with pytest.raises(ValueError, match="plain seed"):
        case(seed_files={"../escape.txt": "no"})


def test_case_unknown_settings_are_rejected():
    with pytest.raises(ValidationError):
        case(http_retries=10)


@pytest.mark.asyncio
async def test_resume_injection_does_not_make_a_provider_call(tmp_path):
    (tmp_path / "document_policy.jsonl").write_text(
        '{"event":{"kind":"evidence_received"}}\n'
    )
    called = []
    raw = FunctionModel(
        lambda m, i: called.append(1) or ModelResponse(parts=[TextPart("ok")])
    )
    wrapped = RecordingModel(raw, tmp_path, True)
    with pytest.raises(InjectedResumeInterruption):
        await wrapped.request([], None, None)
    assert called == []
    assert wrapped.requests == 0
    assert wrapped.injected
    assert (tmp_path / "injected_resume.json").exists()


def test_only_explicit_injected_failure_enables_same_run_continuation():
    outer = RuntimeError("outer")
    outer.__cause__ = InjectedResumeInterruption("test")
    assert caused_by(outer, "InjectedResumeInterruption")
    assert not caused_by(
        ConnectionError("provider failure"), "InjectedResumeInterruption"
    )
