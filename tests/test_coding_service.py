import asyncio
import hashlib
import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic_ai import ModelMessagesTypeAdapter, RequestUsage
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_harness.step_persistence import ToolEffectRecord

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.amendments import (
    CodingRunConfigPatch,
    RuntimeExecutionPolicyPatch,
    append_operational_amendment,
    append_task_revision,
)
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunRequest,
    CodingRunStatus,
    ReadyForVerificationDecision,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.resume import (
    CodingRunReconciliationRequired,
    CodingRunResumeError,
    CodingRunWorkspaceChanged,
    create_step_store,
    load_execution_results,
    load_initial_snapshot,
    load_request_for_resume,
    load_resume_state,
)
from abaqus_sim_agent.coding.service import (
    CodingRunInfrastructureError,
    CodingRunService,
)
from abaqus_sim_agent.coding.verification_port import VerificationAttempt
from abaqus_sim_agent.coding.workspace import (
    WorkspaceResolver,
    WorkspaceSnapshot,
    artifact_evidence_from_snapshots,
)
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
    AbaqusInvocation,
    ProcessOutput,
)
from abaqus_sim_agent.verification.run import (
    PlateTensionRunVerifier,
    PlateTensionVerificationAttempt,
)


class _PassingVerifier:
    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> VerificationAttempt:
        del request, runner, initial_snapshot, executions, decision
        return VerificationAttempt(
            passed=True,
            feedback="Independent physical verification passed.",
        )


class _CountingSuccessfulProcess:
    def __init__(self) -> None:
        self.calls = 0

    async def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> ProcessOutput:
        del command, cwd, timeout_seconds
        self.calls += 1
        return ProcessOutput(
            exit_code=0,
            stdout=b"Abaqus CAE completed\n",
            stderr=b"",
        )


def _request(
    tmp_path: Path,
    *,
    budget: CodingRunBudget | None = None,
) -> tuple[CodingRunRequest, WorkspaceSnapshot]:
    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    state_dir.mkdir()
    initial_snapshot = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
    request = CodingRunRequest(
        run_id="service-test",
        goal="Create the plate; thickness is intentionally absent.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(
            launcher_path=Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
        ),
        **(budget or CodingRunBudget()).as_request_controls(),
    )
    return request, initial_snapshot


def _decision_tool(info: AgentInfo, suffix: str) -> str:
    return next(tool.name for tool in info.output_tools if tool.name.endswith(suffix))


def test_service_pauses_for_clarification_then_verifies(tmp_path: Path) -> None:
    request, initial_snapshot = _request(tmp_path)
    call_count = 0

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        _decision_tool(info, "NeedsInputDecision"),
                        {
                            "status": "needs_input",
                            "clarification": {
                                "field_id": "plate_thickness",
                                "kind": "missing_physical_input",
                                "question": "板厚是多少？",
                                "impact": "板厚会改变平面应力截面刚度和反力。",
                            },
                            "summary": "缺少板厚。",
                        },
                    )
                ],
                usage=RequestUsage(
                    input_tokens=10,
                    output_tokens=4,
                    cost=Decimal("0.001"),
                ),
            )
        assert any("1 mm" in str(message) for message in messages)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "ReadyForVerificationDecision"),
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "plate_tension.odb"}],
                        "summary": "Artifacts are ready.",
                    },
                )
            ],
            usage=RequestUsage(
                input_tokens=20,
                output_tokens=5,
                cost=Decimal("0.002"),
            ),
        )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    service = CodingRunService(
        agent=create_abaqus_coding_agent(FunctionModel(model_function), request),
        request=request,
        dependencies=dependencies,
        clarification_provider=ScriptedClarificationProvider(
            {"plate_thickness": "1 mm"}
        ),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
    )

    result = asyncio.run(service.run())

    assert result.outcome.status is CodingRunStatus.COMPLETED
    assert type(result.verification) is VerificationAttempt
    assert result.outcome.verification_scope == "task_oracle"
    assert result.outcome.usage.model_requests == 2
    assert result.outcome.usage.input_tokens == 30
    assert result.outcome.usage.output_tokens == 9
    assert result.outcome.usage.estimated_cost_usd == 0.003
    assert result.outcome.clarifications[0].field_id == "plate_thickness"
    assert result.outcome.clarifications[0].kind == "missing_physical_input"
    assert "截面刚度" in (result.outcome.clarifications[0].impact or "")
    assert result.outcome.clarifications[0].answer == "1 mm"
    assert result.outcome.clarifications[0].abaqus_calls_before_answer == 0
    assert dependencies.current_turn == 2
    assert dependencies.concept_trigger_source == "clarification"
    assert dependencies.concept_trigger_outcome == "feedback"
    assert dependencies.concept_trigger_reference == "clarification:plate_thickness"
    assert dependencies.concept_trigger_evidence_sha256 is not None
    assert (request.state_dir / "request.json").is_file()
    assert (request.state_dir / "history.json").is_file()
    persisted_history = ModelMessagesTypeAdapter.validate_json(
        (request.state_dir / "history.json").read_bytes()
    )
    assert len(persisted_history) == 6
    assert (
        json.loads((request.state_dir / "outcome.json").read_text("utf-8"))["status"]
        == "completed"
    )
    events = (request.state_dir / "events.jsonl").read_text("utf-8").splitlines()
    assert [json.loads(event)["event"] for event in events] == [
        "agent_decision",
        "clarification",
        "agent_decision",
        "verification",
    ]
    clarification_event = json.loads(events[1])["data"]
    assert clarification_event["kind"] == "missing_physical_input"
    assert "截面刚度" in clarification_event["impact"]
    resume_state = load_resume_state(request.state_dir)
    assert resume_state.status == "completed"
    assert resume_state.turn == 2
    assert resume_state.clarifications[0].answer == "1 mm"


