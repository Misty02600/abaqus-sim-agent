import asyncio
import hashlib
import json

import pytest
from pydantic_evals.reporting import EvaluationReportAdapter, ScalarResult

from abaqus_sim_agent.coding.contracts import (
    ArtifactRef,
    CodingRunOutcome,
    CodingRunStatus,
)
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.evaluation.mechanics_grading import (
    SuiteSummary,
    TaskPassed,
    evaluate_saved_results,
    observe_saved_case,
)
from abaqus_sim_agent.evaluation.public_mechanics_cases import (
    BASIC_LINEAR_CASE_IDS,
    PUBLIC_MECHANICS_PROTOCOL,
    get_case,
)
from abaqus_sim_agent.evaluation.public_mechanics_control import (
    build_report,
    build_report_async,
    read_json,
    save_json,
    summarize_case,
)
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.verification.public_mechanics_run import PublicMechanicsAttempt


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def freeze(root, names):
    save_json(
        root / "manifest.json",
        {
            "version": PUBLIC_MECHANICS_PROTOCOL,
            "case_ids": names,
            "scoring_case_ids": list(BASIC_LINEAR_CASE_IDS),
            "verification_policy": "submitted_results_v1",
            "max_explicit_resumes": 3,
        },
    )
    save_json(root / "cases.json", [get_case(n).model_dump(mode="json") for n in names])
    save_json(
        root / "seal.json",
        {
            "files": {
                p: digest((root / p).read_bytes())
                for p in ("manifest.json", "cases.json")
            }
        },
    )


def completed(root, name, *, amendment=0, failed=False, wrong_number=False):
    state = root / name / "state"
    case = get_case(name)
    measured = {key: metric.target for key, metric in case.metrics.items()}
    if wrong_number:
        measured[next(iter(measured))] *= 1.5
    artifact = ArtifactRef.model_validate_json(
        json.dumps(
            {
                "relative_path": "model.odb",
                "size_bytes": 1,
                "sha256": "a" * 64,
                "kind": "odb",
                "fresh": True,
            }
        )
    )
    verification = PublicMechanicsAttempt(
        passed=True,
        feedback="fixture host verification",
        artifacts=(artifact,),
        reported=measured,
        verification_policy="submitted_results_v1",
    )
    outcome = CodingRunOutcome(
        run_id="fixture-" + name,
        summary="fixture completion",
        status=CodingRunStatus.FAILED if failed else CodingRunStatus.COMPLETED,
        failure_stage="model_budget" if failed else None,
        amendment_sequence=amendment,
        artifacts=(artifact,),
        verification_scope="task_oracle",
    )
    save_json(state / "outcome.json", outcome.model_dump(mode="json"))
    line = json.dumps(
        {
            "event": "verification",
            "task_revision": 0,
            "amendment_sequence": amendment,
            "data": verification.model_dump(mode="json"),
        },
        ensure_ascii=False,
    ).encode("utf-8")
    (state / "events.jsonl").write_bytes(line + b"\n")
    return state


def native_report(root):
    return EvaluationReportAdapter.validate_json(
        (root / "pydantic-evals-report.json").read_bytes()
    )


def test_native_rules_keep_planned_denominator_and_all_evidence_layers(
    tmp_path, monkeypatch
):
    names = [
        "cantilever-uniform-load",
        "circular-shaft-pure-torsion",
        "thick-cylinder-internal-pressure",
        "plate-with-hole-tension",
        "solid-cantilever-directional-bending",
        "nafems-le6-skew-plate",
        "uniform-axial-bar",
    ]
    freeze(tmp_path, names)
    completed(tmp_path, names[0])
    completed(tmp_path, names[1])
    completed(tmp_path, names[2], failed=True)
    save_json(
        tmp_path / names[3] / "state/resume_state.json",
        {
            "status": "interrupted",
            "last_error_type": "ModelAPIError",
            "usage": {"requests": 3},
            "dependencies": {},
        },
    )
    # VM184 intentionally never started; it must stay in the selected denominator.
    completed(tmp_path, names[5], amendment=1)
    completed(tmp_path, names[6])  # Historical success is not a scoring vote.
    inputs = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    async def forbidden(*args, **kwargs):
        raise AssertionError("result grading must never run an external effect")

    monkeypatch.setattr(CodingRunService, "run", forbidden)
    monkeypatch.setattr(Abaqus2024Runner, "run_cae_script", forbidden)
    monkeypatch.setattr(Abaqus2024Runner, "run_solver_job", forbidden)
    monkeypatch.setattr(Abaqus2024Runner, "run_trusted_abaqus_python", forbidden)
    report = build_report(tmp_path, bindings_unchanged=True)
    summary = report["scoring"]["task_summary"]
    assert summary["planned_tasks"] == 6
    assert summary["tasks_passed"] == 2
    assert summary["tasks_failed"] == 1
    assert "tasks_pending_review" not in summary
    assert summary["tasks_unknown"] == 2
    assert summary["tasks_posthoc"] == 1
    assert summary["task_pass_rate"] == pytest.approx(2 / 6)
    assert not report["scoring"]["full_set_selected"]
    native = native_report(tmp_path)
    assert len(native.cases) == 7
    assert not native.failures
    by_name = {case.name: case for case in native.cases}
    assert by_name[names[1]].assertions["automatic_pass"].value is True
    assert by_name[names[1]].assertions["task_pass"].value is True
    assert by_name[names[2]].assertions["numeric_ok"].value is True
    assert by_name[names[2]].assertions["task_pass"].value is False
    assert by_name[names[3]].labels["run_status"].value == "provider_interrupted"
    assert "numeric_ok" not in by_name[names[3]].assertions
    assert by_name[names[6]].labels["score_scope"].value == "diagnostic"
    assert by_name[names[3]].metrics["requests"] == 3
    assert "input_tokens" not in by_name[names[3]].metrics
    assert all(p.read_bytes() == raw for p, raw in inputs.items())
    second = asyncio.run(build_report_async(tmp_path, bindings_unchanged=True))
    assert second["scoring"]["task_summary"] == summary
    assert all(p.read_bytes() == raw for p, raw in inputs.items())


