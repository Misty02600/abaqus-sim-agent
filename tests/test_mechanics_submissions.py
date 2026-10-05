import asyncio
import json

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunRequest,
    LimitedCount,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.service import (
    CodingRunInfrastructureError,
    CodingRunService,
)
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.evaluation.mechanics_grading import (
    evaluate_saved_results,
    observe_saved_case,
)
from abaqus_sim_agent.evaluation.mechanics_submissions import (
    SUBMISSION_POLICY,
    MechanicsSubmissionVerifier,
    load_submissions,
    model_feedback,
)
from abaqus_sim_agent.evaluation.public_mechanics_cases import get_case, mechanics_cases
from abaqus_sim_agent.evaluation.public_mechanics_control import summarize_case
from abaqus_sim_agent.execution import Abaqus2024Runner, ProcessOutput
from abaqus_sim_agent.verification.public_mechanics_run import (
    PublicMechanicsAttempt,
    PublicMechanicsRunVerifier,
)


@pytest.fixture
def run_context(tmp_path):
    case = get_case("cantilever-uniform-load")
    workspace = tmp_path / case.case_id / "workspace"
    state = workspace.parent / "state"
    workspace.mkdir(parents=True)
    state.mkdir()
    request = CodingRunRequest(
        run_id="bounded-feedback-test",
        goal=case.goal(),
        workspace=workspace,
        state_dir=state,
        runtime=RuntimeProfile(launcher_path=tmp_path / "abq2024.bat"),
        **case.run_budget().as_request_controls(),
    )
    return tmp_path, case, request, WorkspaceResolver().snapshot(workspace)


class Scorer(PublicMechanicsRunVerifier):
    def __init__(self, case, pass_at=None, error=False):
        super().__init__(case)
        self.calls = 0
        self.pass_at = pass_at
        self.error = error

    async def verify(
        self, request, runner, initial_snapshot, executions, decision=None
    ):
        self.calls += 1
        if self.error:
            raise RuntimeError("secret reference 9876543, actual too low")
        passed = self.calls == self.pass_at
        return PublicMechanicsAttempt(
            passed=passed,
            feedback="secret reference 9876543, actual too low",
            findings=()
            if passed
            else ("tip_u2: actual 1, reference 9876543, relative tolerance 0.03",),
            measured={key: metric.target for key, metric in self.case.metrics.items()},
            failure_stage=None if passed else "task_oracle",
        )


class RepairModel:
    def __init__(self, interrupt_at=None):
        self.calls = 0
        self.interrupt_at = interrupt_at
        self.histories = []
        self.check_feedback = True
        self.deliveries = 0

    def __call__(self, messages, info):
        self.calls += 1
        self.histories.append(str(messages))
        if self.check_feedback:
            assert "9876543" not in str(messages)
            assert "actual too low" not in str(messages)
        if self.calls == self.interrupt_at:
            # 下一进程仍先编辑再提交。
            self.calls -= 1
            self.interrupt_at = None
            raise ConnectionError("offline interruption")
        if self.calls % 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {
                            "path": "report.md",
                            "content": f"delivery-{self.deliveries + 1}",
                        },
                    )
                ]
            )
        self.deliveries += 1
        name = next(
            t.name
            for t in info.output_tools
            if t.name.endswith("ReadyForVerificationDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    name,
                    {
                        "summary": "ready",
                        "deliverables": [{"relative_path": "report.md"}],
                    },
                )
            ]
        )


def service(context, scorer, model, *, resume=False, bounded=True, process=None):
    _, _, request, snapshot = context
    runner = Abaqus2024Runner(
        request.workspace, request.state_dir, process_strategy=process
    )
    verifier = MechanicsSubmissionVerifier(request, scorer) if bounded else scorer
    return CodingRunService(
        agent=create_abaqus_coding_agent(FunctionModel(model), request),
        request=request,
        dependencies=CodingAgentDependencies(request=request, runner=runner),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=verifier,
        initial_snapshot=snapshot,
        resume=resume,
        verification_started=verifier.capture if bounded else None,
    )