def test_service_records_verifier_feedback_as_concept_trigger(tmp_path: Path) -> None:
    request, initial_snapshot = _request(tmp_path)
    verifier_calls = 0

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "ReadyForVerificationDecision"),
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "placeholder.txt"}],
                        "summary": "Request host verification.",
                    },
                )
            ]
        )

    class _RejectOnceVerifier:
        async def verify(
            self,
            request: CodingRunRequest,
            runner: Abaqus2024Runner,
            initial_snapshot: WorkspaceSnapshot,
            executions: Sequence[AbaqusExecutionResult],
            decision: ReadyForVerificationDecision | None = None,
        ) -> VerificationAttempt:
            nonlocal verifier_calls
            del request, runner, initial_snapshot, executions, decision
            verifier_calls += 1
            return VerificationAttempt(
                passed=verifier_calls == 2,
                feedback=(
                    "Independent verification passed."
                    if verifier_calls == 2
                    else "Beam section assignment is incomplete."
                ),
                failure_stage=None if verifier_calls == 2 else "model",
            )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    result = asyncio.run(
        CodingRunService(
            agent=create_abaqus_coding_agent(FunctionModel(model_function), request),
            request=request,
            dependencies=dependencies,
            clarification_provider=ScriptedClarificationProvider({}),
            verifier=_RejectOnceVerifier(),
            initial_snapshot=initial_snapshot,
        ).run()
    )

    assert result.outcome.status is CodingRunStatus.COMPLETED
    assert verifier_calls == 2
    assert dependencies.current_turn == 2
    assert dependencies.concept_trigger_source == "verification"
    assert dependencies.concept_trigger_outcome == "feedback"
    assert dependencies.concept_trigger_reference == "turn:1"
    assert dependencies.concept_trigger_evidence_sha256 is not None


def test_service_records_execution_evidence_scope_for_open_task(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(tmp_path)
    call_count = 0

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "user_task.py", "content": "value = 1\n"},
                        tool_call_id="write-user-task",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "ReadyForVerificationDecision"),
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "user_task.py"}],
                        "summary": "The requested script is ready.",
                    },
                )
            ]
        )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    service = CodingRunService(
        agent=create_abaqus_coding_agent(FunctionModel(model_function), request),
        request=request,
        dependencies=dependencies,
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=ExecutionEvidenceVerifier(),
        initial_snapshot=initial_snapshot,
    )

    result = asyncio.run(service.run())

    assert result.outcome.status is CodingRunStatus.COMPLETED
    assert result.outcome.verification_scope == "execution_evidence"
    assert result.verification is not None
    assert result.verification.scope == "execution_evidence"
    assert [artifact.relative_path for artifact in result.outcome.artifacts] == [
        "user_task.py"
    ]


def test_service_converts_request_limit_into_failed_outcome(tmp_path: Path) -> None:
    request, initial_snapshot = _request(
        tmp_path,
        budget=CodingRunBudget(max_model_requests=1),
    )

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "NeedsInputDecision"),
                    {
                        "status": "needs_input",
                        "clarification": {
                            "field_id": "plate_thickness",
                            "kind": "missing_physical_input",
                            "question": "板厚是多少？",
                            "impact": "板厚会改变平面应力截面刚度和反力。",
                        },
                        "summary": "缺少板厚。",
                    },
                )
            ]
        )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    service = CodingRunService(
        agent=create_abaqus_coding_agent(FunctionModel(model_function), request),
        request=request,
        dependencies=dependencies,
        clarification_provider=ScriptedClarificationProvider(
            {"plate_thickness": "1 mm"}
        ),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
    )

    result = asyncio.run(service.run())

    assert result.outcome.status is CodingRunStatus.FAILED
    assert result.outcome.failure_stage == "model_budget"
    assert "request_limit" in result.outcome.summary


