import json
from pathlib import Path

import pytest
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)

from abaqus_sim_agent.evaluation import (
    foundation_skill_behavior,
    skill_routing_behavior,
)
from abaqus_sim_agent.evaluation.skill_observation import (
    loaded_skill_body_bytes,
    read_skill_evidence,
    score_skill_route,
    skill_content_hashes,
    write_new_json,
)


def save_history(state: Path, calls: list[ToolCallPart]) -> None:
    state.mkdir(parents=True)
    (state / "history.json").write_bytes(
        ModelMessagesTypeAdapter.dump_json(
            [
                ModelRequest(parts=[UserPromptPart("inspection fixture")]),
                ModelResponse(parts=calls),
            ]
        )
    )


def test_observation_preserves_load_attempts_separately_from_ids(tmp_path):
    save_history(
        tmp_path / "state",
        [
            ToolCallPart("load_capability", {"id": "known"}),
            ToolCallPart("load_capability", {"id": "known"}),
            ToolCallPart("load_capability", {}),
            ToolCallPart("load_capability", {"id": ""}),
            ToolCallPart("read_file", {"path": "model.py"}),
            ToolCallPart("run_cae_script", {"script": "model.py"}),
        ],
    )
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    evidence = read_skill_evidence(tmp_path)
    fields = evidence.observation_fields("fixture")
    assert fields["load_call_count"] == 4
    assert fields["duplicate_load_call_count"] == 1
    assert fields["load_sequence"] == ["known", "known"]
    assert fields["runtime_requested"] is True
    assert fields["analysis"] is None
    assert fields["outcome"] is None
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("bad_file", ["history.json", "outcome.json"])
def test_corrupt_host_evidence_is_not_silently_treated_as_absent(tmp_path, bad_file):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / bad_file).write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError, match=r"(?i)(invalid json|expecting value)"):
        read_skill_evidence(tmp_path)


def test_invalid_agent_analysis_is_retained_for_review(tmp_path):
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "analysis.json").write_bytes(b"\xff")
    assert read_skill_evidence(tmp_path).analysis == {"invalid_json": True}


def test_route_grade_does_not_become_a_semantic_behavior_grade(tmp_path):
    case = skill_routing_behavior.load_cases()[0]
    save_history(
        tmp_path / case.case_id / "state",
        [
            ToolCallPart("load_capability", {"id": "not-in-this-suite"}),
        ],
    )
    routing = skill_routing_behavior.observe_case(tmp_path, case)
    behavior = foundation_skill_behavior.observe_case(tmp_path, case)
    assert routing["unexpected_loads"] == ["not-in-this-suite"]
    assert routing["exact_route_pass"] is False
    # The behavior contract describes forbidden known Skills separately from unknown IDs.
    assert behavior["unexpected_loads"] == []
    assert behavior["load_sequence"] == ["not-in-this-suite"]
    assert behavior["behavior_review_status"] == "pending_host_semantic_review"
    assert "exact_route_pass" not in behavior
    assert "behavior_pass" not in behavior


def test_skill_content_scope_is_explicit_and_duplicate_loads_are_not_billed_twice(
    tmp_path,
):
    for name, raw in [("one", b"abc"), ("two", b"defgh")]:
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_bytes(raw)
    assert (
        loaded_skill_body_bytes(
            ["one", "one", "two", "unknown"],
            skill_root=tmp_path,
            capability_ids=("one",),
        )
        == 3
    )
    assert set(skill_content_hashes(tmp_path, ("two",))) == {"two"}


def test_route_scoring_handles_the_explicit_no_load_case_without_io():
    assert (
        score_skill_route(
            [], required_loads=(), allowed_loads=(), forbidden_loads=("known",)
        )["exact_route_pass"]
        is True
    )
    result = score_skill_route(
        ["unknown"], required_loads=(), allowed_loads=(), forbidden_loads=("known",)
    )
    assert result["exact_route_pass"] is False
    assert result["forbidden_load_count"] == 0
    assert result["unexpected_loads"] == ["unknown"]


def test_new_json_writer_keeps_exclusive_creation_and_utf8_bytes(tmp_path):
    path = tmp_path / "observation.json"
    value = {"reason": "保持原证据"}
    write_new_json(path, value)
    before = path.read_bytes()
    assert before == (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    with pytest.raises(FileExistsError):
        write_new_json(path, {"replacement": True})
    assert path.read_bytes() == before