@pytest.mark.parametrize("pass_at", [1, 2, 3, None])
def test_first_snapshot_and_three_submission_limit(run_context, pass_at):
    _, case, request, _ = run_context
    scorer, model = Scorer(case, pass_at), RepairModel()
    result = asyncio.run(service(run_context, scorer, model).run())
    expected = pass_at or 3
    assert scorer.calls == expected
    assert model.deliveries == expected
    assert result.outcome.usage.model_requests == expected * 2
    assert result.outcome.status.value == ("completed" if pass_at else "failed")
    assert result.outcome.failure_stage == (None if pass_at else "submission_limit")
    records = load_submissions(request.state_dir)
    assert [s.usage.model_requests for s, _ in records] == list(
        range(2, expected * 2 + 1, 2)
    )
    assert records[0][1].passed is (pass_at == 1)
    assert (
        request.state_dir / "submissions/001/workspace/report.md"
    ).read_text() == "delivery-1"
    assert (request.workspace / "report.md").read_text() == f"delivery-{expected}"
    assert "9876543" in (request.state_dir / "submissions/001/result.json").read_text()
    assert "9876543" not in (request.state_dir / "history.json").read_text(
        encoding="utf-8"
    )
    assert sum(
        not r.passed and r.terminal_failure_stage is None for _, r in records
    ) == min(expected - bool(pass_at), 2)


def test_missing_delivery_counts_as_first_submission(run_context):
    _, case, request, _ = run_context
    model = RepairModel()
    model.calls = 1  # 先声明完成；首次缺交付仍保存并评分。
    result = asyncio.run(
        service(run_context, PublicMechanicsRunVerifier(case), model).run()
    )
    first, score = load_submissions(request.state_dir)[0]
    assert first.workspace.files == ()
    assert score.failure_stage == "provenance"
    assert not score.passed
    assert result.outcome.failure_stage == "submission_limit"


def test_resume_keeps_first_score_counts_and_shared_budget(run_context):
    _, case, request, _ = run_context
    scorer, model = Scorer(case), RepairModel(interrupt_at=3)
    with pytest.raises(CodingRunInfrastructureError):
        asyncio.run(service(run_context, scorer, model).run())
    original = (request.state_dir / "submissions/001/submission.json").read_bytes()
    result = asyncio.run(service(run_context, scorer, model, resume=True).run())
    assert scorer.calls == 3
    assert result.outcome.failure_stage == "submission_limit"
    assert result.outcome.usage.model_requests >= 6
    assert (
        request.state_dir / "submissions/001/submission.json"
    ).read_bytes() == original
    records = load_submissions(request.state_dir)
    assert len(records) == 3
    assert records[1][0].usage.model_requests > records[0][0].usage.model_requests
    assert "Failed checks:" in model.histories[-1]
    assert (
        records[-1][0].usage.wall_time_seconds >= records[0][0].usage.wall_time_seconds
    )


def test_scoring_exception_is_evaluation_error_and_does_not_invite_repair(run_context):
    _, case, request, _ = run_context
    model = RepairModel()
    result = asyncio.run(service(run_context, Scorer(case, error=True), model).run())
    assert model.deliveries == 1
    assert result.outcome.failure_stage == "evaluation_error"
    assert "9876543" not in result.outcome.summary
    assert (
        summarize_case(run_context[0], case.case_id)["classification"]
        == "evaluation_error"
    )
    assert "9876543" in (request.state_dir / "submissions/001/error.json").read_text()
    root = run_context[0]
    observation = observe_saved_case(
        root,
        case,
        summarize_case(root, case.case_id),
        {
            "submission_policy": SUBMISSION_POLICY,
            "verification_policy": "results_first_v1",
        },
        bindings_unchanged=True,
    )
    report = asyncio.run(
        evaluate_saved_results(
            (case,), {case.case_id: observation}, (case.case_id,), {}
        )
    )
    assert report.cases[0].labels["task_status"].value == "grading_error"
    assert "task_pass" not in report.cases[0].assertions
    assert "first_submission_pass" not in report.cases[0].assertions
    assert "automatic_pass" not in report.cases[0].assertions


def test_reader_failure_is_not_sent_as_answer_feedback(run_context):
    _, case, _, _ = run_context

    class ReaderFailure(Scorer):
        async def verify(self, *args, **kwargs):
            return PublicMechanicsAttempt(
                passed=False,
                feedback="reader failed: reference 9876543",
                failure_stage="oracle",
            )

    model = RepairModel()
    result = asyncio.run(service(run_context, ReaderFailure(case), model).run())
    assert result.outcome.failure_stage == "evaluation_error"
    assert model.deliveries == 1
    assert "9876543" not in result.outcome.summary


