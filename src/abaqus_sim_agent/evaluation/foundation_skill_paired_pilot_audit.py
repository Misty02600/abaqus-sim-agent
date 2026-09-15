from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

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
from abaqus_sim_agent.evaluation.foundation_pilot_trace import summarize_trace
from abaqus_sim_agent.evaluation.foundation_public_mechanics_benchmark import (
    PairOutcome,
    Severity,
    paired_outcome,
)
from abaqus_sim_agent.evaluation.foundation_skill_paired_pilot import EVALUATOR_VERSION

Profile = Literal["control", "foundation"]


class CriterionAnnotation(MeasurementContract):
    criterion_id: str = Field(min_length=1)
    verdict: Verdict
    reason: str = Field(min_length=1)


class TrialAnnotation(MeasurementContract):
    trial_id: str = Field(pattern=r"^[a-z0-9-]+$")
    completion_kind: Completion
    opportunity: Literal["observed", "not_observed", "partial", "unknown"]
    checks: tuple[CriterionAnnotation, ...] = Field(min_length=1)
    severity: Severity
    severity_reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def unique_checks(self):
        if len({check.criterion_id for check in self.checks}) != len(self.checks):
            raise ValueError("duplicate criterion annotation")
        return self


class PilotAnnotations(MeasurementContract):
    schema_version: Literal["foundation-skill-paired-pilot-host-review-v1"]
    reviewer: str = Field(min_length=1)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trials: tuple[TrialAnnotation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_trials(self):
        if len({trial.trial_id for trial in self.trials}) != len(self.trials):
            raise ValueError("duplicate trial annotation")
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
    annotation: TrialAnnotation,
    trace: dict,
) -> TrialReview:
    if rubric.case_id != observation["case_id"]:
        raise ValueError("case identity mismatch")
    if annotation.trial_id != observation["trial_id"]:
        raise ValueError("trial identity mismatch")
    expected = {criterion.id for criterion in rubric.criteria}
    actual = {check.criterion_id for check in annotation.checks}
    if expected != actual:
        raise ValueError("annotation must cover the exact frozen rubric")

    state = batch / "trials" / annotation.trial_id / "state"
    observation_path = state / "foundation_paired_pilot_observation.json"
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
    trace_available = bool(
        trace["files"]["history.json"] and trace["complete_responses"]
    )
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
        trial_id=annotation.trial_id,
        case_id=rubric.case_id,
        started=True,
        completion_kind=annotation.completion_kind,
        host_status=outcome.get("status") if isinstance(outcome, dict) else None,
        termination_reason=termination,
        opportunity=annotation.opportunity,
        measurement_valid=normal and trace_available,
        validity_reason=(
            "Complete offline behavior trace available for semantic review."
            if normal and trace_available
            else "Abnormal termination or unavailable history excludes this trial from paired treatment scoring."
        ),
        checks=checks,
        cost=TrialCost(
            model_requests=usage.get("model_requests"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            token_coverage=(
                "unknown"
                if not trace["complete_responses"]
                or not all(
                    usage.get(k) is not None for k in ("input_tokens", "output_tokens")
                )
                else "complete"
                if normal
                else "received_responses_only"
            ),
            runtime_attempts=trace["runtime_tool_attempts"]
            if trace["files"]["history.json"]
            else None,
            runtime_started=observation.get("actual_abaqus_calls"),
            lookup_calls=0,
            wall_seconds=usage.get("wall_time_seconds"),
        ),
        provenance=tuple(provenance),
    )


def summarize_pair_outcomes(
    trial_verdicts: dict[tuple[str, int, Profile], Verdict],
) -> dict[str, object]:
    pair_keys = sorted(
        {(case_id, repetition) for case_id, repetition, _ in trial_verdicts}
    )
    pairs: list[dict[str, object]] = []
    counts = dict.fromkeys(("win", "tie", "loss", "unpaired_unknown"), 0)
    for case_id, repetition in pair_keys:
        control = trial_verdicts.get((case_id, repetition, "control"), "unknown")
        foundation = trial_verdicts.get((case_id, repetition, "foundation"), "unknown")
        result: PairOutcome = paired_outcome(control, foundation)
        counts[result] += 1
        pairs.append(
            {
                "case_id": case_id,
                "repetition": repetition,
                "control": control,
                "foundation": foundation,
                "outcome": result,
            }
        )
    return {"counts": counts, "pairs": pairs}


def severity_counts(
    rows: list[tuple[Profile, Severity, bool]],
) -> dict[str, dict[str, int]]:
    result = {
        profile: {"S0": 0, "S1": 0, "S2": 0, "S3": 0, "S2_S3": 0, "invalid": 0}
        for profile in ("control", "foundation")
    }
    for profile, severity, measurement_valid in rows:
        if not measurement_valid:
            result[profile]["invalid"] += 1
            continue
        result[profile][severity] += 1
        if severity in {"S2", "S3"}:
            result[profile]["S2_S3"] += 1
    return result


