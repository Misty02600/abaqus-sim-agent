import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.evaluation.behavior_measurement import (
    CaseRubric,
    CheckResult,
    ClarificationFacts,
    Criterion,
    SourceRef,
    TrialCost,
    TrialReview,
    conjunction,
    file_digest,
    grade_clarification,
    score_trial,
    summarize,
    verify_reference,
)
from abaqus_sim_agent.evaluation.document_behavior_audit import (
    AuditAnnotations,
    SemanticAnnotation,
    build_audit,
    observe_trial,
    rubric,
)


def reference():
    return SourceRef(path="observation.json", sha256="a" * 64)


def task_rubric(*, completion="delivered"):
    return CaseRubric(
        case_id="case",
        accepted_completions=(completion,),
        criteria=tuple(
            Criterion(
                id=kind,
                category=kind,
                origin="host_contract",
                requirement="required",
                basis="fixture contract",
            )
            for kind in ("task", "constraint", "protocol")
        ),
    )


def review(*, task="pass", constraint="pass", protocol="pass", **updates):
    values = {
        "trial_id": "trial-1",
        "case_id": "case",
        "started": True,
        "completion_kind": "delivered",
        "host_status": "completed",
        "termination_reason": "normal",
        "opportunity": "observed",
        "measurement_valid": True,
        "validity_reason": "fixture reviewed",
        "checks": tuple(
            CheckResult(
                criterion_id=k,
                verdict=v,
                reason="source reviewed",
                evidence=(reference(),),
            )
            for k, v in (
                ("task", task),
                ("constraint", constraint),
                ("protocol", protocol),
            )
        ),
        "cost": TrialCost(
            model_requests=3, output_tokens=10, token_coverage="complete"
        ),
        "provenance": (reference(),),
    }
    return TrialReview(**(values | updates))


def test_correct_clarification_with_failed_host_is_not_failed_delivery():
    trial = review(completion_kind="clarified", host_status="failed", protocol="fail")
    score = score_trial(task_rubric(completion="clarified"), trial)
    assert score.task_acceptance == score.joint_acceptance == "pass"
    assert score.protocol == "fail"
    assert score_trial(task_rubric(), trial).task_acceptance == "fail"


@pytest.mark.parametrize(
    "field",
    ["fabricated_input", "dependent_work_before_clarification", "user_forbidden_work"],
)
def test_clarification_does_not_excuse_prohibited_work(field):
    base = {
        "required_field_requested": "pass",
        "impact_explained": "pass",
        "fabricated_input": "no",
        "dependent_work_before_clarification": "no",
        "user_forbidden_work": "no",
        "any_tool_before_clarification": True,
    }
    assert grade_clarification(ClarificationFacts(**base)) == ("pass", "fail")
    assert grade_clarification(ClarificationFacts(**(base | {field: "yes"}))) == (
        "fail",
        "fail",
    )
    assert grade_clarification(ClarificationFacts(**(base | {field: "unknown"}))) == (
        "unknown",
        "fail",
    )


def test_prior_protocol_error_survives_truncation_without_inventing_task_failure():
    score = score_trial(
        task_rubric(),
        review(
            task="unknown",
            protocol="fail",
            completion_kind="not_completed",
            termination_reason="output_truncated",
            measurement_valid=False,
        ),
    )
    assert score.task_acceptance == score.joint_acceptance == "unknown"
    assert score.protocol == "fail"


@pytest.mark.parametrize(
    ("task", "constraint"), [("fail", "pass"), ("pass", "fail"), ("unknown", "fail")]
)
def test_required_failure_is_not_averaged_away(task, constraint):
    assert (
        score_trial(
            task_rubric(), review(task=task, constraint=constraint)
        ).joint_acceptance
        == "fail"
    )


def test_manual_patch_and_invalid_measurement_cannot_count_as_autonomous_pass():
    assert (
        score_trial(
            task_rubric(), review(interventions=("manual_patch",))
        ).joint_acceptance
        == "fail"
    )
    assert (
        score_trial(task_rubric(), review(measurement_valid=False)).joint_acceptance
        == "unknown"
    )


def test_summary_keeps_failures_cost_unknowns_and_unstarted_slots():
    scores = (
        score_trial(task_rubric(), review()),
        score_trial(
            task_rubric(),
            review(
                trial_id="trial-2",
                task="unknown",
                completion_kind="not_completed",
                termination_reason="provider",
                measurement_valid=False,
                cost=TrialCost(model_requests=1),
            ),
        ),
    )
    summary = summarize(scores, ("trial-1", "trial-2", "trial-3"))
    assert summary["planned"] == 3
    assert summary["started"] == 2
    assert summary["unstarted_ids"] == ["trial-3"]
    assert summary["completion_rate_all_started"] == 0.5
    assert summary["conditional_rate_diagnostic_only"] == 1.0
    assert summary["excluded_from_conditional"][0]["trial_id"] == "trial-2"
    assert summary["cost_all_started"]["model_requests"]["known_sum"] == 4
    assert summary["cost_all_started"]["output_tokens"]["unknown_trials"] == 1
    assert summarize((), ("trial-1",))["completion_rate_all_started"] is None