def test_default_assertion_average_is_not_task_pass_rate(tmp_path):
    names = ["cantilever-uniform-load", "circular-shaft-pure-torsion"]
    freeze(tmp_path, names)
    completed(tmp_path, names[0])
    report = build_report(tmp_path, bindings_unchanged=True)
    native = native_report(tmp_path)
    assert native.averages().assertions == 1
    assert report["scoring"]["task_summary"]["task_pass_rate"] == 0.5


@pytest.mark.parametrize(
    "defect", ["outcome", "event", "corrupt_event", "policy", "missing_event", "scope"]
)
def test_incomplete_or_mismatched_evidence_cannot_become_task_pass(tmp_path, defect):
    name = "circular-shaft-pure-torsion"
    freeze(tmp_path, [name])
    state = completed(tmp_path, name)
    if defect == "outcome":
        value = read_json(state / "outcome.json")
        value["artifacts"] = []
        save_json(state / "outcome.json", value)
    elif defect == "missing_event":
        (state / "events.jsonl").unlink()
    elif defect == "corrupt_event":
        (state / "events.jsonl").write_text("not-json", encoding="utf-8")
    else:
        event = json.loads((state / "events.jsonl").read_bytes())
        if defect == "event":
            event["data"]["artifacts"] = []
        elif defect == "policy":
            event["data"]["verification_policy"] = "strict_contract_v1"
        else:
            event["data"]["scope"] = "execution_evidence"
        (state / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    report = build_report(tmp_path, bindings_unchanged=True)
    assert report["scoring"]["task_summary"]["tasks_passed"] == 0
    assert report["trials"][0]["task_verdict"] in {"invalid_evidence", "unknown"}


def test_host_pass_flag_cannot_override_saved_numeric_failure(tmp_path):
    name = "circular-shaft-pure-torsion"
    freeze(tmp_path, [name])
    completed(tmp_path, name, wrong_number=True)
    report = build_report(tmp_path, bindings_unchanged=True)
    assert report["trials"][0]["task_verdict"] == "failed"
    native = native_report(tmp_path)
    assert native.cases[0].assertions["numeric_ok"].value is False
    assert any(
        score.value == pytest.approx(0.5) for score in native.cases[0].scores.values()
    )


def test_unrelated_review_records_do_not_gate_numeric_success(tmp_path):
    name = "circular-shaft-pure-torsion"
    freeze(tmp_path, [name])
    state = completed(tmp_path, name)
    original = (state / "outcome.json").read_bytes()
    path = tmp_path / "reviews" / f"{name}.json"
    save_json(
        path,
        {
            "passed": False,
            "reason": "legacy parser complaint: surface load or FOLLOWER=NO",
        },
    )
    report = build_report(tmp_path, bindings_unchanged=True)
    assert report["trials"][0]["task_verdict"] == "passed"
    assert (state / "outcome.json").read_bytes() == original


def test_excess_resumes_and_changed_bindings_are_not_formal_passes(tmp_path):
    name = "circular-shaft-pure-torsion"
    freeze(tmp_path, [name])
    state = completed(tmp_path, name)
    for n in range(4):
        save_json(state / "attempts" / f"{n}.json", {"mode": "resume"})
    assert (
        build_report(tmp_path, bindings_unchanged=True)["trials"][0]["task_verdict"]
        == "posthoc"
    )
    assert (
        build_report(tmp_path, bindings_unchanged=False)["trials"][0]["task_verdict"]
        == "invalid_evidence"
    )


def test_native_task_or_evaluator_exceptions_remain_in_denominator(
    tmp_path, monkeypatch
):
    name = "circular-shaft-pure-torsion"
    freeze(tmp_path, [name])
    completed(tmp_path, name)
    case = get_case(name)
    observed = observe_saved_case(
        tmp_path,
        case,
        summarize_case(tmp_path, name),
        read_json(tmp_path / "manifest.json"),
        bindings_unchanged=True,
    )
    failure = asyncio.run(evaluate_saved_results((case,), {}, (name,), {}))
    assert len(failure.failures) == 1
    values = {a.title: a.value for a in failure.analyses if isinstance(a, ScalarResult)}
    assert values["planned_tasks"] == values["tasks_grading_error"] == 1
    assert values["task_pass_rate"] == 0

    def broken(self, ctx):
        raise RuntimeError("fixture grader error")

    monkeypatch.setattr(TaskPassed, "evaluate", broken)
    report = asyncio.run(evaluate_saved_results((case,), {name: observed}, (name,), {}))
    assert report.cases[0].evaluator_failures
    values = {a.title: a.value for a in report.analyses if isinstance(a, ScalarResult)}
    assert values["tasks_grading_error"] == 1
    assert values["task_pass_rate"] == 0


def test_vm184_counts_once_and_historical_only_has_no_rate(tmp_path):
    name = "solid-cantilever-directional-bending"
    freeze(tmp_path, [name])
    completed(tmp_path, name)
    summary = build_report(tmp_path, bindings_unchanged=True)["scoring"]["task_summary"]
    assert summary["planned_tasks"] == summary["tasks_passed"] == 1
    assert summary["task_pass_rate"] == 1
    other = tmp_path / "history"
    freeze(other, ["uniform-axial-bar"])
    completed(other, "uniform-axial-bar")
    summary = build_report(other, bindings_unchanged=True)["scoring"]["task_summary"]
    assert summary["planned_tasks"] == 0
    assert "task_pass_rate" not in summary
    assert "没有计分题" in (other / "report.md").read_text(encoding="utf-8")


def test_report_evaluator_failure_is_visible_without_a_fake_zero_summary(
    tmp_path, monkeypatch
):
    freeze(tmp_path, ["circular-shaft-pure-torsion"])
    completed(tmp_path, "circular-shaft-pure-torsion")

    def broken(self, ctx):
        raise RuntimeError("fixture summary failed")

    monkeypatch.setattr(SuiteSummary, "evaluate", broken)
    report = build_report(tmp_path, bindings_unchanged=True)
    assert report["scoring"]["summary_valid"] is False
    assert not report["scoring"]["task_summary"]
    assert native_report(tmp_path).report_evaluator_failures
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "整批评分器异常" in text
    assert "0/0" not in text


@pytest.mark.parametrize(
    "defect",
    ["missing_case", "metadata", "evidence", "bindings", "verification", "usage"],
)
def test_execution_report_mismatch_cannot_overwrite_current_report(tmp_path, defect):
    name = BASIC_LINEAR_CASE_IDS[0]
    freeze(tmp_path, [name])
    completed(tmp_path, name)
    case = get_case(name)
    manifest = read_json(tmp_path / "manifest.json")
    observation = observe_saved_case(
        tmp_path,
        case,
        summarize_case(tmp_path, name),
        manifest,
        bindings_unchanged=True,
    )
    native = asyncio.run(
        evaluate_saved_results((case,), {name: observation}, BASIC_LINEAR_CASE_IDS, {})
    )
    if defect == "missing_case":
        native.cases.clear()
    elif defect == "metadata":
        native.cases[0].metadata = case.metadata.model_copy(
            update={"description": "different task"}
        )
    elif defect == "evidence":
        path = tmp_path / name / "state/outcome.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif defect == "verification":
        path = tmp_path / name / "state/events.jsonl"
        event = json.loads(path.read_bytes())
        event["data"]["feedback"] = "changed execution evidence"
        path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    elif defect == "usage":
        path = tmp_path / name / "state/resume_state.json"
        saved = read_json(path) or {}
        saved["usage"] = {"requests": 999}
        save_json(path, saved)
    original = b"existing-report"
    (tmp_path / "report.json").write_bytes(original)
    with pytest.raises(ValueError, match="native execution report"):
        asyncio.run(
            build_report_async(
                tmp_path, bindings_unchanged=defect != "bindings", native_report=native
            )
        )
    assert (tmp_path / "report.json").read_bytes() == original
