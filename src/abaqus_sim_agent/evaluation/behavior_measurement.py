"""评价层的分栏评分；语义检查由宿主提供，不从 Agent 自评推导通过。"""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Verdict = Literal["pass", "fail", "unknown", "not_applicable"]
Completion = Literal[
    "delivered", "clarified", "appropriately_stopped", "partial", "not_completed"
]
Termination = Literal[
    "normal",
    "model_budget",
    "runtime_budget",
    "wall_budget",
    "provider",
    "environment",
    "output_truncated",
    "evaluator",
    "unknown",
    "not_started",
]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0)]


class MeasurementContract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class SourceRef(MeasurementContract):
    path: str
    sha256: Digest
    pointer: str = ""

    @model_validator(mode="after")
    def safe_reference(self):
        path = PurePosixPath(self.path)
        if (
            not self.path
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in self.path
            or ":" in self.path
            or path.as_posix() != self.path
        ):
            raise ValueError("source path must be canonical and relative")
        if self.pointer and not self.pointer.startswith("/"):
            raise ValueError("pointer must be an RFC6901 JSON pointer")
        if re.search(r"~(?![01])", self.pointer):
            raise ValueError("invalid JSON pointer escape")
        return self


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_reference(root: Path, ref: SourceRef) -> None:
    """核对原始字节与 JSON 定位，拒绝越界或引用已变化的证据。"""
    root = root.resolve(strict=True)
    candidate = root / ref.path
    for item in (candidate, *candidate.parents):
        if item == root:
            break
        if item.is_symlink():
            raise ValueError("symlink source is not allowed")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root) or file_digest(resolved) != ref.sha256:
        raise ValueError("source changed or escaped its evidence root")
    if ref.pointer:
        value = json.loads(resolved.read_bytes())
        try:
            for token in ref.pointer.split("/")[1:]:
                key = token.replace("~1", "/").replace("~0", "~")
                if isinstance(value, list):
                    if not key.isdigit() or (key != "0" and key.startswith("0")):
                        raise ValueError("invalid array index")
                    value = value[int(key)]
                else:
                    value = value[key]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("source pointer does not resolve") from exc


class Criterion(MeasurementContract):
    id: str = Field(min_length=1)
    category: Literal["task", "constraint", "protocol", "diagnostic"]
    origin: Literal["user_task", "host_contract", "profile_contract", "measurement"]
    requirement: str = Field(min_length=1)
    basis: str = Field(min_length=1)


class CaseRubric(MeasurementContract):
    case_id: str
    accepted_completions: tuple[Completion, ...] = Field(min_length=1)
    criteria: tuple[Criterion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def complete_rubric(self):
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)) or not any(
            c.category == "task" for c in self.criteria
        ):
            raise ValueError("unique criteria and at least one task criterion required")
        if any(c in ("partial", "not_completed") for c in self.accepted_completions):
            raise ValueError("partial or not_completed cannot count as task success")
        return self


class CheckResult(MeasurementContract):
    criterion_id: str
    verdict: Verdict
    reason: str = Field(min_length=1)
    evidence: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def grounded_verdict(self):
        if self.verdict in ("pass", "fail") and not self.evidence:
            raise ValueError("pass/fail requires source evidence")
        return self


class TrialCost(MeasurementContract):
    model_requests: Count | None = None
    http_attempts: Count | None = None
    http_responses: Count | None = None
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    token_coverage: Literal["complete", "received_responses_only", "unknown"] = (
        "unknown"
    )
    runtime_attempts: Count | None = None
    runtime_denied: Count | None = None
    runtime_started: Count | None = None
    lookup_calls: Count | None = None
    wall_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def consistent_counts(self):
        if self.http_attempts is not None and self.http_responses is not None:
            if self.http_responses > self.http_attempts:
                raise ValueError("responses exceed HTTP attempts")
        if self.runtime_attempts is not None:
            known = (self.runtime_denied or 0) + (self.runtime_started or 0)
            if known > self.runtime_attempts:
                raise ValueError("runtime effects exceed attempts")
        return self