def _cost_summary(rows: list[tuple[Profile, TrialCost]]) -> dict[str, Any]:
    fields = ("model_requests", "input_tokens", "output_tokens", "wall_seconds")
    result: dict[str, Any] = {}
    for profile in ("control", "foundation"):
        costs = [cost for row_profile, cost in rows if row_profile == profile]
        result[profile] = {
            field: {
                "known_sum": sum(
                    value
                    for value in (getattr(cost, field) for cost in costs)
                    if value is not None
                ),
                "unknown_trials": sum(getattr(cost, field) is None for cost in costs),
            }
            for field in fields
        }
        result[profile]["token_coverage"] = {
            status: sum(cost.token_coverage == status for cost in costs)
            for status in ("complete", "received_responses_only", "unknown")
        }
        result[profile]["trial_count"] = len(costs)
    return result


def paired_cost_summary(trials: list[dict]) -> dict[str, Any]:
    """只在相同 case/repetition 内配对，并分别保留有效与双方通过子集。"""
    groups: defaultdict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for trial in trials:
        group = groups[(trial["case_id"], trial["repetition"])]
        if trial["profile"] in group:
            raise ValueError("duplicate profile within a trial pair")
        group[trial["profile"]] = trial
    fields = (
        "model_requests",
        "input_tokens",
        "output_tokens",
        "tool_calls",
        "wall_seconds",
    )
    pairs = []
    for (case_id, repetition), group in sorted(groups.items()):
        eligible = all(
            p in group
            and group[p]["measurement_valid"]
            and group[p]["paired_verdict"] in {"pass", "fail"}
            for p in ("control", "foundation")
        )
        pairs.append(
            {
                "case_id": case_id,
                "repetition": repetition,
                "eligible": eligible,
                "both_pass": eligible
                and all(r["paired_verdict"] == "pass" for r in group.values()),
                "trial_ids": {p: r["trial_id"] for p, r in group.items()},
                "values": {
                    p: {key: r["cost_values"].get(key) for key in fields}
                    for p, r in group.items()
                },
            }
        )
    summaries = {}
    for label, predicate in (
        ("measurement_valid_pairs", "eligible"),
        ("both_pass_pairs", "both_pass"),
    ):
        selected = [p for p in pairs if p[predicate]]
        summary = {"pair_count": len(selected), "metrics": {}}
        for field in (*fields, "total_tokens"):
            values = []
            for pair in selected:
                if field == "total_tokens":
                    value = {
                        p: (
                            pair["values"][p]["input_tokens"]
                            + pair["values"][p]["output_tokens"]
                            if pair["values"][p]["input_tokens"] is not None
                            and pair["values"][p]["output_tokens"] is not None
                            else None
                        )
                        for p in ("control", "foundation")
                    }
                else:
                    value = {
                        p: pair["values"][p][field] for p in ("control", "foundation")
                    }
                if all(v is not None for v in value.values()):
                    values.append(value)
            sums = {p: sum(v[p] for v in values) for p in ("control", "foundation")}
            summary["metrics"][field] = {
                "known_pair_count": len(values),
                "unknown_pair_count": len(selected) - len(values),
                **sums,
                "delta": sums["foundation"] - sums["control"],
                "relative_change": sums["foundation"] / sums["control"] - 1
                if sums["control"]
                else None,
            }
        summaries[label] = summary
    return {
        **summaries,
        "pairs": pairs,
        "interpretation": "Conditional paired profile comparison, not isolated Skill overhead or billing. Both-pass selection is post-treatment; invalid trials remain in batch resource accounting.",
    }


