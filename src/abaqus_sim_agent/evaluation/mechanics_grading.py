"""把已保存的宿主证据投影到Pydantic Evals；不运行Agent、oracle或Solver。"""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_evals import Case, Dataset
from pydantic_evals.dataset import increment_eval_metric, set_eval_attribute
from pydantic_evals.evaluators import (
    EvaluationReason,
    Evaluator,
    EvaluatorContext,
    EvaluatorOutput,
    ReportEvaluator,
    ReportEvaluatorContext,
)
from pydantic_evals.reporting import (
    EvaluationReport,
    ReportCase,
    ScalarResult,
    TableResult,
)

from abaqus_sim_agent.coding.contracts import CodingRunOutcome, CodingRunUsage
from abaqus_sim_agent.evaluation.case_metadata import CaseMetadata
from abaqus_sim_agent.evaluation.mechanics_submissions import (
    SUBMISSION_POLICY,
    load_submissions,
)
from abaqus_sim_agent.evaluation.public_mechanics_cases import MechanicsCase
from abaqus_sim_agent.verification.public_mechanics_run import (
    PublicMechanicsAttempt,
    compare_metrics,
)


class _Model(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", frozen=True, str_strip_whitespace=True
    )


class MechanicsObservation(_Model):
    case_id: str
    status: str
    execution_ok: bool | None = None
    numeric_ok: bool | None = None
    automatic_pass: bool | None = None
    exclusion: Literal["posthoc", "invalid_evidence"] | None = None
    reason: str
    failure_kind: str
    verification_feedback: str | None = None
    relative_errors: dict[str, float] = Field(default_factory=dict)
    metrics: dict[str, int | float] = Field(default_factory=dict)
    evidence_path: str
    evidence_sha256: dict[str, str] = Field(default_factory=dict)
    first_submission_status: str | None = None
    final_submission_status: str | None = None
    submission_count: int = 0
    feedback_rounds: int = 0
    submission_usage: tuple[CodingRunUsage, ...] = ()


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_verification(state: Path, outcome: CodingRunOutcome):
    path = state / "events.jsonl"
    if not path.exists():
        return None, None
    latest = None
    digest = None
    for raw in path.read_bytes().splitlines():
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("verification event must be an object")
        if (
            event.get("event") == "verification"
            and event.get("task_revision") == outcome.task_revision
            and event.get("amendment_sequence") == outcome.amendment_sequence
        ):
            data = event["data"]
            if (
                not isinstance(data, dict)
                or not {"scope", "verification_policy"} <= data.keys()
            ):
                raise ValueError("verification scope and policy must be explicit")
            latest = PublicMechanicsAttempt.model_validate_json(
                json.dumps(data), strict=True
            )
            digest = _digest(raw)
    return latest, digest


