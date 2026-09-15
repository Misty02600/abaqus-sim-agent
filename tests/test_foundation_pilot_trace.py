import json
from pathlib import Path

import pytest
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RequestUsage

from abaqus_sim_agent.evaluation.behavior_measurement import file_digest
from abaqus_sim_agent.evaluation.foundation_pilot_trace import summarize_trace
from abaqus_sim_agent.evaluation.foundation_skill_paired_pilot import EVALUATOR_VERSION
from abaqus_sim_agent.evaluation.foundation_skill_paired_pilot_audit import (
    build_audit,
    paired_cost_summary,
)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _response(*parts: ToolCallPart) -> ModelResponse:
    return ModelResponse(
        parts=list(parts), usage=RequestUsage(input_tokens=100, output_tokens=20)
    )


def test_trace_keeps_parallel_calls_and_validation_rejection_separate(
    tmp_path: Path,
) -> None:
    messages = [
        _response(
            ToolCallPart("read_file", {}, "bad"),
            ToolCallPart("load_capability", {"id": "x"}, "load"),
        ),
        ModelRequest(
            parts=[
                RetryPromptPart(
                    content=[
                        {
                            "type": "missing",
                            "loc": ("path",),
                            "msg": "Field required",
                            "input": {},
                        }
                    ],
                    tool_name="read_file",
                    tool_call_id="bad",
                ),
                ToolReturnPart("load_capability", {"instructions": "指示"}, "load"),
            ]
        ),
        _response(ToolCallPart("read_file", {"path": "analysis.json"}, "good")),
        ModelRequest(parts=[ToolReturnPart("read_file", "file content", "good")]),
    ]
    (tmp_path / "history.json").write_bytes(
        ModelMessagesTypeAdapter.dump_json(messages)
    )
    trace = summarize_trace(
        tmp_path, {"input_tokens": 200, "output_tokens": 40, "model_requests": 2}
    )
    assert trace["tools"]["read_file"] == {"validation_rejected": 1, "returned": 1}
    assert trace["responses"][0]["tools_in_same_response"] == [
        "read_file",
        "load_capability",
    ]
    assert trace["responses"][1]["phase"] == "after_load_before_write"
    assert trace["last_observed_position"] == "after_tool_feedback"
    assert trace["files"]["events.jsonl"] is False
    assert trace["delivered_capability_instruction_bytes"] == 6
    assert "file content" not in json.dumps(trace)


def test_trace_refuses_usage_mismatch_and_does_not_invent_missing_events(
    tmp_path: Path,
) -> None:
    (tmp_path / "history.json").write_bytes(
        ModelMessagesTypeAdapter.dump_json([_response()])
    )
    with pytest.raises(ValueError, match="disagree"):
        summarize_trace(tmp_path, {"model_requests": 0})
    (tmp_path / "history.json").unlink()
    assert (
        summarize_trace(tmp_path, {})["last_observed_position"] == "history_unavailable"
    )


def test_pair_cost_uses_same_case_repeat_and_preserves_unknown_metric() -> None:
    rows = []
    for repeat, verdicts in [
        (1, ("pass", "pass")),
        (2, ("fail", "pass")),
        (3, ("unknown", "pass")),
    ]:
        for profile, verdict in zip(("control", "foundation"), verdicts, strict=True):
            rows.append(
                {
                    "case_id": "case-a",
                    "repetition": repeat,
                    "trial_id": f"{profile}-{repeat}",
                    "profile": profile,
                    "measurement_valid": verdict != "unknown",
                    "paired_verdict": verdict,
                    "cost_values": {
                        "input_tokens": 100 if profile == "control" else 200,
                        "output_tokens": 10,
                        "model_requests": 1,
                        "tool_calls": None,
                        "wall_seconds": 1,
                    },
                }
            )
    summary = paired_cost_summary(rows)
    valid = summary["measurement_valid_pairs"]
    assert valid["pair_count"] == 2
    assert valid["metrics"]["total_tokens"]["control"] == 220
    assert valid["metrics"]["total_tokens"]["foundation"] == 420
    assert valid["metrics"]["tool_calls"]["unknown_pair_count"] == 2
    assert valid["metrics"]["tool_calls"]["relative_change"] is None
    assert summary["both_pass_pairs"]["pair_count"] == 1
    with pytest.raises(ValueError, match="duplicate profile"):
        paired_cost_summary([*rows, rows[0]])