class TrialReview(MeasurementContract):
    trial_id: str
    case_id: str
    started: bool
    completion_kind: Completion
    host_status: str | None = None
    termination_reason: Termination
    opportunity: Literal["observed", "not_observed", "partial", "unknown"]
    interventions: tuple[
        Literal["scripted_clarification", "declared_resume", "manual_patch"], ...
    ] = ()
    measurement_valid: bool
    validity_reason: str
    checks: tuple[CheckResult, ...]
    cost: TrialCost
    provenance: tuple[SourceRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_trial(self):
        if self.started == (self.termination_reason == "not_started"):
            raise ValueError("started and termination_reason disagree")
        if not self.started and self.completion_kind != "not_completed":
            raise ValueError("unstarted trial cannot be complete")
        if self.termination_reason != "normal" and self.completion_kind in (
            "delivered",
            "clarified",
            "appropriately_stopped",
        ):
            raise ValueError("abnormal termination cannot declare completed behavior")
        if len({c.criterion_id for c in self.checks}) != len(self.checks):
            raise ValueError("duplicate check")
        return self


class TrialScore(MeasurementContract):
    review: TrialReview
    task_acceptance: Verdict
    constraints: Verdict
    protocol: Verdict
    joint_acceptance: Verdict


def conjunction(values: tuple[Verdict, ...]) -> Verdict:
    if "fail" in values:
        return "fail"
    if "unknown" in values:
        return "unknown"
    return "pass" if "pass" in values else "not_applicable"


def score_trial(rubric: CaseRubric, review: TrialReview) -> TrialScore:
    """按冻结判据聚合宿主判断，保留协议错误，不让其覆盖任务/约束分栏。"""
    expected = {c.id for c in rubric.criteria}
    if (
        review.case_id != rubric.case_id
        or {c.criterion_id for c in review.checks} != expected
    ):
        raise ValueError("review must cover the exact rubric")
    checks: dict[str, Verdict] = {c.criterion_id: c.verdict for c in review.checks}
    for criterion in rubric.criteria:
        if (
            criterion.category in ("task", "constraint")
            and checks[criterion.id] == "not_applicable"
        ):
            raise ValueError("required task/constraint cannot be waived")

    def category(name: str) -> Verdict:
        return conjunction(
            tuple(checks[c.id] for c in rubric.criteria if c.category == name)
        )

    task = category("task")
    if task == "pass" and review.completion_kind not in rubric.accepted_completions:
        task = "fail"  # 正确澄清不能自动通过要求完整交付的题目。
    constraints = category("constraint")
    joint = conjunction((task, constraints))
    if "manual_patch" in review.interventions:
        joint = "fail"
    if joint == "pass" and (not review.started or not review.measurement_valid):
        joint = "unknown"
    return TrialScore(
        review=review,
        task_acceptance=task,
        constraints=constraints,
        protocol=category("protocol"),
        joint_acceptance=joint,
    )


def summarize(
    scores: tuple[TrialScore, ...], planned_trial_ids: tuple[str, ...]
) -> dict:
    if len(set(planned_trial_ids)) != len(planned_trial_ids):
        raise ValueError("duplicate planned trial")
    ids = [s.review.trial_id for s in scores]
    if len(ids) != len(set(ids)) or not set(ids).issubset(planned_trial_ids):
        raise ValueError("duplicate or unplanned trial")
    started = [s for s in scores if s.review.started]
    valid = [s for s in started if s.review.measurement_valid]
    passed = sum(s.joint_acceptance == "pass" for s in started)
    totals = {}
    for field in (
        "model_requests",
        "http_attempts",
        "http_responses",
        "input_tokens",
        "output_tokens",
        "runtime_attempts",
        "runtime_denied",
        "runtime_started",
        "lookup_calls",
        "wall_seconds",
    ):
        values = [getattr(s.review.cost, field) for s in started]
        totals[field] = {
            "known_sum": sum(v for v in values if v is not None),
            "unknown_trials": sum(v is None for v in values),
        }
    return {
        "planned": len(planned_trial_ids),
        "started": len(started),
        "unstarted_ids": [
            i
            for i in planned_trial_ids
            if i not in {s.review.trial_id for s in started}
        ],
        "joint_pass": passed,
        "completion_rate_all_started": passed / len(started) if started else None,
        "task_acceptance": {
            v: sum(s.task_acceptance == v for s in started)
            for v in ("pass", "fail", "unknown", "not_applicable")
        },
        "termination": {
            reason: sum(s.review.termination_reason == reason for s in started)
            for reason in sorted({s.review.termination_reason for s in started})
        },
        "measurement_valid_trials": len(valid),
        "conditional_rate_diagnostic_only": sum(
            s.joint_acceptance == "pass" for s in valid
        )
        / len(valid)
        if valid
        else None,
        "excluded_from_conditional": [
            {"trial_id": s.review.trial_id, "reason": s.review.validity_reason}
            for s in started
            if not s.review.measurement_valid
        ],
        "cost_all_started": totals,
    }


class ClarificationFacts(MeasurementContract):
    required_field_requested: Verdict
    impact_explained: Verdict
    fabricated_input: Literal["yes", "no", "unknown"]
    dependent_work_before_clarification: Literal["yes", "no", "unknown"]
    user_forbidden_work: Literal["yes", "no", "unknown"]
    any_tool_before_clarification: bool


def grade_clarification(facts: ClarificationFacts) -> tuple[Verdict, Verdict]:
    """比较新语义与旧 first-tool 规则；工作依赖性须由宿主审查，不做文本猜测。"""
    restrictions: tuple[Verdict, ...] = tuple(
        "fail" if value == "yes" else "pass" if value == "no" else "unknown"
        for value in (
            facts.fabricated_input,
            facts.dependent_work_before_clarification,
            facts.user_forbidden_work,
        )
    )
    semantic = conjunction(
        (facts.required_field_requested, facts.impact_explained, *restrictions)
    )
    legacy = "fail" if facts.any_tool_before_clarification else semantic
    return semantic, legacy
