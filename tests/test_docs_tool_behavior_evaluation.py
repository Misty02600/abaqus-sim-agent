import json
from pathlib import Path

import pytest
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelResponse, ToolCallPart

from abaqus_sim_agent.evaluation.docs_tool_behavior import load_cases, observe_case


def test_behavior_cases_have_six_distinct_surfaces_and_private_review_criteria() -> (
    None
):
    cases = load_cases()
    assert len(cases) == 6
    assert [case.preferred_route for case in cases] == [
        "workspace",
        "clarification",
        "api",
        "docs",
        "workspace",
        "docs",
    ]
    assert all(case.review_criteria for case in cases)
    assert all("preferred_route" not in case.goal for case in cases)


def test_observer_preserves_actions_and_does_not_grade_no_lookup_as_failure(
    tmp_path: Path,
) -> None:
    case = next(case for case in load_cases() if case.preferred_route == "docs")
    state = tmp_path / case.case_id / "state"
    workspace = state.parent / "workspace"
    state.mkdir(parents=True)
    workspace.mkdir()
    (state / "history.json").write_bytes(
        ModelMessagesTypeAdapter.dump_json(
            [
                ModelResponse(
                    parts=[ToolCallPart("read_file", {"path": "project_notes.txt"})]
                ),
                ModelResponse(
                    parts=[ToolCallPart("run_solver_job", {"job_name": "forbidden"})]
                ),
            ]
        )
    )
    (workspace / "analysis.json").write_text(
        '{"summary":"answer from current context"}', encoding="utf-8"
    )
    observed = observe_case(tmp_path, case)
    assert observed["review_status"] == "pending_manual_review"
    assert observed["declared_gold_page_hit"] is False
    assert observed["runtime_requested"] is True
    assert observed["document_queries"] == []
    assert "passed" not in observed


def test_case_loader_rejects_seed_path_escape(tmp_path: Path) -> None:
    case = load_cases()[0].model_dump(mode="json")
    case["seed_files"] = {"../outside.py": "pass"}
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="plain workspace"):
        load_cases(path)