def test_interruption_during_scoring_retains_ungraded_first_submission(run_context):
    root, case, request, _ = run_context

    class InterruptedScorer(Scorer):
        async def verify(self, *args, **kwargs):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service(run_context, InterruptedScorer(case), RepairModel()).run())
    records = load_submissions(request.state_dir)
    assert len(records) == 1
    assert records[0][1] is None
    with pytest.raises(ValueError, match="unsettled"):
        MechanicsSubmissionVerifier(request, Scorer(case))
    observation = observe_saved_case(
        root,
        case,
        summarize_case(root, case.case_id),
        {"submission_policy": SUBMISSION_POLICY},
        bindings_unchanged=True,
    )
    assert observation.first_submission_status == "evaluation_error"
    assert observation.feedback_rounds == 0


def test_unsettled_submission_cannot_be_reconstructed_or_overwritten(run_context):
    _, case, request, _ = run_context
    scorer, model = Scorer(case, pass_at=1), RepairModel()
    asyncio.run(service(run_context, scorer, model).run())
    (request.state_dir / "submissions/001/result.json").unlink()
    with pytest.raises(ValueError, match="unsettled"):
        MechanicsSubmissionVerifier(request, scorer)


def test_native_report_keeps_first_and_final_as_one_case(run_context):
    root, case, _, _ = run_context
    asyncio.run(service(run_context, Scorer(case, pass_at=2), RepairModel()).run())
    manifest = {
        "submission_policy": SUBMISSION_POLICY,
        "verification_policy": "results_first_v1",
    }
    observation = observe_saved_case(
        root,
        case,
        summarize_case(root, case.case_id),
        manifest,
        bindings_unchanged=True,
    )
    assert observation.first_submission_status == "failed"
    assert observation.final_submission_status == "passed"
    assert observation.feedback_rounds == 1
    report = asyncio.run(
        evaluate_saved_results(
            (case,), {case.case_id: observation}, (case.case_id,), {}
        )
    )
    assert len(report.cases) == 1
    assert report.cases[0].assertions["first_submission_pass"].value is False
    assert report.cases[0].assertions["final_submission_pass"].value is True
    assert report.cases[0].labels["task_status"].value == "pending_review"
    totals = {
        entry.title: entry.value for entry in report.analyses if hasattr(entry, "value")
    }
    assert totals["first_submission_pass_rate"] == 0
    assert totals["final_submission_pass_rate"] == 1
    assert totals["planned_tasks"] == 1


def test_never_submitted_is_explicit(run_context):
    root, case, _, _ = run_context
    observation = observe_saved_case(
        root,
        case,
        summarize_case(root, case.case_id),
        {"submission_policy": SUBMISSION_POLICY},
        bindings_unchanged=True,
    )
    assert observation.first_submission_status == "not_submitted"
    assert observation.final_submission_status == "not_submitted"
    assert observation.submission_count == 0


@pytest.mark.parametrize(("limit", "submissions"), [(1, 0), (3, 1), (5, 2)])
def test_budget_is_shared_across_debugging_and_feedback(
    run_context, limit, submissions
):
    root, case, request, snapshot = run_context
    request = request.model_copy(
        update={
            "limits": request.limits.model_copy(
                update={"model_requests": LimitedCount(value=limit)}
            )
        }
    )
    context = root, case, request, snapshot
    result = asyncio.run(service(context, Scorer(case), RepairModel()).run())
    assert result.outcome.failure_stage == "model_budget"
    assert result.outcome.usage.model_requests == limit
    assert len(load_submissions(request.state_dir)) == submissions


def test_product_service_keeps_unbounded_verifier_repairs(run_context):
    _, case, request, _ = run_context
    model = RepairModel()
    model.check_feedback = False
    result = asyncio.run(
        service(run_context, Scorer(case, pass_at=4), model, bounded=False).run()
    )
    assert result.outcome.status.value == "completed"
    assert model.deliveries == 4
    assert not (request.state_dir / "submissions").exists()


