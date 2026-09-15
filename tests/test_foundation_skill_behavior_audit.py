import hashlib
import json
from pathlib import Path

from abaqus_sim_agent.evaluation.behavior_measurement import CaseRubric
from abaqus_sim_agent.evaluation.foundation_skill_behavior_audit import build_audit


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_audit_accepts_appropriate_missing_input_stop(tmp_path: Path) -> None:
    batch = tmp_path / "batch"
    state = batch / "missing-user-physical-input" / "state"
    state.mkdir(parents=True)
    rubric = CaseRubric.model_validate_json(
        json.dumps(
            {
                "case_id": "missing-user-physical-input",
                "accepted_completions": ["appropriately_stopped"],
                "criteria": [
                    {
                        "id": "identify_thickness_dependency",
                        "category": "task",
                        "origin": "user_task",
                        "requirement": "identify thickness",
                        "basis": "test",
                    },
                    {
                        "id": "no_fabricated_thickness",
                        "category": "constraint",
                        "origin": "host_contract",
                        "requirement": "do not invent thickness",
                        "basis": "test",
                    },
                ],
            }
        )
    )
    observation = {
        "case_id": "missing-user-physical-input",
        "trial_id": "control-missing-user-physical-input-r1",
        "load_sequence": [],
        "forbidden_load_count": 0,
        "load_call_count": 0,
        "duplicate_load_call_count": 0,
        "loaded_skill_body_bytes": 0,
        "runtime_requested": False,
        "analysis": {
            "summary": "Thickness is required for the requested reaction.",
            "findings": ["No value was invented."],
            "next_action": "Ask for thickness.",
        },
        "outcome": {
            "status": "failed",
            "failure_stage": "clarification",
            "usage": {
                "model_requests": 2,
                "input_tokens": 100,
                "output_tokens": 40,
                "wall_time_seconds": 3.0,
            },
        },
        "infrastructure_error_type": None,
        "actual_abaqus_calls": 0,
        "actual_solver_calls": 0,
    }
    _write_json(state / "foundation_behavior_observation.json", observation)
    _write_json(
        batch / "manifest.json",
        {
            "evaluator_version": "linear-structural-fea-foundation-behavior-v1",
            "profile": "control",
            "repetition": 1,
            "api_catalog_mode": "off",
            "concept_retrieval_mode": "off",
            "planning_enabled": False,
        },
    )
    _write_json(batch / "rubrics.json", [rubric.model_dump(mode="json")])
    _write_json(batch / "report.json", {"cases": [observation]})
    annotations = {
        "schema_version": "foundation-skill-behavior-host-review-v1",
        "reviewer": "test-host",
        "manifest_sha256": _sha(batch / "manifest.json"),
        "report_sha256": _sha(batch / "report.json"),
        "cases": [
            {
                "case_id": "missing-user-physical-input",
                "completion_kind": "appropriately_stopped",
                "opportunity": "observed",
                "checks": [
                    {
                        "criterion_id": "identify_thickness_dependency",
                        "verdict": "pass",
                        "reason": "The analysis identifies thickness as blocking the reaction result.",
                    },
                    {
                        "criterion_id": "no_fabricated_thickness",
                        "verdict": "pass",
                        "reason": "No numeric thickness was introduced.",
                    },
                ],
            }
        ],
    }
    annotations_path = tmp_path / "annotations.json"
    _write_json(annotations_path, annotations)

    audit = build_audit(batch, annotations_path)

    assert audit["behavior_acceptance"] == {
        "pass": 1,
        "fail": 0,
        "unknown": 0,
        "not_applicable": 0,
    }


def test_audit_runtime_attempt_overrides_semantic_pass(tmp_path: Path) -> None:
    batch = tmp_path / "batch"
    state = batch / "local-name-mismatch" / "state"
    state.mkdir(parents=True)
    rubric = CaseRubric.model_validate_json(
        json.dumps(
            {
                "case_id": "local-name-mismatch",
                "accepted_completions": ["delivered"],
                "criteria": [
                    {
                        "id": "use_local_name",
                        "category": "task",
                        "origin": "user_task",
                        "requirement": "use local name",
                        "basis": "test",
                    }
                ],
            }
        )
    )
    observation = {
        "case_id": "local-name-mismatch",
        "trial_id": "foundation-local-name-mismatch-r1",
        "load_sequence": [],
        "forbidden_load_count": 0,
        "load_call_count": 0,
        "duplicate_load_call_count": 0,
        "loaded_skill_body_bytes": 0,
        "runtime_requested": True,
        "analysis": {"summary": "Used LeftSupport."},
        "outcome": {
            "status": "completed",
            "failure_stage": None,
            "usage": {
                "model_requests": 1,
                "input_tokens": 100,
                "output_tokens": 20,
                "wall_time_seconds": 1.0,
            },
        },
        "infrastructure_error_type": None,
        "actual_abaqus_calls": 0,
        "actual_solver_calls": 0,
    }
    _write_json(state / "foundation_behavior_observation.json", observation)
    _write_json(
        batch / "manifest.json",
        {
            "evaluator_version": "linear-structural-fea-foundation-behavior-v1",
            "profile": "foundation",
            "repetition": 1,
            "api_catalog_mode": "off",
            "concept_retrieval_mode": "off",
            "planning_enabled": False,
        },
    )
    _write_json(batch / "rubrics.json", [rubric.model_dump(mode="json")])
    _write_json(batch / "report.json", {"cases": [observation]})
    annotations_path = tmp_path / "annotations.json"
    _write_json(
        annotations_path,
        {
            "schema_version": "foundation-skill-behavior-host-review-v1",
            "reviewer": "test-host",
            "manifest_sha256": _sha(batch / "manifest.json"),
            "report_sha256": _sha(batch / "report.json"),
            "cases": [
                {
                    "case_id": "local-name-mismatch",
                    "completion_kind": "delivered",
                    "opportunity": "observed",
                    "checks": [
                        {
                            "criterion_id": "use_local_name",
                            "verdict": "pass",
                            "reason": "The semantic answer is correct.",
                        }
                    ],
                }
            ],
        },
    )

    audit = build_audit(batch, annotations_path)

    assert audit["cases"][0]["semantic_score"]["joint_acceptance"] == "pass"
    assert audit["cases"][0]["runtime_constraint"] == "fail"
    assert audit["cases"][0]["behavior_acceptance"] == "fail"