def build_audit(batch: Path, annotations_path: Path) -> dict[str, Any]:
    batch = batch.resolve(strict=True)
    annotations = PilotAnnotations.model_validate_json(annotations_path.read_bytes())
    manifest_path = batch / "manifest.json"
    report_path = batch / "report.json"
    if (
        file_digest(manifest_path) != annotations.manifest_sha256
        or file_digest(report_path) != annotations.report_sha256
    ):
        raise ValueError("annotations are bound to another paired pilot")

    manifest = _read(manifest_path)
    report = _read(report_path)
    if not isinstance(manifest, dict) or not isinstance(report, dict):
        raise ValueError("invalid paired pilot")
    if manifest.get("evaluator_version") not in {
        EVALUATOR_VERSION,
        "foundation-followup-v2",
    }:
        raise ValueError("unsupported paired pilot evaluator version")
    if any(
        manifest.get(name) not in (False, "off")
        for name in (
            "planning_enabled",
            "api_catalog_mode",
            "concept_retrieval_mode",
            "diagnostic_retrieval_mode",
        )
    ):
        raise ValueError("paired pilot enabled an excluded treatment channel")

    rubrics_raw = _read(batch / "rubrics.json")
    if not isinstance(rubrics_raw, list):
        raise ValueError("invalid sealed rubric snapshot")
    rubrics = {
        item["case_id"]: CaseRubric.model_validate_json(json.dumps(item))
        for item in rubrics_raw
    }
    observations = report.get("trials")
    if not isinstance(observations, list):
        raise ValueError("invalid paired pilot report")
    by_observation = {item["trial_id"]: item for item in observations}
    if len(by_observation) != len(observations):
        raise ValueError("duplicate trial observation")
    by_annotation = {item.trial_id: item for item in annotations.trials}
    planned = manifest.get("trial_plan")
    if not isinstance(planned, list):
        raise ValueError("invalid paired pilot trial plan")
    planned_ids = [item["trial_id"] for item in planned]
    if len(set(planned_ids)) != len(planned_ids):
        raise ValueError("duplicate planned trial")
    if set(planned_ids) != set(by_observation) or set(planned_ids) != set(
        by_annotation
    ):
        raise ValueError("trial plan, observations and annotations must match exactly")

    trial_reports: list[dict[str, object]] = []
    trial_verdicts: dict[tuple[str, int, Profile], Verdict] = {}
    severity_rows: list[tuple[Profile, Severity, bool]] = []
    cost_rows: list[tuple[Profile, TrialCost]] = []
    routing_bytes: defaultdict[str, int] = defaultdict(int)
    for planned_trial in planned:
        trial_id = planned_trial["trial_id"]
        observation = by_observation[trial_id]
        annotation = by_annotation[trial_id]
        case_id = planned_trial["case_id"]
        profile: Profile = planned_trial["profile"]
        repetition = int(planned_trial["repetition"])
        if (
            observation.get("profile") != profile
            or observation.get("repetition") != repetition
        ):
            raise ValueError("observation does not match frozen trial plan")

        observation_path = (
            batch
            / "trials"
            / trial_id
            / "state/foundation_paired_pilot_observation.json"
        )
        if _read(observation_path) != observation:
            raise ValueError("per-trial observation differs from frozen report")
        rubric = rubrics[case_id]
        outcome = observation.get("outcome") or {}
        usage = outcome.get("usage") or {}
        trace = summarize_trace(observation_path.parent, usage)
        review = _trial_review(batch, rubric, observation, annotation, trace)
        score = score_trial(rubric, review)
        runtime_constraint: Verdict = (
            "fail" if observation.get("runtime_requested") else "pass"
        )
        semantic = conjunction((score.joint_acceptance, runtime_constraint))
        paired_verdict: Verdict = semantic if review.measurement_valid else "unknown"
        trial_verdicts[(case_id, repetition, profile)] = paired_verdict
        severity_rows.append((profile, annotation.severity, review.measurement_valid))
        cost_rows.append((profile, review.cost))
        routing_bytes[profile] += int(observation.get("loaded_skill_body_bytes") or 0)
        trial_reports.append(
            {
                "trial_id": trial_id,
                "case_id": case_id,
                "case_kind": planned_trial["case_kind"],
                "profile": profile,
                "repetition": repetition,
                "semantic_score": score.model_dump(mode="json"),
                "runtime_constraint": runtime_constraint,
                "paired_verdict": paired_verdict,
                "severity": annotation.severity,
                "severity_reason": annotation.severity_reason,
                "measurement_valid": review.measurement_valid,
                "trace": trace,
                "cost_values": {
                    **review.cost.model_dump(mode="json"),
                    "tool_calls": usage.get("tool_calls"),
                },
                "routing_cost": {
                    "load_sequence": observation["load_sequence"],
                    "load_call_count": observation["load_call_count"],
                    "duplicate_load_call_count": observation[
                        "duplicate_load_call_count"
                    ],
                    "loaded_skill_body_bytes": observation["loaded_skill_body_bytes"],
                    "loaded_skill_body_bytes_scope": "Legacy unique source-file byte sizes, including frontmatter; not provider tokens or exact delivered body.",
                    "delivered_capability_instruction_bytes": trace[
                        "delivered_capability_instruction_bytes"
                    ],
                },
            }
        )

    return {
        "schema_version": "foundation-skill-paired-pilot-audit-v2",
        "reviewer": annotations.reviewer,
        "manifest_sha256": annotations.manifest_sha256,
        "report_sha256": annotations.report_sha256,
        "annotations_sha256": file_digest(annotations_path),
        "audit_implementation_sha256": {
            Path(__file__).name: file_digest(Path(__file__)),
            "foundation_pilot_trace.py": file_digest(
                Path(__file__).with_name("foundation_pilot_trace.py")
            ),
        },
        "trial_count": len(trial_reports),
        "paired": summarize_pair_outcomes(trial_verdicts),
        "severity": severity_counts(severity_rows),
        "cost": _cost_summary(cost_rows),
        "cost_scope": "All-trial recorded usage, including partial/unknown coverage. Does not include unknown in-flight requests or full HTTP retry billing.",
        "paired_cost": paired_cost_summary(trial_reports),
        "routing_descriptive": {
            "loaded_skill_body_bytes": dict(routing_bytes),
        },
        "trials": trial_reports,
        "claims_excluded": [
            "physical validation",
            "Abaqus runtime correctness",
            "routing exactness as engineering correctness",
            "general Foundation Skill benefit beyond this frozen pilot",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit a frozen Foundation Skill paired pilot using host annotations."
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
    print(json.dumps(report["paired"], ensure_ascii=False))


if __name__ == "__main__":
    main()