def test_service_can_finalize_verified_artifacts_after_request_limit(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(
        tmp_path,
        budget=CodingRunBudget(max_model_requests=1),
    )

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "NeedsInputDecision"),
                    {
                        "status": "needs_input",
                        "clarification": {
                            "field_id": "plate_thickness",
                            "kind": "missing_physical_input",
                            "question": "板厚是多少？",
                            "impact": "板厚会改变平面应力截面刚度和反力。",
                        },
                        "summary": "缺少板厚。",
                    },
                )
            ]
        )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    service = CodingRunService(
        agent=create_abaqus_coding_agent(FunctionModel(model_function), request),
        request=request,
        dependencies=dependencies,
        clarification_provider=ScriptedClarificationProvider(
            {"plate_thickness": "1 mm"}
        ),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        verify_on_budget_exhaustion=True,
    )

    result = asyncio.run(service.run())

    assert result.outcome.status is CodingRunStatus.COMPLETED
    assert result.outcome.verification_scope == "task_oracle"
    assert result.verification is not None
    assert "model_budget" in result.outcome.summary
    events = (request.state_dir / "events.jsonl").read_text("utf-8").splitlines()
    assert [json.loads(event)["event"] for event in events] == [
        "agent_decision",
        "clarification",
        "verification",
        "budget_exhaustion_finalization",
    ]


def test_service_keeps_budget_failure_when_terminal_verifier_rejects(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(
        tmp_path,
        budget=CodingRunBudget(max_model_requests=1),
    )

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "NeedsInputDecision"),
                    {
                        "status": "needs_input",
                        "clarification": {
                            "field_id": "plate_thickness",
                            "kind": "missing_physical_input",
                            "question": "板厚是多少？",
                            "impact": "板厚会改变平面应力截面刚度和反力。",
                        },
                        "summary": "缺少板厚。",
                    },
                )
            ]
        )

    class _RejectingTerminalVerifier:
        async def verify(
            self,
            request: CodingRunRequest,
            runner: Abaqus2024Runner,
            initial_snapshot: WorkspaceSnapshot,
            executions: Sequence[AbaqusExecutionResult],
            decision: ReadyForVerificationDecision | None = None,
        ) -> VerificationAttempt:
            del request, runner, initial_snapshot, executions, decision
            return VerificationAttempt(
                passed=False,
                feedback="Terminal artifacts are incomplete.",
                failure_stage="artifacts",
            )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    service = CodingRunService(
        agent=create_abaqus_coding_agent(FunctionModel(model_function), request),
        request=request,
        dependencies=dependencies,
        clarification_provider=ScriptedClarificationProvider(
            {"plate_thickness": "1 mm"}
        ),
        verifier=_RejectingTerminalVerifier(),
        initial_snapshot=initial_snapshot,
        verify_on_budget_exhaustion=True,
    )

    result = asyncio.run(service.run())

    assert result.outcome.status is CodingRunStatus.FAILED
    assert result.outcome.failure_stage == "model_budget"
    assert result.verification is not None
    assert not result.verification.passed
    events = (request.state_dir / "events.jsonl").read_text("utf-8").splitlines()
    finalization = json.loads(events[-1])
    assert finalization["event"] == "budget_exhaustion_finalization"
    assert finalization["data"] == {
        "accepted": False,
        "stage": "model_budget",
    }


def test_service_stops_when_runtime_budget_is_exhausted(tmp_path: Path) -> None:
    request, initial_snapshot = _request(
        tmp_path,
        budget=CodingRunBudget(max_abaqus_calls=0, max_solver_calls=0),
    )
    (request.workspace / "build_model.py").write_text(
        "print('not executed')\n",
        encoding="utf-8",
    )
    call_count = 0

    def model_function(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise AssertionError("runtime budget exhaustion must stop the turn")
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "run_cae_script",
                    {"script_path": "build_model.py"},
                    tool_call_id="runtime-budget",
                )
            ]
        )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    service = CodingRunService(
        agent=create_abaqus_coding_agent(FunctionModel(model_function), request),
        request=request,
        dependencies=dependencies,
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
    )

    result = asyncio.run(service.run())

    assert result.outcome.status is CodingRunStatus.FAILED
    assert result.outcome.failure_stage == "runtime_budget"
    assert "not authorized" in result.outcome.summary
    assert call_count == 1
    persisted_history = ModelMessagesTypeAdapter.validate_json(
        (request.state_dir / "history.json").read_bytes()
    )
    runtime_calls = [
        part
        for message in persisted_history
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_name == "run_cae_script"
    ]
    assert [part.tool_call_id for part in runtime_calls] == ["runtime-budget"]


