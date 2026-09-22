import hashlib
import json
from pathlib import Path

import pytest
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelResponse, ToolCallPart

from abaqus_sim_agent.evaluation.structural_topic_skill_routing import (
    CAPABILITY_IDS,
    DEFAULT_CASES,
    DEFAULT_SEAL,
    load_cases,
    observe_case,
)


def test_structural_topic_routing_cases_are_frozen_and_cover_each_entry_point() -> None:
    cases = load_cases()
    seal = json.loads(DEFAULT_SEAL.read_text(encoding="utf-8"))

    assert len(cases) == 13
    assert hashlib.sha256(DEFAULT_CASES.read_bytes()).hexdigest() == seal["sha256"]
    assert tuple(seal["capability_ids"]) == CAPABILITY_IDS
    required = {skill for case in cases for skill in case.required_loads}
    assert required == set(CAPABILITY_IDS)
    assert (
        next(case for case in cases if case.case_id == "local-name-only").required_loads
        == ()
    )
    assert all(case.required_loads == case.allowed_loads for case in cases)


def test_structural_topic_routing_seal_rejects_case_mutation(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_bytes(DEFAULT_CASES.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="frozen seal"):
        load_cases(cases_path, DEFAULT_SEAL)


def test_structural_topic_observer_records_exact_and_duplicate_routing(
    tmp_path: Path,
) -> None:
    case = next(case for case in load_cases() if case.case_id == "contact-definition")
    state = tmp_path / case.case_id / "state"
    workspace = state.parent / "workspace"
    state.mkdir(parents=True)
    workspace.mkdir()
    (state / "history.json").write_bytes(
        ModelMessagesTypeAdapter.dump_json(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart("load_capability", {"id": "contact-fea"}),
                        ToolCallPart("load_capability", {"id": "contact-fea"}),
                    ]
                )
            ]
        )
    )

    observed = observe_case(tmp_path, case)

    assert observed["required_load_recall"] == 1.0
    assert observed["forbidden_load_count"] == 0
    assert observed["load_call_count"] == 2
    assert observed["duplicate_load_call_count"] == 1
    assert observed["loaded_skill_body_bytes"] > 0
    assert observed["exact_route_pass"] is True
    assert observed["runtime_requested"] is False


def test_structural_topic_observer_marks_overrouting(tmp_path: Path) -> None:
    case = next(case for case in load_cases() if case.case_id == "contact-diagnostics")
    state = tmp_path / case.case_id / "state"
    workspace = state.parent / "workspace"
    state.mkdir(parents=True)
    workspace.mkdir()
    (state / "history.json").write_bytes(
        ModelMessagesTypeAdapter.dump_json(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            "load_capability", {"id": "contact-diagnostics-fea"}
                        ),
                        ToolCallPart("load_capability", {"id": "contact-fea"}),
                    ]
                )
            ]
        )
    )

    observed = observe_case(tmp_path, case)

    assert observed["required_load_recall"] == 1.0
    assert observed["forbidden_load_count"] == 1
    assert observed["unexpected_loads"] == ["contact-fea"]
    assert observed["exact_route_pass"] is False


def test_structural_topic_negative_case_passes_with_zero_loads(tmp_path: Path) -> None:
    case = next(case for case in load_cases() if case.case_id == "local-name-only")
    state = tmp_path / case.case_id / "state"
    workspace = state.parent / "workspace"
    state.mkdir(parents=True)
    workspace.mkdir()

    observed = observe_case(tmp_path, case)

    assert observed["required_load_recall"] == 1.0
    assert observed["load_call_count"] == 0
    assert observed["loaded_skill_body_bytes"] == 0
    assert observed["exact_route_pass"] is True