def observe_saved_case(
    root: Path,
    case: MechanicsCase,
    row: dict,
    manifest: dict,
    *,
    bindings_unchanged: bool,
) -> MechanicsObservation:
    """只读任务记录，把缺失、损坏和事后干预保留为显式状态。

    Note:
        verification事件是已有验收结果，不用于推断缺失的runtime副作用ledger。
        本函数不重新打开ODB，也不读取人工审查文件。
    """
    state = root / case.case_id / "state"
    values = {
        "case_id": case.case_id,
        "status": row["classification"],
        "reason": row["classification"],
        "failure_kind": row["classification"],
        "evidence_path": str(state),
    }
    metrics: dict[str, int | float] = {}
    usage = row.get("received_usage") or {}
    for key in ("requests", "input_tokens", "output_tokens", "tool_calls"):
        value = usage.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            metrics[key] = value
    for key in (
        "abaqus_calls",
        "solver_calls",
        "api_lookup_calls",
        "persisted_active_wall_seconds",
    ):
        value = row.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        ):
            metrics[key] = value
    if not bindings_unchanged or row["classification"] == "invalid_evidence":
        return MechanicsObservation(
            **values, metrics=metrics, exclusion="invalid_evidence"
        )
    outcome_path = state / "outcome.json"
    hashes: dict[str, str] = {}
    try:
        if manifest.get("submission_policy") == SUBMISSION_POLICY:
            submissions = load_submissions(state)
            statuses = [
                "evaluation_error"
                if result is None or result.failure_stage == "evaluation_error"
                else "passed"
                if result.passed
                else "failed"
                for _, result in submissions
            ]
            values.update(
                first_submission_status=statuses[0] if statuses else "not_submitted",
                final_submission_status=statuses[-1] if statuses else "not_submitted",
                submission_count=len(submissions),
                feedback_rounds=sum(
                    json.loads(line).get("event") == "verification_feedback"
                    for line in (state / "events.jsonl").read_bytes().splitlines()
                )
                if (state / "events.jsonl").exists()
                else 0,
                submission_usage=tuple(
                    submission.usage for submission, _ in submissions
                ),
            )
            for submission, _ in submissions:
                folder = state / "submissions" / f"{submission.number:03}"
                hashes[f"submission_{submission.number}_sha256"] = _digest(
                    (folder / "submission.json").read_bytes()
                )
                if (folder / "result.json").exists():
                    hashes[f"submission_{submission.number}_result_sha256"] = _digest(
                        (folder / "result.json").read_bytes()
                    )
        if not outcome_path.exists():
            return MechanicsObservation(
                **values, metrics=metrics, evidence_sha256=hashes
            )
        outcome_raw = outcome_path.read_bytes()
        outcome = CodingRunOutcome.model_validate_json(outcome_raw, strict=True)
        hashes["outcome_sha256"] = _digest(outcome_raw)
        values["reason"] = outcome.summary
        values["failure_kind"] = outcome.failure_stage or "none"
        posthoc = (
            outcome.amendment_sequence != 0
            or outcome.task_revision != 0
            or sum(a.get("mode") == "resume" for a in row.get("attempts", []))
            > manifest.get("max_explicit_resumes", 0)
        )
        verification, verification_digest = _load_verification(state, outcome)
        execution_ok = None
        numeric_ok = None
        automatic_pass = None
        errors: dict[str, float] = {}
        if verification is not None:
            if verification_digest is None:
                raise ValueError("missing verification event digest")
            hashes["verification_event_sha256"] = verification_digest
            if (
                verification.scope != "task_oracle"
                or verification.verification_policy
                != manifest.get("verification_policy")
            ):
                raise ValueError("verification scope/policy mismatch")
            execution_ok = verification.failure_stage != "provenance"
            if verification.failure_stage == "evaluation_error":
                execution_ok = None
            if verification.reported is not None:
                for key, metric in case.metrics.items():
                    if key not in verification.reported:
                        continue
                    actual = verification.reported[key]
                    if not math.isfinite(actual) or not math.isfinite(metric.target):
                        raise ValueError("nonfinite submitted value")
                    errors[key] = abs(actual - metric.target) / max(
                        abs(metric.target), metric.absolute_tolerance
                    )
                numeric_ok = verification.passed and not compare_metrics(
                    case, verification.reported
                )
            if outcome.status == "completed":
                if (
                    outcome.verification_scope != "task_oracle"
                    or outcome.artifacts != verification.artifacts
                ):
                    raise ValueError("outcome does not match verification evidence")
                automatic_pass = numeric_ok
            elif (
                outcome.status == "failed"
                and verification.failure_stage != "evaluation_error"
            ):
                automatic_pass = False
        return MechanicsObservation(
            **values,
            metrics=metrics,
            evidence_sha256=hashes,
            execution_ok=execution_ok,
            numeric_ok=numeric_ok,
            automatic_pass=automatic_pass,
            relative_errors=errors,
            exclusion="posthoc" if posthoc else None,
            verification_feedback=verification.feedback
            if verification is not None
            else None,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        values["reason"] = f"Saved evidence could not be graded: {type(exc).__name__}"
        values["failure_kind"] = "invalid_evidence"
        return MechanicsObservation(
            **values,
            metrics=metrics,
            evidence_sha256=hashes,
            exclusion="invalid_evidence",
        )


@dataclass
class TaskPassed(Evaluator[str, MechanicsObservation, CaseMetadata]):
    def evaluate(
        self, ctx: EvaluatorContext[str, MechanicsObservation, CaseMetadata]
    ) -> EvaluatorOutput:
        result = ctx.output
        checks = (result.execution_ok, result.numeric_ok)
        passed: bool | None = None
        status = "unknown"
        if result.exclusion is not None:
            status = result.exclusion
        elif (
            result.status == "evaluation_error"
            or result.final_submission_status == "evaluation_error"
        ):
            status = "grading_error"
        elif result.status == "task_failed":
            passed = False
        elif result.status == "completed":
            if False in checks or result.automatic_pass is False:
                passed = False
            elif (
                all(value is True for value in checks) and result.automatic_pass is True
            ):
                passed = True
        if passed is not None:
            status = "passed" if passed else "failed"
        reason = result.reason
        if status == "posthoc":
            reason = (
                "Amended/revised or over-resumed run; retained as posthoc evidence."
            )
        elif status == "passed":
            reason = "Execution, deliverables and submitted numerical targets passed; modeling details are not independently verified."
        elif result.status == "completed" and passed is False:
            reason = (
                result.verification_feedback
                or "Independent verification rejected the result."
            )
        output: dict[str, bool | float | str | EvaluationReason] = {
            "task_status": EvaluationReason(status, reason=reason),
            "run_status": result.status,
            "failure_kind": result.failure_kind,
            "score_scope": "scored"
            if ctx.attributes.get("included_in_score")
            else "diagnostic",
        }
        if result.first_submission_status is not None:
            for prefix in ("first", "final"):
                submission_status = result.exclusion or getattr(
                    result, f"{prefix}_submission_status"
                )
                output[f"{prefix}_submission_status"] = submission_status
                if submission_status in {"passed", "failed", "not_submitted"}:
                    output[f"{prefix}_submission_pass"] = submission_status == "passed"
            output["submission_count"] = float(result.submission_count)
            output["feedback_rounds"] = float(result.feedback_rounds)
        for key in ("execution_ok", "numeric_ok", "automatic_pass"):
            value = getattr(result, key)
            if value is not None:
                check_reason = result.verification_feedback or result.reason
                output[key] = EvaluationReason(value=value, reason=check_reason)
        if passed is not None:
            output["task_pass"] = EvaluationReason(value=passed, reason=reason)
        output.update(
            {f"relative_error.{k}": v for k, v in result.relative_errors.items()}
        )
        return output


def native_task_verdict(
    case: ReportCase[str, MechanicsObservation, CaseMetadata] | None,
) -> str:
    if (
        case is not None
        and not case.evaluator_failures
        and "task_status" in case.labels
    ):
        return case.labels["task_status"].value
    return "grading_error"


@dataclass
class SuiteSummary(ReportEvaluator[str, MechanicsObservation, CaseMetadata]):
    planned_case_ids: tuple[str, ...]

    def __post_init__(self):
        if len(self.planned_case_ids) != len(set(self.planned_case_ids)):
            raise ValueError("duplicate planned case")

    def evaluate(
        self, ctx: ReportEvaluatorContext[str, MechanicsObservation, CaseMetadata]
    ):
        by_name = {case.name: case for case in ctx.report.cases}
        counts = dict.fromkeys(
            (
                "passed",
                "failed",
                "unknown",
                "posthoc",
                "invalid_evidence",
                "grading_error",
            ),
            0,
        )
        rows = []
        for name in self.planned_case_ids:
            case = by_name.get(name)
            status = native_task_verdict(case)
            if status not in counts:
                status = "grading_error"
            counts[status] += 1
            rows.append([name, status])
        analyses = [
            ScalarResult(title="planned_tasks", value=len(self.planned_case_ids)),
            *[
                ScalarResult(title=f"tasks_{key}", value=value)
                for key, value in counts.items()
            ],
        ]
        for prefix in ("first", "final"):
            label = f"{prefix}_submission_status"
            if not any(label in case.labels for case in by_name.values()):
                continue
            statuses = [
                by_name[name].labels[label].value
                if name in by_name
                and label in by_name[name].labels
                and not by_name[name].evaluator_failures
                else "evaluation_error"
                for name in self.planned_case_ids
            ]
            if statuses:
                for status in (
                    "passed",
                    "failed",
                    "not_submitted",
                    "evaluation_error",
                    "posthoc",
                    "invalid_evidence",
                ):
                    analyses.append(
                        ScalarResult(
                            title=f"{prefix}_submissions_{status}",
                            value=statuses.count(status),
                        )
                    )
                analyses.append(
                    ScalarResult(
                        title=f"{prefix}_submission_pass_rate",
                        value=statuses.count("passed") / len(self.planned_case_ids),
                    )
                )
        if self.planned_case_ids:
            analyses.append(
                ScalarResult(
                    title="task_pass_rate",
                    value=counts["passed"] / len(self.planned_case_ids),
                    description="Confirmed passes / planned selected scoring cases; incomplete and ungraded cases remain in the denominator.",
                )
            )
        resource_names = sorted(
            {
                key
                for name in self.planned_case_ids
                if name in by_name
                for key in by_name[name].metrics
            }
        )
        resources = []
        for key in resource_names:
            values = [
                by_name[name].metrics[key]
                for name in self.planned_case_ids
                if name in by_name and key in by_name[name].metrics
            ]
            resources.append(
                [key, sum(values), len(values), len(self.planned_case_ids)]
            )
        return [
            *analyses,
            TableResult(
                title="task_verdicts", columns=["case_id", "verdict"], rows=rows
            ),
            TableResult(
                title="recorded_resource_totals",
                columns=["metric", "sum_recorded", "cases_with_value", "planned_cases"],
                rows=resources,
                description="Only recorded values are summed. Missing values are unknown; summed active time is not batch elapsed time or billing.",
            ),
        ]


def build_mechanics_dataset(
    cases: tuple[MechanicsCase, ...],
    scoring_case_ids: tuple[str, ...],
    *,
    name: str,
) -> Dataset[str, MechanicsObservation, CaseMetadata]:
    planned = tuple(case.case_id for case in cases if case.case_id in scoring_case_ids)
    return Dataset[str, MechanicsObservation, CaseMetadata](
        name=name,
        cases=[
            Case(name=c.case_id, inputs=c.case_id, metadata=c.metadata) for c in cases
        ],
        evaluators=[TaskPassed()],
        report_evaluators=[SuiteSummary(planned)],
    )


def record_observation(
    result: MechanicsObservation, *, included_in_score: bool, duration_scope: str
) -> MechanicsObservation:
    for key, value in result.metrics.items():
        increment_eval_metric(key, value)
    set_eval_attribute("evidence_path", result.evidence_path)
    set_eval_attribute("evidence_sha256", result.evidence_sha256)
    set_eval_attribute("included_in_score", included_in_score)
    set_eval_attribute("duration_scope", duration_scope)
    return result


async def evaluate_saved_results(
    cases: tuple[MechanicsCase, ...],
    observations: dict[str, MechanicsObservation],
    scoring_case_ids: tuple[str, ...],
    experiment_metadata: dict,
) -> EvaluationReport[str, MechanicsObservation, CaseMetadata]:
    """通过同一组原生评分器汇总已存结果；不进入Agent、oracle或Solver。

    Note:
        此处task时间只是投影耗时。逐题Agent实际活动时间由保存的metrics提供。
    """
    dataset = build_mechanics_dataset(
        cases, scoring_case_ids, name="basic-linear-saved-results"
    )

    def project(case_id: str) -> MechanicsObservation:
        return record_observation(
            observations[case_id],
            included_in_score=case_id in scoring_case_ids,
            duration_scope="saved_result_projection_only",
        )

    return await dataset.evaluate(
        project,
        progress=False,
        max_concurrency=1,
        retry_task=None,
        retry_evaluators=None,
        repeat=1,
        metadata=experiment_metadata,
    )