def test_audit_timeout_usage_is_partial_or_unknown_and_original_files_unchanged(
    tmp_path: Path,
) -> None:
    batch = tmp_path / "batch"
    plan = []
    observations = []
    annotations = []
    for repeat in (1, 2, 3):
        for profile in ("control", "foundation"):
            tid = f"r{repeat}-case-a-{profile}"
            normal = profile == "foundation"
            response_count = 0 if repeat == 1 and not normal else 1
            usage = {
                "input_tokens": 100 * response_count,
                "output_tokens": 20 * response_count,
                "model_requests": response_count,
                "tool_calls": 0,
                "wall_time_seconds": 5,
            }
            trial = {
                "trial_id": tid,
                "case_id": "case-a",
                "case_kind": "public",
                "repetition": repeat,
                "profile": profile,
            }
            plan.append(trial)
            observation = {
                **trial,
                "load_sequence": [],
                "load_call_count": 0,
                "duplicate_load_call_count": 0,
                "loaded_skill_body_bytes": 0,
                "runtime_requested": False,
                "actual_abaqus_calls": 0,
                "actual_solver_calls": 0,
                "outcome": {
                    "status": "completed" if normal else "failed",
                    "failure_stage": None if normal else "timeout",
                    "usage": usage,
                },
                "infrastructure_error_type": None,
            }
            observations.append(observation)
            state = batch / "trials" / tid / "state"
            _write(state / "foundation_paired_pilot_observation.json", observation)
            if repeat != 3 or not normal:
                (state / "history.json").write_bytes(
                    ModelMessagesTypeAdapter.dump_json([_response()] * response_count)
                )
            annotations.append(
                {
                    "trial_id": tid,
                    "completion_kind": "delivered" if normal else "not_completed",
                    "opportunity": "observed" if normal else "unknown",
                    "severity": "S0",
                    "severity_reason": "fixture",
                    "checks": [
                        {
                            "criterion_id": "answer",
                            "verdict": "pass" if normal else "unknown",
                            "reason": "fixture",
                        }
                    ],
                }
            )
    _write(
        batch / "manifest.json",
        {
            "evaluator_version": EVALUATOR_VERSION,
            "trial_plan": plan,
            "planning_enabled": False,
            "api_catalog_mode": "off",
            "concept_retrieval_mode": "off",
            "diagnostic_retrieval_mode": "off",
        },
    )
    _write(batch / "report.json", {"trials": observations})
    _write(
        batch / "rubrics.json",
        [
            {
                "case_id": "case-a",
                "accepted_completions": ["delivered"],
                "criteria": [
                    {
                        "id": "answer",
                        "category": "task",
                        "origin": "user_task",
                        "requirement": "answer",
                        "basis": "fixture",
                    }
                ],
            }
        ],
    )
    annotations_path = batch / "host_annotations.json"
    _write(
        annotations_path,
        {
            "schema_version": "foundation-skill-paired-pilot-host-review-v1",
            "reviewer": "test",
            "manifest_sha256": file_digest(batch / "manifest.json"),
            "report_sha256": file_digest(batch / "report.json"),
            "trials": annotations,
        },
    )
    before = {p: file_digest(p) for p in batch.rglob("*") if p.is_file()}
    audit = build_audit(batch, annotations_path)
    assert audit["schema_version"] == "foundation-skill-paired-pilot-audit-v2"
    rows = {r["trial_id"]: r for r in audit["trials"]}
    assert rows["r1-case-a-control"]["cost_values"]["token_coverage"] == "unknown"
    assert (
        rows["r2-case-a-control"]["cost_values"]["token_coverage"]
        == "received_responses_only"
    )
    assert rows["r1-case-a-foundation"]["cost_values"]["token_coverage"] == "complete"
    assert rows["r3-case-a-foundation"]["measurement_valid"] is False
    assert rows["r3-case-a-foundation"]["cost_values"]["token_coverage"] == "unknown"
    assert audit["cost"]["control"]["token_coverage"] == {
        "complete": 0,
        "received_responses_only": 2,
        "unknown": 1,
    }
    assert before == {p: file_digest(p) for p in batch.rglob("*") if p.is_file()}