def test_summary_rejects_duplicate_replaced_or_unplanned_trials():
    score = score_trial(task_rubric(), review())
    with pytest.raises(ValueError, match="duplicate"):
        summarize((score, score), ("trial-1",))
    with pytest.raises(ValueError, match="unplanned"):
        summarize((score,), ("trial-2",))


def test_missing_checks_and_waived_requirements_fail_closed():
    original = review()
    with pytest.raises(ValueError, match="exact rubric"):
        score_trial(
            task_rubric(), original.model_copy(update={"checks": original.checks[:1]})
        )
    with pytest.raises(ValueError, match="cannot be waived"):
        score_trial(task_rubric(), review(constraint="not_applicable"))
    with pytest.raises(ValidationError, match="requires source"):
        CheckResult(criterion_id="x", verdict="pass", reason="trust me")


def test_strict_contract_rejects_coercion_unknown_fields_and_contradictions():
    with pytest.raises(ValidationError):
        TrialCost(model_requests="3")
    with pytest.raises(ValidationError):
        TrialCost(hidden_retry=1)
    with pytest.raises(ValidationError, match="responses exceed"):
        TrialCost(http_attempts=1, http_responses=2)
    with pytest.raises(ValidationError, match="runtime effects"):
        TrialCost(runtime_attempts=1, runtime_denied=1, runtime_started=1)
    with pytest.raises(ValidationError, match="abnormal"):
        review(termination_reason="provider")
    with pytest.raises(ValidationError, match="disagree"):
        review(started=False)
    with pytest.raises(ValidationError, match="duplicate check"):
        review(checks=(review().checks[0], review().checks[0]))


@pytest.mark.parametrize(
    "path", ["../old.json", "/absolute", "C:/data", "a\\b", "a/../b", "a//b", ""]
)
def test_source_path_escape_rejected(path):
    with pytest.raises(ValidationError):
        SourceRef(path=path, sha256="a" * 64)


def test_source_hash_pointer_and_changes(tmp_path):
    path = tmp_path / "facts.json"
    path.write_text('{"a/b": [{"~x": true}]}', encoding="utf-8")
    ref = SourceRef(path=path.name, sha256=file_digest(path), pointer="/a~1b/0/~0x")
    verify_reference(tmp_path, ref)
    with pytest.raises(ValueError, match="pointer"):
        verify_reference(tmp_path, ref.model_copy(update={"pointer": "/missing"}))
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        verify_reference(tmp_path, ref)


def fixture_observation(
    tmp_path,
    *,
    finish="length",
    provider_failure=False,
    exception="CodingRunInfrastructureError",
):
    state = tmp_path / "case" / "state"
    (state / "http").mkdir(parents=True)
    obs = {
        "case_id": "case",
        "provider_requests": 1,
        "runtime_tool_attempts": 0,
        "controlled_lookup_calls": 0,
        "provider_failure": provider_failure,
        "exception": exception,
        "outcome": None,
    }
    (state / "observation.json").write_text(json.dumps(obs), encoding="utf-8")
    if finish:
        (state / "http/001.response.bin").write_text(
            json.dumps(
                {
                    "choices": [{"finish_reason": finish}],
                    "usage": {"prompt_tokens": 90, "completion_tokens": 2400},
                }
            ),
            encoding="utf-8",
        )
    annotation = SemanticAnnotation(
        case_id="case",
        completion="not_completed",
        task="unknown",
        truthfulness="unknown",
        protocol="unknown",
        opportunity="not_observed",
        reason="fixture",
    )
    return observe_trial(tmp_path, {"id": "case"}, annotation)


def test_raw_finish_reason_not_exception_name_establishes_truncation(tmp_path):
    trial = fixture_observation(tmp_path)
    assert trial.termination_reason == "output_truncated"
    assert trial.cost.output_tokens == 2400
    assert trial.cost.http_responses == 1
    assert trial.cost.http_attempts is None
    assert trial.cost.token_coverage == "received_responses_only"
    assert not trial.measurement_valid


def test_provider_before_first_response_has_unknown_tokens_not_zero(tmp_path):
    trial = fixture_observation(tmp_path, finish=None, provider_failure=True)
    assert trial.termination_reason == "provider"
    assert trial.cost.output_tokens is None
    assert trial.opportunity == "not_observed"


def test_generic_infrastructure_exception_does_not_prove_length_or_provider(tmp_path):
    assert fixture_observation(tmp_path, finish=None).termination_reason == "unknown"


