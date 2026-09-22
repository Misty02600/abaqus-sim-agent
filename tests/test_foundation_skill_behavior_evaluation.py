import json
from pathlib import Path

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelResponse, ToolCallPart

from abaqus_sim_agent.evaluation.foundation_skill_behavior import (
    DEFAULT_RUBRIC,
    load_rubrics,
    observe_case,
)
from abaqus_sim_agent.evaluation.skill_routing_behavior import load_cases


def test_behavior_rubric_covers_frozen_cases_without_route_as_correctness() -> None:
    cases = load_cases()
    rubrics = load_rubrics()

    assert set(rubrics) == {case.case_id for case in cases}
    assert all(rubric.criteria for rubric in rubrics.values())
    assert all(
        criterion.id not in {"required_loads", "exact_route_pass"}
        for rubric in rubrics.values()
        for criterion in rubric.criteria
    )
    raw = json.loads(DEFAULT_RUBRIC.read_bytes())
    assert raw["task_source"] == "benchmarks/coding/skill_routing_cases_v1.jsonl"


def test_missing_input_behavior_accepts_appropriate_stop() -> None:
    rubric = load_rubrics()["missing-user-physical-input"]
    assert rubric.accepted_completions == ("appropriately_stopped",)
    assert {criterion.id for criterion in rubric.criteria} == {
        "identify_thickness_dependency",
        "no_fabricated_thickness",
        "stop_before_dependent_work",
    }


def test_observer_keeps_zero_skill_load_semantically_ungraded(tmp_path: Path) -> None:
    case = next(
        case for case in load_cases() if case.case_id == "existing-property-load-edit"
    )
    state = tmp_path / case.case_id / "state"
    workspace = state.parent / "workspace"
    state.mkdir(parents=True)
    workspace.mkdir()
    (state / "history.json").write_bytes(
        ModelMessagesTypeAdapter.dump_json(
            [
                ModelResponse(
                    parts=[ToolCallPart("read_file", {"path": "model_contract.txt"})]
                )
            ]
        )
    )
    (workspace / "analysis.json").write_text(
        json.dumps(
            {
                "summary": "Only the requested section and load change.",
                "findings": ["Preserve the accepted beam model."],
                "next_action": "ready_for_verification",
            }
        ),
        encoding="utf-8",
    )

    observed = observe_case(tmp_path, case)

    assert observed["load_sequence"] == []
    assert observed["behavior_review_status"] == "pending_host_semantic_review"
    assert "behavior_pass" not in observed
    assert "exact_route_pass" not in observed
    assert "required_load_recall" not in observed


def test_observer_records_skill_context_proxy_without_using_it_as_grade(
    tmp_path: Path,
) -> None:
    case = next(case for case in load_cases() if case.case_id == "element-mesh-review")
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
                            "load_capability",
                            {"id": "linear-structural-fea-discretization"},
                        )
                    ]
                )
            ]
        )
    )

    observed = observe_case(tmp_path, case)

    assert observed["loaded_skill_body_bytes"] > 0
    assert observed["load_call_count"] == 1
    assert observed["behavior_review_status"] == "pending_host_semantic_review"