def test_service_rejects_verification_with_unresolved_probe_state(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(tmp_path)
    (request.workspace / "probe.py").write_text("print('probe')\n", encoding="utf-8")
    call_count = 0

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        _decision_tool(info, "ReadyForVerificationDecision"),
                        {
                            "status": "ready_for_verification",
                            "deliverables": [{"relative_path": "probe.py"}],
                            "summary": "Diagnostic is complete.",
                        },
                    )
                ]
            )
        assert any("no typed conclusion" in str(message) for message in messages)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "FailedDecision"),
                    {
                        "status": "failed",
                        "failure_stage": "probe_protocol",
                        "summary": "Typed conclusion is still missing.",
                    },
                )
            ]
        )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    dependencies.ensure_probe_protocol().register_diagnostic_execution(
        "probe.py",
        AbaqusExecutionResult(
            invocation=AbaqusInvocation.CAE_NOGUI,
            command=("abq2024.bat", "cae", "noGUI=probe.py"),
            workspace=request.workspace,
            exit_code=0,
            timed_out=False,
            duration_seconds=1.0,
            stdout="PROBE DONE\n",
            stderr="",
            stdout_encoding="utf-8",
            stderr_encoding="utf-8",
            stdout_raw_path=request.state_dir / "stdout.bin",
            stderr_raw_path=request.state_dir / "stderr.bin",
        ),
        runtime_stage_succeeded=True,
    )
    service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(model_function),
            request,
            probe_protocol_mode="typed",
        ),
        request=request,
        dependencies=dependencies,
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
    )

    result = asyncio.run(service.run())

    assert result.outcome.status is CodingRunStatus.FAILED
    assert result.outcome.failure_stage == "probe_protocol"
    assert call_count == 2
    events = [
        json.loads(line)
        for line in (request.state_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(event["event"] == "probe_protocol_block" for event in events)


def test_service_persists_completed_tool_exchange_when_request_limit_interrupts_turn(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(
        tmp_path,
        budget=CodingRunBudget(max_model_requests=2),
    )
    (request.workspace / "marker.txt").write_text(
        "persisted during failed turn",
        encoding="utf-8",
    )
    call_count = 0

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        _decision_tool(info, "NeedsInputDecision"),
                        {
                            "status": "needs_input",
                            "clarification": {
                                "field_id": "plate_thickness",
                                "kind": "missing_physical_input",
                                "question": "板厚是多少？",
                                "impact": "板厚会改变平面应力截面刚度和反力。",
                            },
                            "summary": "缺少板厚。",
                        },
                    )
                ]
            )
        if call_count == 2:
            assert any("1 mm" in str(message) for message in messages)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "read_file",
                        {"path": "marker.txt"},
                        tool_call_id="read-marker",
                    )
                ]
            )
        raise AssertionError("The request limit should prevent a third model call")

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    service = CodingRunService(
        agent=create_abaqus_coding_agent(FunctionModel(model_function), request),
        request=request,
        dependencies=dependencies,
        clarification_provider=ScriptedClarificationProvider(
            {"plate_thickness": "1 mm"}
        ),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
    )

    result = asyncio.run(service.run())

    assert result.outcome.status is CodingRunStatus.FAILED
    assert result.outcome.failure_stage == "model_budget"
    assert call_count == 2
    persisted_history = ModelMessagesTypeAdapter.validate_json(
        (request.state_dir / "history.json").read_bytes()
    )
    tool_calls = [
        part
        for message in persisted_history
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_name == "read_file"
    ]
    tool_returns = [
        part
        for message in persisted_history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "read_file"
    ]
    assert [part.tool_call_id for part in tool_calls] == ["read-marker"]
    assert [part.tool_call_id for part in tool_returns] == ["read-marker"]
    assert "persisted during failed turn" in str(tool_returns[0].content)