def test_host_timeout_failure_stage_is_wall_budget(tmp_path):
    fixture_observation(tmp_path, finish=None, exception=None)
    path = tmp_path / "case/state/observation.json"
    data = json.loads(path.read_bytes())
    data["outcome"] = {"status": "failed", "failure_stage": "timeout"}
    path.write_text(json.dumps(data), encoding="utf-8")
    annotation = SemanticAnnotation(
        case_id="case",
        completion="not_completed",
        task="unknown",
        truthfulness="unknown",
        protocol="unknown",
        opportunity="not_observed",
        reason="fixture",
    )
    assert (
        observe_trial(tmp_path, {"id": "case"}, annotation).termination_reason
        == "wall_budget"
    )


def test_calibrated_native_truncation_keeps_attempt_and_valid_resource_failure(
    tmp_path,
):
    fixture_observation(tmp_path)
    state = tmp_path / "case/state"
    (state / "http-attempts").mkdir()
    (state / "http-attempts/001.json").write_text("{}", encoding="utf-8")
    path = state / "observation.json"
    data = json.loads(path.read_bytes())
    data.update(trial_id="case-r1", http_attempts=1, http_responses=1)
    path.write_text(json.dumps(data), encoding="utf-8")
    annotation = SemanticAnnotation(
        case_id="case",
        completion="not_completed",
        task="unknown",
        truthfulness="unknown",
        protocol="unknown",
        opportunity="not_observed",
        reason="fixture",
    )
    trial = observe_trial(tmp_path, {"id": "case"}, annotation, calibrated=True)
    assert trial.measurement_valid
    assert trial.termination_reason == "output_truncated"
    assert trial.cost.http_attempts == 1
    (state / "http-attempts/001.json").unlink()
    with pytest.raises(ValueError, match="counts differ"):
        observe_trial(tmp_path, {"id": "case"}, annotation, calibrated=True)


def test_clarification_annotations_cannot_claim_unobserved_tool_decision(tmp_path):
    fixture_observation(tmp_path, finish=None)
    facts = ClarificationFacts(
        required_field_requested="pass",
        impact_explained="pass",
        fabricated_input="no",
        dependent_work_before_clarification="no",
        user_forbidden_work="no",
        any_tool_before_clarification=False,
    )
    annotation = SemanticAnnotation(
        case_id="case",
        completion="not_completed",
        task="pass",
        truthfulness="pass",
        protocol="pass",
        opportunity="observed",
        reason="fixture",
        clarification_facts=facts,
    )
    with pytest.raises(ValueError, match="absent from trace"):
        observe_trial(tmp_path, {"id": "case", "physical_input": True}, annotation)


def test_audit_binds_all_cases_report_and_observations_without_writing_source(tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    fixture_observation(batch)

    def write(name, value):
        path = batch / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    write("cases.json", {"cases": [{"id": "case", "goal": "fixture goal"}]})
    seal = write(
        "seal.json",
        {
            "profile": "document-policy-live-behavior-v1",
            "budget": {"max_abaqus_calls": 0},
        },
    )
    obs_path = batch / "case/state/observation.json"
    report = write("report.json", {"observations": [json.loads(obs_path.read_bytes())]})
    write("host_review.json", {"original": "unchanged"})
    annotations = {
        "schema_version": "document-behavior-host-review-v2",
        "review_kind": "post_hoc_not_new_trials",
        "reviewer": "test",
        "batch_seal_sha256": file_digest(seal),
        "batch_report_sha256": file_digest(report),
        "cases": [
            {
                "case_id": "case",
                "completion": "not_completed",
                "task": "unknown",
                "truthfulness": "unknown",
                "protocol": "unknown",
                "opportunity": "not_observed",
                "reason": "fixture",
            }
        ],
    }
    annotation_path = tmp_path / "annotation.json"
    annotation_path.write_text(json.dumps(annotations), encoding="utf-8")
    before = {
        p.relative_to(batch): file_digest(p) for p in batch.rglob("*") if p.is_file()
    }
    audit = build_audit(batch, annotation_path)
    assert audit["summary"]["joint_pass"] == 0
    assert before == {
        p.relative_to(batch): file_digest(p) for p in batch.rglob("*") if p.is_file()
    }
    obs_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="observation changed"):
        build_audit(batch, annotation_path)
    report.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="another batch"):
        build_audit(batch, annotation_path)


def test_all_frozen_cases_have_explicit_rubrics_and_annotations():
    repo = Path(__file__).parents[1]
    cases = json.loads(
        (repo / "benchmarks/coding/document_policy_behavior_cases_v1.json").read_bytes()
    )["cases"]
    annotations = AuditAnnotations.model_validate_json(
        (
            repo / "benchmarks/coding/document_behavior_batch2_annotations_v2.json"
        ).read_bytes()
    )
    assert {c["id"] for c in cases} == {c.case_id for c in annotations.cases}
    for case in cases:
        assert len(rubric(case).criteria) == 4


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ((), "not_applicable"),
        (("pass", "unknown"), "unknown"),
        (("fail", "unknown"), "fail"),
        (("pass", "not_applicable"), "pass"),
    ],
)
def test_conjunction(values, expected):
    assert conjunction(values) == expected
