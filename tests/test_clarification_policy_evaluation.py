import json
from pathlib import Path

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelResponse, ToolCallPart

from abaqus_sim_agent.coding.contracts import CLARIFICATION_POLICY_VERSION
from abaqus_sim_agent.evaluation.clarification_policy import (
    _decision_matches,
    _first_agent_decision,
    _tools_before_first_decision,
    load_cases,
    replay_evaluation,
)


def _ambiguous_case():
    return next(
        case for case in load_cases() if case.case_id == "plate-out-of-plane-ambiguous"
    )


def _decision(question: str = "该截面的面外应变是否受到约束？") -> dict[str, object]:
    return {
        "status": "needs_input",
        "clarification": {
            "field_id": "out_of_plane_behavior",
            "kind": "ambiguous_physical_intent",
            "question": question,
            "impact": "该事实决定使用平面应力还是平面应变理想化。",
        },
        "summary": "面外物理状态尚未确定。",
    }


def test_checked_in_cases_are_strict_and_versioned() -> None:
    cases = load_cases()

    assert len(cases) == 4
    assert {case.policy_version for case in cases} == {CLARIFICATION_POLICY_VERSION}
    assert sum(case.expected_decision == "needs_input" for case in cases) == 2
    derived = next(case for case in cases if case.case_id == "plate-element-derived")
    assert derived.expected_element_type is None
    assert derived.allowed_element_types == ("CPS4", "CPS4R")


def test_decision_score_rejects_pretools_and_implementation_questions() -> None:
    case = _ambiguous_case()

    assert _decision_matches(case, _decision(), ())
    assert not _decision_matches(case, _decision(), ("load_capability",))
    assert not _decision_matches(case, _decision("请选择 CPS4R 或 CPE4R。"), ())
    assert _decision_matches(
        case,
        _decision("请说明面外物理状态，不要只选择 Abaqus 单元类型。"),
        (),
    )


def test_history_and_event_extractors_keep_first_decision_boundary(
    tmp_path: Path,
) -> None:
    history_path = tmp_path / "history.json"
    history_path.write_bytes(
        ModelMessagesTypeAdapter.dump_json(
            [
                ModelResponse(
                    parts=[
                        ToolCallPart("list_directory", {"path": "."}),
                        ToolCallPart(
                            "final_result_NeedsInputDecision",
                            _decision(),
                        ),
                        ToolCallPart("read_file", {"path": "later.py"}),
                    ]
                )
            ]
        )
    )
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps(
            {"event": "agent_decision", "data": _decision()},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    assert _tools_before_first_decision(history_path) == ("list_directory",)
    assert _first_agent_decision(events_path) == _decision()


def test_replay_rescores_stored_attempts_without_overwriting_source(
    tmp_path: Path,
) -> None:
    source = {
        "attempts": [
            {
                "case_id": "plate-out-of-plane-ambiguous",
                "actual_decision": "needs_input",
                "actual_field_id": "out_of_plane_physical_state",
                "actual_kind": "ambiguous_physical_intent",
                "question": "请说明面外物理状态，不要只选择 Abaqus 单元类型。",
                "impact": "该选择改变控制方程与反力。",
                "pre_decision_tool_names": [],
                "passed": False,
            }
        ],
        "cases": [
            {
                "case_id": "plate-out-of-plane-ambiguous",
                "required_passes": 1,
            }
        ],
    }
    source_path = tmp_path / "evaluation_summary.json"
    source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")

    assert replay_evaluation(tmp_path)
    assert json.loads(source_path.read_text("utf-8")) == source
    replay = json.loads(
        (
            tmp_path / "evaluation_replay_clarification-policy-evaluator-v2.json"
        ).read_text("utf-8")
    )
    assert replay["passed"] is True
    assert replay["attempts"][0]["passed"] is True