def test_model_budget_failure_records_terminal_artifact_hash_after_repair(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(
        tmp_path,
        budget=CodingRunBudget(max_model_requests=4),
    )
    call_count = 0

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        _decision_tool(info, "NeedsInputDecision"),
                        {
                            "status": "needs_input",
                            "clarification": {
                                "field_id": "plate_thickness",
                                "kind": "missing_physical_input",
                                "question": "板厚是多少？",
                                "impact": "板厚会改变平面应力截面刚度和反力。",
                            },
                            "summary": "缺少板厚。",
                        },
                    )
                ]
            )
        if call_count == 2:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "build_model.py", "content": "first version"},
                        tool_call_id="write-first",
                    )
                ]
            )
        if call_count == 3:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        _decision_tool(info, "ReadyForVerificationDecision"),
                        {
                            "status": "ready_for_verification",
                            "deliverables": [{"relative_path": "build_model.py"}],
                            "summary": "First version is ready.",
                        },
                    )
                ]
            )
        if call_count == 4:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "build_model.py", "content": "final version"},
                        tool_call_id="write-final",
                    )
                ]
            )
        raise AssertionError("The request limit should prevent a fifth model call")

    class _RejectingVerifier(PlateTensionRunVerifier):
        def __init__(self) -> None:
            pass

        async def verify(
            self,
            request: CodingRunRequest,
            runner: Abaqus2024Runner,
            initial_snapshot: WorkspaceSnapshot,
            executions: Sequence[AbaqusExecutionResult],
            decision: ReadyForVerificationDecision | None = None,
        ) -> PlateTensionVerificationAttempt:
            del runner, executions, decision
            current = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
                request.workspace
            )
            artifacts, _freshness = artifact_evidence_from_snapshots(
                initial_snapshot, current
            )
            return PlateTensionVerificationAttempt(
                passed=False,
                feedback="Repair the generated script.",
                artifacts=artifacts,
                failure_stage="verification",
            )

    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    dependencies = CodingAgentDependencies(request=request, runner=runner)
    service = CodingRunService(
        agent=create_abaqus_coding_agent(FunctionModel(model_function), request),
        request=request,
        dependencies=dependencies,
        clarification_provider=ScriptedClarificationProvider(
            {"plate_thickness": "1 mm"}
        ),
        verifier=_RejectingVerifier(),
        initial_snapshot=initial_snapshot,
    )

    result = asyncio.run(service.run())

    assert result.outcome.status is CodingRunStatus.FAILED
    assert result.outcome.failure_stage == "model_budget"
    assert call_count == 4
    [artifact] = result.outcome.artifacts
    assert artifact.relative_path == "build_model.py"
    assert artifact.fresh
    assert artifact.sha256 == hashlib.sha256(b"final version").hexdigest()
    assert result.verification is not None
    [verified_artifact] = result.verification.artifacts
    assert verified_artifact.sha256 == hashlib.sha256(b"first version").hexdigest()


