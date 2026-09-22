from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from abaqus_sim_agent.evaluation.behavior_measurement import (
    CaseRubric,
    CheckResult,
    Completion,
    MeasurementContract,
    SourceRef,
    Termination,
    TrialCost,
    TrialReview,
    Verdict,
    conjunction,
    file_digest,
    score_trial,
    verify_reference,
)
from abaqus_sim_agent.evaluation.foundation_skill_behavior import EVALUATOR_VERSION


class CriterionAnnotation(MeasurementContract):
    criterion_id: str = Field(min_length=1)
    verdict: Verdict
    reason: str = Field(min_length=1)


class CaseAnnotation(MeasurementContract):
    case_id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    completion_kind: Completion
    opportunity: Literal["observed", "not_observed", "partial", "unknown"]
    checks: tuple[CriterionAnnotation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_checks(self):
        if len({check.criterion_id for check in self.checks}) != len(self.checks):
            raise ValueError("duplicate criterion annotation")
        return self


class BehaviorAnnotations(MeasurementContract):
    schema_version: Literal["foundation-skill-behavior-host-review-v1"]
    reviewer: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: tuple[CaseAnnotation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate case annotation")
        return self


def _read(path: Path) -> object:
    return json.loads(path.read_bytes())


def _source_ref(batch: Path, path: Path, pointer: str = "") -> SourceRef:
    ref = SourceRef(
        path=path.relative_to(batch).as_posix(),
        sha256=file_digest(path),
        pointer=pointer,
    )
    verify_reference(batch, ref)
    return ref


def _termination(
    outcome: dict | None, rubric: CaseRubric, error: str | None
) -> Termination:
    if outcome is not None:
        failure = outcome.get("failure_stage")
        if outcome.get("status") == "completed":
            return "normal"
        if (
            failure == "clarification"
            and "appropriately_stopped" in rubric.accepted_completions
        ):
            return "normal"
        mapping: dict[str, Termination] = {
            "model_budget": "model_budget",
            "runtime_budget": "runtime_budget",
            "wall_clock": "wall_budget",
            "timeout": "wall_budget",
            "provider": "provider",
        }
        return (
            mapping.get(failure, "unknown") if isinstance(failure, str) else "unknown"
        )
    return "unknown" if error else "environment"


def _trial_review(
    batch: Path,
    rubric: CaseRubric,
    observation: dict,
    annotation: CaseAnnotation,
) -> TrialReview:
    if rubric.case_id != observation["case_id"] or rubric.case_id != annotation.case_id:
        raise ValueError("case identity mismatch")
    expected = {criterion.id for criterion in rubric.criteria}
    actual = {check.criterion_id for check in annotation.checks}
    if expected != actual:
        raise ValueError("annotation must cover the exact behavior rubric")
    state = batch / rubric.case_id / "state"
    observation_path = state / "foundation_behavior_observation.json"
    obs_ref = _source_ref(batch, observation_path)
    checks = tuple(
        CheckResult(
            criterion_id=check.criterion_id,
            verdict=check.verdict,
            reason=check.reason,
            evidence=(obs_ref,),
        )
        for check in annotation.checks
    )
    outcome = observation.get("outcome")
    usage = outcome.get("usage", {}) if isinstance(outcome, dict) else {}
    termination = _termination(
        outcome if isinstance(outcome, dict) else None,
        rubric,
        observation.get("infrastructure_error_type"),
    )
    normal = termination == "normal"
    if not normal and annotation.completion_kind in {
        "delivered",
        "clarified",
        "appropriately_stopped",
    }:
        raise ValueError("abnormal trial cannot be annotated as completed")
    provenance = [obs_ref]
    history = state / "history.json"
    if history.is_file():
        provenance.append(_source_ref(batch, history))
    return TrialReview(
        trial_id=observation["trial_id"],
        case_id=rubric.case_id,
        started=True,
        completion_kind=annotation.completion_kind,
        host_status=outcome.get("status") if isinstance(outcome, dict) else None,
        termination_reason=termination,
        opportunity=annotation.opportunity,
        measurement_valid=normal,
        validity_reason=(
            "Complete offline behavior trace available for semantic review."
            if normal
            else "Trial ended abnormally; retain observed failures but do not count an apparent pass as validated behavior."
        ),
        checks=checks,
        cost=TrialCost(
            model_requests=usage.get("model_requests"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            token_coverage="complete" if usage else "unknown",
            runtime_attempts=1 if observation.get("runtime_requested") else 0,
            runtime_started=(observation.get("actual_abaqus_calls") or 0)
            + (observation.get("actual_solver_calls") or 0),
            lookup_calls=0,
            wall_seconds=usage.get("wall_time_seconds"),
        ),
        provenance=tuple(provenance),
    )


def build_audit(batch: Path, annotations_path: Path) -> dict[str, object]:
    batch = batch.resolve(strict=True)
    annotations = BehaviorAnnotations.model_validate_json(annotations_path.read_bytes())
    manifest_path = batch / "manifest.json"
    report_path = batch / "report.json"
    if (
        file_digest(manifest_path) != annotations.manifest_sha256
        or file_digest(report_path) != annotations.report_sha256
    ):
        raise ValueError("annotations are bound to another behavior batch")
    manifest = _read(manifest_path)
    report = _read(report_path)
    if not isinstance(manifest, dict) or not isinstance(report, dict):
        raise ValueError("invalid behavior batch")
    if manifest.get("evaluator_version") != EVALUATOR_VERSION:
        raise ValueError("unsupported behavior evaluator version")
    if (
        manifest.get("api_catalog_mode") != "off"
        or manifest.get("concept_retrieval_mode") != "off"
    ):
        raise ValueError("behavior batch enabled an excluded retrieval channel")
    if manifest.get("planning_enabled") is not False:
        raise ValueError("behavior batch enabled Planning")
    rubrics_raw = _read(batch / "rubrics.json")
    if not isinstance(rubrics_raw, list):
        raise ValueError("invalid sealed rubric snapshot")
    rubrics = {
        item["case_id"]: CaseRubric.model_validate_json(json.dumps(item))
        for item in rubrics_raw
    }
    observations = report.get("cases")
    if not isinstance(observations, list):
        raise ValueError("invalid behavior report")
    by_observation = {item["case_id"]: item for item in observations}
    by_annotation = {item.case_id: item for item in annotations.cases}
    if set(rubrics) != set(by_observation) or set(rubrics) != set(by_annotation):
        raise ValueError(
            "rubrics, observations and annotations must cover the same cases"
        )

    trial_reports: list[dict[str, object]] = []
    for case_id, rubric in rubrics.items():
        observation_path = (
            batch / case_id / "state/foundation_behavior_observation.json"
        )
        recorded = _read(observation_path)
        if recorded != by_observation[case_id]:
            raise ValueError("per-case observation differs from frozen report")
        review = _trial_review(
            batch,
            rubric,
            by_observation[case_id],
            by_annotation[case_id],
        )
        score = score_trial(rubric, review)
        runtime_constraint: Verdict = (
            "fail" if by_observation[case_id].get("runtime_requested") else "pass"
        )
        behavior_acceptance = conjunction((score.joint_acceptance, runtime_constraint))
        trial_reports.append(
            {
                "trial_id": review.trial_id,
                "case_id": case_id,
                "semantic_score": score.model_dump(mode="json"),
                "runtime_constraint": runtime_constraint,
                "behavior_acceptance": behavior_acceptance,
                "routing_cost": {
                    "load_sequence": by_observation[case_id]["load_sequence"],
                    "forbidden_load_count": by_observation[case_id][
                        "forbidden_load_count"
                    ],
                    "load_call_count": by_observation[case_id]["load_call_count"],
                    "duplicate_load_call_count": by_observation[case_id][
                        "duplicate_load_call_count"
                    ],
                    "loaded_skill_body_bytes": by_observation[case_id][
                        "loaded_skill_body_bytes"
                    ],
                },
            }
        )
    counts = {
        verdict: sum(item["behavior_acceptance"] == verdict for item in trial_reports)
        for verdict in ("pass", "fail", "unknown", "not_applicable")
    }
    return {
        "schema_version": "foundation-skill-behavior-audit-v1",
        "reviewer": annotations.reviewer,
        "profile": manifest["profile"],
        "repetition": manifest["repetition"],
        "manifest_sha256": annotations.manifest_sha256,
        "report_sha256": annotations.report_sha256,
        "annotations_sha256": file_digest(annotations_path),
        "behavior_acceptance": counts,
        "case_count": len(trial_reports),
        "cases": trial_reports,
        "claims_excluded": [
            "physical validation",
            "Abaqus runtime correctness",
            "general foundation-Skill benefit",
            "routing exactness as engineering correctness",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit one frozen foundation-Skill behavior batch using host annotations."
    )
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = build_audit(args.batch, args.annotations)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(report["behavior_acceptance"], ensure_ascii=False))


if __name__ == "__main__":
    main()