def test_runtime_allowance_survives_feedback_and_process_resume(run_context):
    root, case, request, snapshot = run_context
    request = request.model_copy(
        update=CodingRunBudget(
            max_model_requests=200, max_abaqus_calls=2, max_solver_calls=2
        ).as_request_controls()
    )
    context = root, case, request, snapshot

    class Process:
        calls = 0

        async def run(self, command, *, cwd, timeout_seconds):
            self.calls += 1
            return ProcessOutput(exit_code=0, stdout=b"done", stderr=b"")

    calls = 0
    interrupt = True

    def model(messages, info):
        nonlocal calls, interrupt
        if calls == 3 and interrupt:
            interrupt = False
            raise ConnectionError("offline interruption")
        calls += 1
        if calls % 3 == 1:
            part = ToolCallPart(
                "write_file", {"path": "build_model.py", "content": f"print({calls})"}
            )
        elif calls % 3 == 2:
            part = ToolCallPart("run_cae_script", {"script_path": "build_model.py"})
        else:
            name = next(
                t.name
                for t in info.output_tools
                if t.name.endswith("ReadyForVerificationDecision")
            )
            part = ToolCallPart(
                name,
                {
                    "summary": "ready",
                    "deliverables": [{"relative_path": "build_model.py"}],
                },
            )
        return ModelResponse(parts=[part])

    process, scorer = Process(), Scorer(case)
    with pytest.raises(CodingRunInfrastructureError):
        asyncio.run(service(context, scorer, model, process=process).run())
    result = asyncio.run(
        service(context, scorer, model, resume=True, process=process).run()
    )
    assert result.outcome.failure_stage == "runtime_budget"
    assert process.calls == result.outcome.usage.abaqus_calls == 2
    assert [
        submission.usage.abaqus_calls
        for submission, _ in load_submissions(request.state_dir)
    ] == [1, 2]
    assert scorer.calls == 2


def test_corrupted_first_snapshot_cannot_receive_credit(run_context):
    root, case, request, _ = run_context
    asyncio.run(service(run_context, Scorer(case, pass_at=2), RepairModel()).run())
    (request.state_dir / "submissions/001/workspace/report.md").write_text(
        "changed", encoding="utf-8"
    )
    observation = observe_saved_case(
        root,
        case,
        summarize_case(root, case.case_id),
        {"submission_policy": SUBMISSION_POLICY},
        bindings_unchanged=True,
    )
    assert observation.exclusion == "invalid_evidence"


def test_first_score_is_bound_to_the_original_host_event(run_context):
    _, case, request, _ = run_context
    asyncio.run(service(run_context, Scorer(case, pass_at=2), RepairModel()).run())
    path = request.state_dir / "submissions/001/result.json"
    score = json.loads(path.read_bytes())
    score["passed"] = True
    path.write_text(json.dumps(score), encoding="utf-8")
    with pytest.raises(ValueError, match="host verification event"):
        load_submissions(request.state_dir)


def test_goal_does_not_depend_on_reference_values_or_source_metadata():
    for case in mechanics_cases():
        changed = case.model_copy(
            update={
                "metrics": {
                    key: metric.model_copy(
                        update={"target": 9876543.0, "relative_tolerance": 0.987}
                    )
                    for key, metric in case.metrics.items()
                },
                "metadata": case.metadata.model_copy(
                    update={"description": "answer 9876543"}
                ),
            }
        )
        assert changed.goal() == case.goal()
        assert "9876543" not in case.goal()
        assert "公开参考为" not in case.goal()
        assert "官方细网格" not in case.goal()
        for metric in case.metrics.values():
            assert metric.meaning in case.goal()


def test_feedback_uses_check_names_without_numbers_or_direction():
    attempt = PublicMechanicsAttempt(
        passed=False,
        feedback="secret 9876543 too high",
        findings=(
            "tip_u2: actual 100, reference 9876543, relative tolerance 0.01",
            "results.json tip_u2 does not match independent ODB measurement",
            "model: arbitrary oracle detail 9876543 too high",
        ),
    )
    feedback = model_feedback(attempt, ("tip_u2",))
    assert "metric tip_u2" in feedback
    assert "consistency for tip_u2" in feedback
    assert "9876543" not in feedback
    assert "high" not in feedback
    assert "100" not in feedback