def test_service_resumes_completed_tool_work_after_provider_failure(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(
        tmp_path,
        budget=CodingRunBudget(max_model_requests=3),
    )
    first_calls = 0

    def interrupted_model(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        nonlocal first_calls
        first_calls += 1
        if first_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {
                            "path": "continued.py",
                            "content": "value = 1\n",
                        },
                        tool_call_id="write-before-provider-failure",
                    )
                ],
                usage=RequestUsage(input_tokens=10, output_tokens=3),
            )
        raise ConnectionError("simulated provider outage")

    step_store = create_step_store(request.state_dir)
    first_dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        step_store=step_store,
    )
    first_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(interrupted_model),
            request,
            step_store=step_store,
        ),
        request=request,
        dependencies=first_dependencies,
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        step_store=step_store,
    )

    with pytest.raises(CodingRunInfrastructureError, match="provider outage"):
        asyncio.run(first_service.run())

    interrupted = load_resume_state(request.state_dir)
    assert interrupted.status == "interrupted"
    assert interrupted.turn == 1
    assert interrupted.usage.requests == 1
    assert load_initial_snapshot(request.state_dir) == initial_snapshot
    assert (request.workspace / "continued.py").read_text(encoding="utf-8") == (
        "value = 1\n"
    )

    resumed_calls = 0

    def resumed_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal resumed_calls
        resumed_calls += 1
        assert any("value = 1" in str(message) for message in messages)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "ReadyForVerificationDecision"),
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "continued.py"}],
                        "summary": "Continued work is ready.",
                    },
                )
            ],
            usage=RequestUsage(input_tokens=12, output_tokens=4),
        )

    resumed_store = create_step_store(request.state_dir)
    resumed_dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        step_store=resumed_store,
    )
    resumed_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(resumed_model),
            request,
            step_store=resumed_store,
        ),
        request=request,
        dependencies=resumed_dependencies,
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        resume=True,
        step_store=resumed_store,
    )

    result = asyncio.run(resumed_service.run())

    assert result.outcome.status is CodingRunStatus.COMPLETED
    assert result.outcome.usage.model_requests == 2
    assert resumed_calls == 1
    completed = load_resume_state(request.state_dir)
    assert completed.status == "completed"
    assert completed.turn == 2
    assert completed.active_wall_time_seconds >= interrupted.active_wall_time_seconds
    events = [
        json.loads(line)
        for line in (request.state_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    [resume_event] = [event for event in events if event["event"] == "run_resumed"]
    assert resume_event["data"]["history_source"] == "step_complete"


def test_service_restores_completed_abaqus_execution_without_rerunning(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(
        tmp_path,
        budget=CodingRunBudget(max_model_requests=3),
    )
    (request.workspace / "build_model.py").write_text(
        "print('build')\n",
        encoding="utf-8",
    )
    process = _CountingSuccessfulProcess()
    first_calls = 0

    def interrupted_model(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        nonlocal first_calls
        first_calls += 1
        if first_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_cae_script",
                        {"script_path": "build_model.py"},
                        tool_call_id="cae-before-provider-failure",
                    )
                ],
                usage=RequestUsage(input_tokens=10, output_tokens=3),
            )
        raise ConnectionError("provider failed after CAE completed")

    first_store = create_step_store(request.state_dir)
    first_dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(
            request.workspace,
            request.state_dir,
            process_strategy=process,
        ),
        step_store=first_store,
    )
    first_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(interrupted_model),
            request,
            step_store=first_store,
        ),
        request=request,
        dependencies=first_dependencies,
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        step_store=first_store,
    )

    with pytest.raises(CodingRunInfrastructureError, match="after CAE completed"):
        asyncio.run(first_service.run())

    assert process.calls == 1
    [execution] = load_execution_results(request.state_dir)
    assert execution.invocation is AbaqusInvocation.CAE_NOGUI
    assert load_resume_state(request.state_dir).dependencies.abaqus_calls == 1
    [run_record] = asyncio.run(first_store.list_runs(conversation_id=request.run_id))
    effect = asyncio.run(
        first_store.get_tool_effect(
            run_id=run_record.run_id,
            tool_call_id="cae-before-provider-failure",
        )
    )
    assert effect is not None
    assert effect.status == "completed"

    class _CapturingVerifier:
        def __init__(self) -> None:
            self.executions: tuple[AbaqusExecutionResult, ...] = ()

        async def verify(
            self,
            request: CodingRunRequest,
            runner: Abaqus2024Runner,
            initial_snapshot: WorkspaceSnapshot,
            executions: Sequence[AbaqusExecutionResult],
            decision: ReadyForVerificationDecision | None = None,
        ) -> VerificationAttempt:
            del request, runner, initial_snapshot, decision
            self.executions = tuple(executions)
            return VerificationAttempt(
                passed=True,
                feedback="Restored execution evidence passed.",
            )

    resumed_calls = 0

    def resumed_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal resumed_calls
        resumed_calls += 1
        assert any(
            isinstance(message, ModelRequest)
            and any(
                isinstance(part, ToolReturnPart)
                and part.tool_name == "run_cae_script"
                and part.tool_call_id == "cae-before-provider-failure"
                for part in message.parts
            )
            for message in messages
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "ReadyForVerificationDecision"),
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "build_model.py"}],
                        "summary": "Restored CAE evidence is ready.",
                    },
                )
            ],
            usage=RequestUsage(input_tokens=12, output_tokens=4),
        )

    verifier = _CapturingVerifier()
    resumed_store = create_step_store(request.state_dir)
    resumed_dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(
            request.workspace,
            request.state_dir,
            process_strategy=process,
        ),
        step_store=resumed_store,
    )
    resumed_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(resumed_model),
            request,
            step_store=resumed_store,
        ),
        request=request,
        dependencies=resumed_dependencies,
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=verifier,
        initial_snapshot=initial_snapshot,
        resume=True,
        step_store=resumed_store,
    )

    result = asyncio.run(resumed_service.run())

    assert result.outcome.status is CodingRunStatus.COMPLETED
    assert result.outcome.usage.model_requests == 2
    assert result.outcome.usage.abaqus_calls == 1
    assert resumed_calls == 1
    assert process.calls == 1
    assert verifier.executions == (execution,)
    assert resumed_dependencies.executions == [execution]
    assert load_execution_results(request.state_dir) == (execution,)


def test_task_revision_uses_new_snapshot_and_excludes_prior_execution_evidence(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(tmp_path)
    (request.workspace / "build_model.py").write_text(
        "print('old task')\n",
        encoding="utf-8",
    )
    process = _CountingSuccessfulProcess()
    first_calls = 0

    def first_model(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal first_calls
        first_calls += 1
        if first_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_cae_script",
                        {"script_path": "build_model.py"},
                        tool_call_id="old-revision-cae",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "ReadyForVerificationDecision"),
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "build_model.py"}],
                        "summary": "Old task completed.",
                    },
                )
            ]
        )

    first_store = create_step_store(request.state_dir)
    first_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(first_model),
            request,
            step_store=first_store,
        ),
        request=request,
        dependencies=CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(
                request.workspace,
                request.state_dir,
                process_strategy=process,
            ),
            step_store=first_store,
        ),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        step_store=first_store,
    )
    assert asyncio.run(first_service.run()).outcome.status is CodingRunStatus.COMPLETED
    assert process.calls == 1
    assert len(load_execution_results(request.state_dir)) == 1

    append_task_revision(
        request.state_dir,
        goal="Create a new artifact for the revised task",
        reason="User changed the task contract",
    )
    revised_request = load_request_for_resume(request.state_dir)
    revised_calls = 0

    def revised_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal revised_calls
        revised_calls += 1
        assert revised_request.goal in str(messages)
        if revised_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "new.py", "content": "value = 'new'\n"},
                        tool_call_id="new-revision-file",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "ReadyForVerificationDecision"),
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "new.py"}],
                        "summary": "New task completed.",
                    },
                )
            ]
        )

    class _RevisionVerifier:
        def __init__(self) -> None:
            self.initial_snapshot: WorkspaceSnapshot | None = None
            self.executions: tuple[AbaqusExecutionResult, ...] | None = None

        async def verify(
            self,
            request: CodingRunRequest,
            runner: Abaqus2024Runner,
            initial_snapshot: WorkspaceSnapshot,
            executions: Sequence[AbaqusExecutionResult],
            decision: ReadyForVerificationDecision | None = None,
        ) -> VerificationAttempt:
            del request, runner, decision
            self.initial_snapshot = initial_snapshot
            self.executions = tuple(executions)
            return VerificationAttempt(passed=True, feedback="New evidence passed.")

    verifier = _RevisionVerifier()
    revised_store = create_step_store(request.state_dir)
    revised_dependencies = CodingAgentDependencies(
        request=revised_request,
        runner=Abaqus2024Runner(
            revised_request.workspace,
            revised_request.state_dir,
            process_strategy=process,
        ),
        step_store=revised_store,
    )
    revised_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(revised_model),
            revised_request,
            step_store=revised_store,
        ),
        request=revised_request,
        dependencies=revised_dependencies,
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=verifier,
        initial_snapshot=initial_snapshot,
        resume=True,
        step_store=revised_store,
        expected_amendment_sequence=1,
    )

    result = asyncio.run(revised_service.run())

    assert result.outcome.status is CodingRunStatus.COMPLETED
    assert result.outcome.task_revision == 1
    assert verifier.executions == ()
    assert verifier.initial_snapshot is not None
    assert "build_model.py" in verifier.initial_snapshot.by_path()
    assert process.calls == 1
    assert revised_dependencies.current_task_revision == 1
    assert revised_dependencies.current_amendment_sequence == 1
    assert revised_dependencies.concept_trigger_source == "task_revision"
    assert revised_dependencies.concept_trigger_outcome == "feedback"
    assert revised_dependencies.concept_trigger_reference == "task_revision:1"


def test_service_requires_reconciliation_for_unsettled_solver_tool(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(tmp_path)

    def interrupted_model(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        raise ConnectionError("simulated provider outage")

    step_store = create_step_store(request.state_dir)
    first_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(interrupted_model),
            request,
            step_store=step_store,
        ),
        request=request,
        dependencies=CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
            step_store=step_store,
        ),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        step_store=step_store,
    )
    with pytest.raises(CodingRunInfrastructureError):
        asyncio.run(first_service.run())

    [run_record] = asyncio.run(step_store.list_runs(conversation_id=request.run_id))
    asyncio.run(
        step_store.record_tool_effect(
            ToolEffectRecord(
                tool_call_id="solver-unknown-after-crash",
                tool_name="run_solver_job",
                run_id=run_record.run_id,
                status="started",
                idempotency_key="service-test::solver::plate",
                effect_summary="solver_job path=plate.inp job=plate",
            )
        )
    )

    def must_not_run(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        raise AssertionError("model must not run before solver reconciliation")

    resumed_store = create_step_store(request.state_dir)
    resumed_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(must_not_run),
            request,
            step_store=resumed_store,
        ),
        request=request,
        dependencies=CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
            step_store=resumed_store,
        ),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        resume=True,
        step_store=resumed_store,
    )

    with pytest.raises(
        CodingRunReconciliationRequired,
        match="reconcile before resuming",
    ):
        asyncio.run(resumed_service.run())

    blocked = load_resume_state(request.state_dir)
    assert blocked.status == "needs_reconcile"
    assert blocked.last_error_type == "CodingRunReconciliationRequired"


def test_service_retries_original_goal_when_first_model_request_never_completed(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(tmp_path)

    def unavailable_model(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        raise ConnectionError("gateway unavailable before first response")

    first_store = create_step_store(request.state_dir)
    first_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(unavailable_model),
            request,
            step_store=first_store,
        ),
        request=request,
        dependencies=CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
            step_store=first_store,
        ),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        step_store=first_store,
    )
    with pytest.raises(CodingRunInfrastructureError):
        asyncio.run(first_service.run())

    recovered_calls = 0

    def recovered_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal recovered_calls
        recovered_calls += 1
        assert request.goal in str(messages)
        assert "infrastructure interruption" not in str(messages)
        if recovered_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "result.py", "content": "value = 1\n"},
                        tool_call_id="write-recovered-result",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _decision_tool(info, "ReadyForVerificationDecision"),
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "result.py"}],
                        "summary": "Recovered from the initial gateway failure.",
                    },
                )
            ]
        )

    resumed_store = create_step_store(request.state_dir)
    resumed_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(recovered_model),
            request,
            step_store=resumed_store,
        ),
        request=request,
        dependencies=CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
            step_store=resumed_store,
        ),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        resume=True,
        step_store=resumed_store,
    )

    result = asyncio.run(resumed_service.run())

    assert result.outcome.status is CodingRunStatus.COMPLETED
    events = [
        json.loads(line)
        for line in (request.state_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    [resume_event] = [event for event in events if event["event"] == "run_resumed"]
    assert resume_event["data"]["history_source"] == "original_goal"


def test_service_refuses_resume_after_workspace_changes(tmp_path: Path) -> None:
    request, initial_snapshot = _request(tmp_path)

    def unavailable_model(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        raise ConnectionError("simulated provider outage")

    first_store = create_step_store(request.state_dir)
    first_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(unavailable_model),
            request,
            step_store=first_store,
        ),
        request=request,
        dependencies=CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
            step_store=first_store,
        ),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        step_store=first_store,
    )
    with pytest.raises(CodingRunInfrastructureError):
        asyncio.run(first_service.run())

    interrupted = load_resume_state(request.state_dir)
    assert interrupted.workspace_snapshot == WorkspaceResolver(
        captured_cwd=request.workspace
    ).snapshot(request.workspace)
    (request.workspace / "external_change.py").write_text(
        "value = 2\n",
        encoding="utf-8",
    )

    def must_not_run(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        raise AssertionError("model must not run after workspace drift")

    resumed_store = create_step_store(request.state_dir)
    resumed_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(must_not_run),
            request,
            step_store=resumed_store,
        ),
        request=request,
        dependencies=CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
            step_store=resumed_store,
        ),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        resume=True,
        step_store=resumed_store,
    )

    with pytest.raises(CodingRunWorkspaceChanged, match=r"external_change\.py"):
        asyncio.run(resumed_service.run())

    assert load_resume_state(request.state_dir).status == "interrupted"
    events = [
        json.loads(line)
        for line in (request.state_dir / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event"] == "resume_workspace_changed"
    assert events[-1]["data"]["changed_paths"] == ["external_change.py"]


def test_service_rejects_stale_composition_after_concurrent_amendment(
    tmp_path: Path,
) -> None:
    request, initial_snapshot = _request(tmp_path)

    def unavailable_model(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        raise ConnectionError("initial interruption")

    first_store = create_step_store(request.state_dir)
    first_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(unavailable_model),
            request,
            step_store=first_store,
        ),
        request=request,
        dependencies=CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
            step_store=first_store,
        ),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        step_store=first_store,
    )
    with pytest.raises(CodingRunInfrastructureError):
        asyncio.run(first_service.run())

    append_operational_amendment(
        request.state_dir,
        patch=CodingRunConfigPatch(
            runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
                {"solver": {"mode": "allow"}}
            )
        ),
        reason="First composition amendment",
    )
    composed_request = load_request_for_resume(request.state_dir)
    model_called = False

    def must_not_run(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        nonlocal model_called
        model_called = True
        raise AssertionError("stale composition must fail before model execution")

    composed_store = create_step_store(request.state_dir)
    stale_service = CodingRunService(
        agent=create_abaqus_coding_agent(
            FunctionModel(must_not_run),
            composed_request,
            step_store=composed_store,
        ),
        request=composed_request,
        dependencies=CodingAgentDependencies(
            request=composed_request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
            step_store=composed_store,
        ),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=_PassingVerifier(),
        initial_snapshot=initial_snapshot,
        resume=True,
        step_store=composed_store,
        expected_amendment_sequence=1,
    )
    append_operational_amendment(
        request.state_dir,
        patch=CodingRunConfigPatch(
            runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
                {"solver_timeout": {"mode": "limited", "seconds": 5400.0}}
            )
        ),
        reason="Concurrent second amendment",
    )

    with pytest.raises(CodingRunResumeError, match="changed after"):
        asyncio.run(stale_service.run())
    assert not model_called
