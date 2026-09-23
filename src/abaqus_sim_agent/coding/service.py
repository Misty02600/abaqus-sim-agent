from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic, perf_counter
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict
from pydantic_ai import (
    Agent,
    AgentRun,
    DeferredToolRequests,
    DeferredToolResults,
    ModelMessagesTypeAdapter,
    ModelRequestNode,
    RunUsage,
    UsageLimitExceeded,
    UsageLimits,
)
from pydantic_ai.exceptions import RunCancelled, ToolFailed
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolReturnPart
from pydantic_ai_harness.step_persistence import StepStore

from abaqus_sim_agent.coding.amendments import amendment_prompt
from abaqus_sim_agent.coding.api_lookup import ApiCatalogMode
from abaqus_sim_agent.coding.clarification import ClarificationProvider
from abaqus_sim_agent.coding.contracts import (
    ClarificationEvent,
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
    CodingRunUsage,
    FailedDecision,
    NeedsInputDecision,
    ReadyForVerificationDecision,
    ResponseDecision,
)
from abaqus_sim_agent.coding.deferred_runtime import (
    DeferredSolverRunner,
    DeferredToolFrontier,
    WaitingForToolExecution,
    load_deferred_tool_frontier,
    write_deferred_tool_frontier,
)
from abaqus_sim_agent.coding.dependencies import (
    CodingAgentDependencies,
    RuntimeBudgetExceeded,
)
from abaqus_sim_agent.coding.diagnostic_retrieval import DiagnosticRetrievalMode
from abaqus_sim_agent.coding.document_policy import DocumentPolicy
from abaqus_sim_agent.coding.document_policy_contracts import (
    PolicyCheckpoint,
    PolicyConfiguration,
)
from abaqus_sim_agent.coding.document_policy_state import DocumentPolicyDenied
from abaqus_sim_agent.coding.persistence import write_bytes_atomic, write_text_atomic
from abaqus_sim_agent.coding.response_stream import (
    MAX_PREVIEW,
    LiveReply,
    ModelResponseControl,
    RunCancellationControl,
    StoppedReply,
    UserStoppedError,
    public_response_text,
)
from abaqus_sim_agent.coding.resume import (
    CodingRunReconciliationRequired,
    CodingRunResumeError,
    CodingRunWorkspaceChanged,
    ResumeStatus,
    append_execution_result,
    create_step_store,
    ensure_new_run_state_available,
    load_api_discovery_traces,
    load_api_lookup_traces,
    load_diagnostic_retrieval_traces,
    load_document_lookup_traces,
    load_effective_run_for_resume,
    load_execution_results,
    load_history_fallback,
    load_resume_state,
    new_resume_state,
    select_resume_history,
    write_resume_state,
)
from abaqus_sim_agent.coding.run_lock import CodingRunFileLock
from abaqus_sim_agent.coding.runtime_tools import finalize_runtime_result
from abaqus_sim_agent.coding.user_questions import (
    CodingAgentOutput,
    QuestionProtocol,
    UserQuestion,
    WaitingForUser,
    load_questions,
    question_history,
    record_question,
)
from abaqus_sim_agent.coding.verification_port import (
    CodingRunVerifier,
    VerificationAttempt,
)
from abaqus_sim_agent.coding.workspace import (
    WorkspaceResolver,
    WorkspaceSnapshot,
    artifact_evidence_from_snapshots,
)
from abaqus_sim_agent.execution import AbaqusExecutionResult, ArtifactChangeKind

from .progress import CodingProgress, ProgressObserver, notify

ProgressSink = Callable[[str], None]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CodingRunResult[VerificationAttemptT: VerificationAttempt](_FrozenModel):
    outcome: CodingRunOutcome
    verification: VerificationAttemptT | None = None


class CodingRunInfrastructureError(RuntimeError):
    pass


@dataclass(slots=True)
class _RunStartState:
    started: float
    usage: RunUsage
    clarifications: list[ClarificationEvent]
    history: list[ModelMessage]
    prompt: str | None
    turn: int
    remaining_wall_time_seconds: float | None


@dataclass(slots=True)
class CodingRunService[VerificationAttemptT: VerificationAttempt]:
    agent: Agent[CodingAgentDependencies, CodingAgentOutput]
    request: CodingRunRequest
    dependencies: CodingAgentDependencies
    clarification_provider: ClarificationProvider
    verifier: CodingRunVerifier[VerificationAttemptT]
    initial_snapshot: WorkspaceSnapshot
    progress_sink: ProgressSink | None = None
    observer: ProgressObserver | None = None
    stream_model: bool = False
    response_control: ModelResponseControl | None = None
    run_control: RunCancellationControl | None = None
    _view_stopped: bytes | None = field(default=None, init=False, repr=False)
    _response_id: str = field(default="", init=False)
    _response_number: int = field(default=0, init=False)
    _public_text: str = field(default="", init=False)
    _view_history: bytes | None = field(default=None, init=False, repr=False)
    _view_outcome: bytes | None = field(default=None, init=False, repr=False)
    _view_verification: bytes | None = field(default=None, init=False, repr=False)
    verify_on_budget_exhaustion: bool = False
    resume: bool = False
    step_store: StepStore | None = None
    launch_manifest_json: str | None = None
    expected_amendment_sequence: int | None = None
    continuation_id: str | None = None
    continuation_prompt: str | None = None
    question_protocol: QuestionProtocol = "legacy_decision"
    response_enabled: bool = False
    _pending_question: UserQuestion | None = field(default=None, init=False, repr=False)
    _pending_tool_frontier: DeferredToolFrontier | None = field(
        default=None, init=False, repr=False
    )
    _deferred_input: DeferredToolResults | None = field(
        default=None, init=False, repr=False
    )
    _current_turn: int = field(default=0, init=False, repr=False)
    _current_amendment_sequence: int = field(default=0, init=False, repr=False)
    _current_task_revision: int = field(default=0, init=False, repr=False)
    _current_continuation_id: str | None = field(default=None, init=False, repr=False)
    _revision_workspace_snapshot: WorkspaceSnapshot | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _revision_execution_start_index: int = field(default=0, init=False, repr=False)
    _run_lock: CodingRunFileLock | None = field(default=None, init=False, repr=False)
    _document_checkpoint: PolicyCheckpoint | None = field(
        default=None, init=False, repr=False
    )

    async def run(self) -> CodingRunResult[VerificationAttemptT]:
        if self.question_protocol != "legacy_decision":
            raise ValueError(
                "ask_user protocol requires run_step, which may return waiting_for_user"
            )
        result = await self.run_step()
        assert isinstance(result, CodingRunResult)
        return result

    async def run_step(
        self,
    ) -> (
        CodingRunResult[VerificationAttemptT] | WaitingForUser | WaitingForToolExecution
    ):
        """执行一个主动工作段；待答时返回并释放锁，由调用方接收答复。

        Note:
            新协议不在本方法中读取Console输入；答复提交后需按当前effective request恢复。
            legacy协议保留原来的同步澄清行为。
        """
        with CodingRunFileLock(self.request.state_dir) as lock:
            self._run_lock = lock
            return await self._run_exclusive()

    async def _run_exclusive(
        self,
    ) -> (
        CodingRunResult[VerificationAttemptT] | WaitingForUser | WaitingForToolExecution
    ):
        start = await self._prepare_start()
        started = start.started
        shared_usage = start.usage
        clarifications = start.clarifications
        history = start.history
        prompt = start.prompt
        verification: VerificationAttemptT | None = None
        turn = start.turn
        if (
            self._pending_tool_frontier is not None
            and self._pending_tool_frontier.state == "waiting"
        ):
            return self._tool_waiting_result(self._pending_tool_frontier)
        if self._pending_question is not None and self._pending_question.pending:
            return self._waiting(shared_usage, clarifications, started)
        if (
            start.remaining_wall_time_seconds is not None
            and start.remaining_wall_time_seconds <= 0
        ):
            return self._failed(
                "timeout",
                "Coding run exhausted its cumulative wall-clock budget",
                shared_usage,
                clarifications,
                started,
            )

        try:
            async with asyncio.timeout(start.remaining_wall_time_seconds) as timeout:
                self.dependencies.active_wall.attach_timeout(timeout)
                while True:
                    turn += 1
                    self._current_turn = turn
                    self.dependencies.current_turn = turn
                    self._save_resume_state(
                        "running",
                        shared_usage,
                        clarifications,
                        started,
                    )
                    self._progress(f"Agent turn {turn} started")
                    async with self.agent.iter(
                        prompt,
                        deps=self.dependencies,
                        message_history=history,
                        deferred_tool_results=self._deferred_input,
                        conversation_id=self.request.run_id,
                        run_id=f"{self.request.run_id}-turn-{turn}",
                        usage=shared_usage,
                        usage_limits=UsageLimits(
                            request_limit=self.request.limits.request_limit,
                        ),
                    ) as agent_run:
                        if self.run_control:
                            self.run_control.bind(agent_run.cancel)
                        self._deferred_input = None
                        self._pending_question = None
                        previous_node_completed = False
                        try:
                            async for _node in agent_run:
                                if previous_node_completed:
                                    history = self._persist_history_json(
                                        agent_run.all_messages_json()
                                    )
                                    self._save_resume_state(
                                        "running",
                                        shared_usage,
                                        clarifications,
                                        started,
                                    )
                                if (
                                    self.stream_model
                                    or self.response_control is not None
                                ) and Agent.is_model_request_node(_node):
                                    await self._request_with_display(_node, agent_run)
                                previous_node_completed = True
                        finally:
                            if self.run_control:
                                self.run_control.unbind()
                            history = self._persist_history_json(
                                agent_run.all_messages_json()
                            )
                            self._save_resume_state(
                                "running",
                                shared_usage,
                                clarifications,
                                started,
                            )
                    result = agent_run.result
                    assert result is not None, "The agent graph did not finish properly"
                    decision = result.output
                    if (
                        self._pending_tool_frontier is not None
                        and self._pending_tool_frontier.state == "resuming"
                    ):
                        self._pending_tool_frontier = write_deferred_tool_frontier(
                            self.request.state_dir,
                            self._pending_tool_frontier.model_copy(
                                update={"state": "resolved"}
                            ),
                        )
                    if isinstance(decision, DeferredToolRequests):
                        if (
                            self.question_protocol != "ask_user_v1"
                            or decision.approvals
                            or len(decision.calls) != 1
                        ):
                            raise ValueError("unsupported deferred tool request")
                        call = decision.calls[0]
                        if call.tool_name == "run_solver_job":
                            return self._defer_solver_tool(
                                decision,
                                result.all_messages_json(),
                                shared_usage,
                                clarifications,
                                started,
                            )
                        if call.tool_name != "ask_user":
                            raise ValueError("unsupported deferred tool request")
                        ledger = load_questions(
                            self.request.state_dir, self.request.run_id
                        )
                        if self._clarification_exhausted(len(ledger.questions)):
                            return self._failed(
                                "clarification_budget",
                                "User question budget exhausted",
                                shared_usage,
                                clarifications,
                                started,
                            )
                        history_bytes = result.all_messages_json()
                        question = UserQuestion(
                            question_id=call.tool_call_id,
                            question=call.args_as_dict()["question"],
                            task_revision=self._current_task_revision,
                            turn=self._current_turn,
                            abaqus_calls_before=self.dependencies.abaqus_calls,
                            revision_had_runtime=bool(self._verification_executions()),
                            goal_at_question=self.request.goal,
                            execution_count=len(self.dependencies.executions),
                            history_sha256=hashlib.sha256(history_bytes).hexdigest(),
                            workspace_sha256=hashlib.sha256(
                                WorkspaceResolver()
                                .snapshot(self.request.workspace)
                                .model_dump_json()
                                .encode()
                            ).hexdigest(),
                            asked_at=datetime.now(UTC),
                        )
                        assert self._run_lock is not None
                        record_question(
                            self.request.state_dir,
                            self.request.run_id,
                            question,
                            history_bytes,
                            self._run_lock,
                        )
                        self._pending_question = question
                        self._append_event(
                            "user_question", question.model_dump(mode="json")
                        )
                        return self._waiting(shared_usage, clarifications, started)
                    self._append_event(
                        "agent_decision",
                        decision.model_dump(mode="json"),
                    )

                    if self.dependencies.pending_solver_executions:
                        pending = ", ".join(
                            sorted(self.dependencies.pending_solver_executions)
                        )
                        feedback = (
                            "Solver executions are still pending: "
                            f"{pending}. Call solver_job with action='wait' before "
                            "ending this run."
                        )
                        history = self._persist_history_json(
                            result.all_messages_json(
                                output_tool_return_content=feedback
                            )
                        )
                        prompt = feedback
                        self._save_resume_state(
                            "running", shared_usage, clarifications, started
                        )
                        continue

                    if isinstance(decision, NeedsInputDecision):
                        if self.dependencies.abaqus_calls:
                            return self._failed(
                                "clarification",
                                "Agent invoked Abaqus before obtaining required input",
                                shared_usage,
                                clarifications,
                                started,
                            )
                        if self._clarification_exhausted(len(clarifications)):
                            return self._failed(
                                "clarification",
                                "Clarification budget is exhausted",
                                shared_usage,
                                clarifications,
                                started,
                            )
                        answer = self.clarification_provider.get_answer(
                            decision.clarification
                        )
                        if answer is None:
                            return self._failed(
                                "clarification",
                                f"No answer supplied for {decision.clarification.field_id}",
                                shared_usage,
                                clarifications,
                                started,
                            )
                        event = ClarificationEvent(
                            field_id=decision.clarification.field_id,
                            question=decision.clarification.question,
                            kind=decision.clarification.kind,
                            impact=decision.clarification.impact,
                            answer=answer.value,
                            source=answer.source,
                            abaqus_calls_before_answer=self.dependencies.abaqus_calls,
                        )
                        clarifications.append(event)
                        self._document_clarification(event)
                        self._append_event(
                            "clarification",
                            event.model_dump(mode="json"),
                        )
                        history = self._persist_history_json(
                            result.all_messages_json(
                                output_tool_return_content=(
                                    f"The user supplied {answer.field_id}: {answer.value}"
                                )
                            )
                        )
                        prompt = (
                            f"The user answered `{answer.field_id}` with `{answer.value}`. "
                            "Continue the same coding run."
                        )
                        self.dependencies.set_concept_lookup_trigger(
                            "clarification",
                            "feedback",
                            reference=f"clarification:{answer.field_id}",
                            evidence=answer.value,
                        )
                        self._save_resume_state(
                            "running",
                            shared_usage,
                            clarifications,
                            started,
                        )
                        continue

                    history = self._persist_history_json(result.all_messages_json())
                    if isinstance(decision, FailedDecision):
                        return self._failed(
                            decision.failure_stage,
                            decision.summary,
                            shared_usage,
                            clarifications,
                            started,
                        )
                    if isinstance(decision, ResponseDecision):
                        current = WorkspaceResolver().snapshot(self.request.workspace)
                        if (
                            not self.response_enabled
                            or self._verification_executions()
                            or current != self._verification_snapshot()
                            or self.dependencies.unresolved_probe_feedback() is not None
                        ):
                            return self._failed(
                                "response_scope",
                                "Text-only completion cannot replace verification of file changes or execution.",
                                shared_usage,
                                clarifications,
                                started,
                            )
                        outcome = CodingRunOutcome(
                            run_id=self.request.run_id,
                            task_revision=self._current_task_revision,
                            amendment_sequence=self._current_amendment_sequence,
                            status=CodingRunStatus.COMPLETED,
                            completion_kind="response",
                            summary=decision.summary,
                            usage=_usage(shared_usage, self.dependencies, started),
                            clarifications=tuple(clarifications),
                        )
                        self._view_verification = None
                        self._write_outcome(outcome)
                        self._save_resume_state(
                            "completed", shared_usage, clarifications, started
                        )
                        self._progress(
                            "Response delivered; no execution verification claimed"
                        )
                        return CodingRunResult(outcome=outcome)
                    if not isinstance(decision, ReadyForVerificationDecision):
                        return self._failed(
                            "agent_output",
                            "Agent returned an unsupported decision",
                            shared_usage,
                            clarifications,
                            started,
                        )

                    probe_feedback = self.dependencies.unresolved_probe_feedback()
                    if probe_feedback is not None:
                        self.dependencies.set_concept_lookup_trigger(
                            "probe",
                            "feedback",
                            reference=f"turn:{turn}",
                            evidence=probe_feedback,
                        )
                        self._append_event(
                            "probe_protocol_block",
                            {"feedback": probe_feedback},
                        )
                        history = self._persist_history_json(
                            result.all_messages_json(
                                output_tool_return_content=probe_feedback
                            )
                        )
                        prompt = (
                            "The typed probe protocol rejected verification: "
                            f"{probe_feedback} Complete the required probe conclusion "
                            "or production adoption before requesting verification again."
                        )
                        self._save_resume_state(
                            "running",
                            shared_usage,
                            clarifications,
                            started,
                        )
                        continue

                    document_feedback = await self._document_verification_denial()
                    if document_feedback is not None:
                        self._append_event(
                            "document_policy_block", {"feedback": document_feedback}
                        )
                        prompt = (
                            "The host document policy rejected readiness: "
                            + document_feedback
                            + ". Record the pending assessment or resolve the registered conditions. "
                            "If critical evidence remains unavailable, return failed with failure_stage=knowledge_evidence; "
                            "request needs_input for a missing physical user input. Do not rerun a failed lookup."
                        )
                        self._save_resume_state(
                            "running", shared_usage, clarifications, started
                        )
                        continue
                    notify(
                        self.observer,
                        CodingProgress(
                            "verification",
                            "verification",
                            turn,
                            self._current_task_revision,
                        ),
                    )
                    verification = await self.verifier.verify(
                        self.request,
                        self.dependencies.runner,
                        self._verification_snapshot(),
                        self._verification_executions(),
                        decision,
                    )
                    self._append_event(
                        "verification",
                        verification.model_dump(mode="json"),
                    )
                    self._view_verification = verification.model_dump_json().encode()
                    if verification.passed:
                        outcome = CodingRunOutcome(
                            run_id=self.request.run_id,
                            task_revision=self._current_task_revision,
                            amendment_sequence=self._current_amendment_sequence,
                            status=CodingRunStatus.COMPLETED,
                            summary=decision.summary,
                            usage=_usage(shared_usage, self.dependencies, started),
                            artifacts=verification.artifacts,
                            clarifications=tuple(clarifications),
                            verification_scope=verification.scope,
                        )
                        self._write_outcome(outcome)
                        self._save_resume_state(
                            "completed",
                            shared_usage,
                            clarifications,
                            started,
                        )
                        self._progress("Coding run completed and verified")
                        return CodingRunResult(
                            outcome=outcome,
                            verification=verification,
                        )

                    if self.dependencies.runtime_permission_denial(solver=False):
                        return self._failed(
                            verification.failure_stage or "verification",
                            verification.feedback,
                            shared_usage,
                            clarifications,
                            started,
                            verification=verification,
                        )
                    history = self._persist_history_json(
                        result.all_messages_json(
                            output_tool_return_content=verification.feedback
                        )
                    )
                    self.dependencies.set_concept_lookup_trigger(
                        "verification",
                        "feedback",
                        reference=f"turn:{turn}",
                        evidence=verification.feedback,
                    )
                    prompt = (
                        "The independent verifier rejected the current artifacts. "
                        f"Evidence: {verification.feedback} Fix the smallest evidenced "
                        "problem, rerun only the necessary stages, and request verification again."
                    )
                    self._save_resume_state(
                        "running",
                        shared_usage,
                        clarifications,
                        started,
                    )
        except (RunCancelled, UserStoppedError) as exc:
            if self.run_control and self.run_control.delete_requested:
                self._save_resume_state(
                    "interrupted", shared_usage, clarifications, started, error=exc
                )
                raise asyncio.CancelledError("宿主正在删除会话") from exc
            user_stopped = bool(
                (self.run_control and self.run_control.user_stop_requested)
                or (self.response_control and self.response_control.requested)
            )
            if not user_stopped:
                self._save_resume_state(
                    "interrupted", shared_usage, clarifications, started, error=exc
                )
                raise CodingRunInfrastructureError(str(exc)) from exc
            cleanup_error = self.run_control.cleanup_error if self.run_control else None
            if cleanup_error:
                error = RuntimeError(f"停止当前任务时执行收尾失败：{cleanup_error}")
                self._save_resume_state(
                    "interrupted", shared_usage, clarifications, started, error=error
                )
                raise CodingRunInfrastructureError(str(error)) from exc
            if self.response_control and self.response_control.requested:
                stopped = StoppedReply(
                    request_id=self._response_id,
                    text=self._public_text,
                    turn=turn,
                    response_number=self._response_number,
                    task_revision=self._current_task_revision,
                )
                self._append_event(
                    "model_response_stopped", stopped.model_dump(mode="json")
                )
                self._view_stopped = stopped.model_dump_json().encode()
            error = UserStoppedError(
                "用户已停止当前任务；已有工作、后台作业和用量保留。"
            )
            self._save_resume_state(
                "interrupted", shared_usage, clarifications, started, error=error
            )
            raise error from exc
        except TimeoutError:
            return self._failed(
                "timeout",
                "Coding run exceeded its wall-clock budget",
                shared_usage,
                clarifications,
                started,
                verification=verification,
            )
        except UsageLimitExceeded as exc:
            return await self._finalize_budget_exhaustion(
                "model_budget",
                str(exc),
                shared_usage,
                clarifications,
                started,
                verification=verification,
            )
        except RuntimeBudgetExceeded as exc:
            return await self._finalize_budget_exhaustion(
                "runtime_budget",
                str(exc),
                shared_usage,
                clarifications,
                started,
                verification=verification,
            )
        except asyncio.CancelledError as exc:
            if self.run_control and self.run_control.user_stop_requested:
                error = UserStoppedError(
                    "用户已停止当前任务；已有工作、后台作业和用量保留。"
                )
                self._save_resume_state(
                    "interrupted",
                    shared_usage,
                    clarifications,
                    started,
                    error=error,
                )
                raise error from exc
            self._append_event(
                "infrastructure_interrupted",
                {"type": type(exc).__name__, "message": str(exc)},
            )
            self._save_resume_state(
                "interrupted",
                shared_usage,
                clarifications,
                started,
                error=exc,
            )
            raise
        except Exception as exc:
            self._append_event(
                "infrastructure_error",
                {"type": type(exc).__name__, "message": str(exc)},
            )
            self._save_resume_state(
                "interrupted",
                shared_usage,
                clarifications,
                started,
                error=exc,
            )
            raise CodingRunInfrastructureError(f"{type(exc).__name__}: {exc}") from exc

    async def _request_with_display(
        self,
        node: ModelRequestNode[CodingAgentDependencies, CodingAgentOutput],
        agent_run: AgentRun[CodingAgentDependencies, CodingAgentOutput],
    ) -> None:
        from uuid import uuid4

        self._response_id = (
            self.response_control.begin(agent_run.cancel)
            if self.response_control
            else uuid4().hex
        )
        self._response_number = 1 + sum(
            isinstance(m, ModelResponse)
            and m.run_id == f"{self.request.run_id}-turn-{self._current_turn}"
            for m in agent_run.all_messages()
        )
        self._public_text = ""
        last_emit = 0.0
        pending_emit: asyncio.TimerHandle | None = None

        def emit(
            kind: Literal["model_started", "model_delta", "model_finished"],
        ) -> None:
            notify(
                self.observer,
                CodingProgress(
                    kind,
                    "model" if kind != "model_finished" else "running",
                    self._current_turn,
                    self._current_task_revision,
                    reply_json=LiveReply(
                        request_id=self._response_id,
                        text=self._public_text[:MAX_PREVIEW]
                        + (
                            "\n…（流式预览已截断，完成后显示全文）"
                            if len(self._public_text) > MAX_PREVIEW
                            else ""
                        ),
                    )
                    .model_dump_json()
                    .encode(),
                ),
            )

        def capture(response: ModelResponse) -> None:
            text = public_response_text(response, partial=True)
            if text and not (
                len(text) < len(self._public_text)
                and self._public_text.startswith(text)
            ):
                self._public_text = text

        emit("model_started")
        try:
            if self.stream_model:
                async with node.stream(agent_run.ctx) as stream:
                    try:

                        def publish() -> None:
                            nonlocal last_emit, pending_emit
                            pending_emit = None
                            last_emit = monotonic()
                            emit("model_delta")

                        async for _event in stream:
                            capture(stream.response)
                            delay = max(0.0, 0.05 - (monotonic() - last_emit))
                            if pending_emit is None:
                                pending_emit = asyncio.get_running_loop().call_later(
                                    delay, publish
                                )
                    finally:
                        if pending_emit is not None:
                            pending_emit.cancel()
                        capture(stream.response)
                        emit("model_delta")
            else:
                await agent_run.next(node)
        finally:
            if self.response_control:
                self.response_control.end()
            emit("model_finished")
        if self.response_control and self.response_control.requested:
            raise UserStoppedError("User stopped before tool dispatch")

    def _clarification_exhausted(self, used: int) -> bool:
        limit = self.request.limits.clarification_limit
        return limit is not None and used >= limit

    async def _prepare_start(self) -> _RunStartState:
        from .launch import LaunchManifest

        launch_path = self.request.state_dir / "launch.json"
        raw_launch = (
            launch_path.read_bytes()
            if self.resume and launch_path.is_file()
            else self.launch_manifest_json
        )
        prepared_domain_knowledge = False
        if raw_launch is not None:
            launch = LaunchManifest.model_validate_json(raw_launch)
            prepared_domain_knowledge = launch.domain_knowledge is not None
            if (
                launch.question_protocol or "legacy_decision"
            ) != self.question_protocol:
                raise CodingRunResumeError("question protocol differs from launch")
        elif self.question_protocol != "legacy_decision":
            raise ValueError("ask_user requires a versioned launch manifest")
        self.request.state_dir.mkdir(parents=True, exist_ok=True)
        self.step_store = self.step_store or create_step_store(self.request.state_dir)
        if self.dependencies.step_store is None:
            self.dependencies.step_store = self.step_store
        elif self.dependencies.step_store is not self.step_store:
            raise CodingRunResumeError(
                "Agent dependencies and CodingRunService must share one step store"
            )
        if self.resume:
            if self.dependencies.edit_scope_gate is not None:
                self.dependencies.edit_scope_gate.prepare(resume=True)
            return await self._prepare_resume_start()
        return self._prepare_new_start(
            prepared_domain_knowledge=prepared_domain_knowledge
        )

    def _prepare_new_start(
        self, *, prepared_domain_knowledge: bool = False
    ) -> _RunStartState:
        ensure_new_run_state_available(
            self.request.state_dir,
            prepared_domain_knowledge=prepared_domain_knowledge,
        )
        if self.dependencies.edit_scope_gate is not None:
            self.dependencies.edit_scope_gate.prepare(resume=False)
        if self.launch_manifest_json is not None:
            self._write_text("launch.json", self.launch_manifest_json)
        self._write_json("request.json", self.request.model_dump(mode="json"))
        self._write_text(
            "initial_snapshot.json",
            self.initial_snapshot.model_dump_json(indent=2),
        )
        started = perf_counter()
        self.dependencies.active_wall.reset()
        usage = RunUsage()
        clarifications: list[ClarificationEvent] = []
        self._current_turn = 0
        self._current_amendment_sequence = 0
        self._current_task_revision = 0
        self.dependencies.current_amendment_sequence = 0
        self.dependencies.current_task_revision = 0
        self._revision_workspace_snapshot = self.initial_snapshot
        self._revision_execution_start_index = 0
        self._prepare_document_policy(restoring=False, clarifications=clarifications)
        self._save_resume_state("running", usage, clarifications, started)
        return _RunStartState(
            started=started,
            usage=usage,
            clarifications=clarifications,
            history=[],
            prompt=self.request.goal,
            turn=0,
            remaining_wall_time_seconds=self.request.limits.active_wall_seconds,
        )

    async def _prepare_resume_start(self) -> _RunStartState:
        effective = load_effective_run_for_resume(self.request.state_dir)
        if (
            effective.amendment_sequence > 0
            and self.expected_amendment_sequence is None
        ):
            raise CodingRunResumeError(
                "resuming an amended run requires its composition sequence"
            )
        if (
            self.expected_amendment_sequence is not None
            and effective.amendment_sequence != self.expected_amendment_sequence
        ):
            raise CodingRunResumeError(
                "amendment ledger changed after the execution was composed"
            )
        if effective.request != self.request:
            raise CodingRunResumeError(
                "resume request differs from the current effective request"
            )
        if effective.initial_snapshot != self.initial_snapshot:
            raise CodingRunResumeError(
                "resume initial workspace snapshot differs from the original"
            )
        persisted = load_resume_state(self.request.state_dir)
        if (persisted.question_protocol or "legacy_decision") != self.question_protocol:
            raise CodingRunResumeError("question protocol differs from resume state")
        self._document_checkpoint = persisted.document_policy_checkpoint
        if (self.request.document_policy is None) != (
            self._document_checkpoint is None
        ):
            raise CodingRunResumeError(
                "document policy activation differs from persisted checkpoint"
            )
        if self.request.document_policy is not None and (
            persisted.schema_version != "coding-run-resume-v4"
            or self._document_checkpoint is None
        ):
            raise CodingRunResumeError(
                "bounded document policy requires its versioned resume checkpoint"
            )
        if persisted.run_id != self.request.run_id:
            raise CodingRunResumeError(
                "resume state run_id does not match request.json"
            )
        if persisted.status == "needs_reconcile":
            raise CodingRunResumeError(
                "CodingRun still requires Abaqus runtime reconciliation"
            )
        if effective.amendment_sequence < persisted.amendment_sequence:
            raise CodingRunResumeError("amendment ledger is older than resume state")
        has_pending_amendment = (
            effective.amendment_sequence > persisted.amendment_sequence
        )
        new_continuation = (
            self.continuation_id is not None
            and self.continuation_id != persisted.continuation_id
        )
        if (
            persisted.status in {"completed", "failed"}
            and not has_pending_amendment
            and not new_continuation
        ):
            raise CodingRunResumeError(
                f"CodingRun is terminal and has no new amendment or continuation: {persisted.status}"
            )
        if effective.task_revision < persisted.task_revision:
            raise CodingRunResumeError("task revision moved backwards")
        task_revision_changed = effective.task_revision > persisted.task_revision
        if task_revision_changed and self.continuation_id is not None:
            raise CodingRunResumeError(
                "a host continuation cannot be combined with a new task revision"
            )
        self._current_amendment_sequence = effective.amendment_sequence
        self._current_task_revision = effective.task_revision
        self.dependencies.current_amendment_sequence = effective.amendment_sequence
        self.dependencies.current_task_revision = effective.task_revision
        usage = persisted.usage.restore()
        clarifications = (
            []
            if task_revision_changed or new_continuation
            else list(persisted.clarifications)
        )
        persisted.dependencies.restore_into(
            self.dependencies,
            journaled_executions=load_execution_results(self.request.state_dir),
            journaled_api_lookups=load_api_lookup_traces(self.request.state_dir),
            journaled_api_discoveries=load_api_discovery_traces(self.request.state_dir),
            journaled_diagnostic_retrievals=load_diagnostic_retrieval_traces(
                self.request.state_dir
            ),
            journaled_document_lookups=load_document_lookup_traces(
                self.request.state_dir
            ),
        )
        if task_revision_changed:
            self._current_continuation_id = None
            self._revision_workspace_snapshot = effective.revision_workspace_snapshot
            self._revision_execution_start_index = (
                effective.revision_execution_start_index
            )
            self.dependencies.set_concept_lookup_trigger(
                "task_revision",
                "feedback",
                reference=f"task_revision:{effective.task_revision}",
                evidence=effective.request.goal,
            )
        elif new_continuation:
            self._current_continuation_id = self.continuation_id
            self._revision_workspace_snapshot = persisted.workspace_snapshot
            self._revision_execution_start_index = len(self.dependencies.executions)
        else:
            self._current_continuation_id = persisted.continuation_id
            self._revision_workspace_snapshot = persisted.revision_workspace_snapshot
            self._revision_execution_start_index = (
                persisted.revision_execution_start_index
            )
        if self._revision_execution_start_index > len(self.dependencies.executions):
            raise CodingRunResumeError(
                "task revision execution boundary exceeds persisted executions"
            )
        self._current_turn = persisted.turn
        started = perf_counter()
        self.dependencies.active_wall.reset(persisted.active_wall_time_seconds)
        deferred_frontier = load_deferred_tool_frontier(self.request.state_dir)
        if (
            deferred_frontier is not None
            and deferred_frontier.run_id != self.request.run_id
        ):
            raise CodingRunResumeError("deferred tool frontier belongs to another run")
        if persisted.status == "waiting_for_tool_execution":
            if deferred_frontier is None or deferred_frontier.state == "resolved":
                raise CodingRunResumeError(
                    "waiting Solver state is missing its deferred tool frontier"
                )
            if task_revision_changed or new_continuation:
                raise CodingRunResumeError(
                    "cannot change task revision or continuation while Solver is pending"
                )
            history_at_deferral = _frontier_history(deferred_frontier)
            runner = self.dependencies.runner
            if not isinstance(runner, DeferredSolverRunner):
                raise CodingRunResumeError(
                    "saved deferred Solver requires a host deferred runner"
                )
            resolution = runner.resolve_deferred_solver_job(
                deferred_frontier.execution_id
            )
            if resolution.status in {"starting", "running"}:
                self._pending_tool_frontier = deferred_frontier
                self._prepare_document_policy(
                    restoring=True, clarifications=clarifications
                )
                return _RunStartState(
                    started=started,
                    usage=usage,
                    clarifications=clarifications,
                    history=history_at_deferral,
                    prompt=None,
                    turn=persisted.turn,
                    remaining_wall_time_seconds=self.dependencies.active_wall.remaining_seconds(
                        self.request.limits.active_wall_seconds
                    ),
                )
            if resolution.status == "needs_reconcile":
                error = CodingRunResumeError(
                    resolution.error or "deferred Solver requires reconciliation"
                )
                self._save_resume_state(
                    "needs_reconcile",
                    usage,
                    clarifications,
                    started,
                    error=error,
                )
                raise error
            current_snapshot = WorkspaceResolver(
                captured_cwd=self.request.workspace
            ).snapshot(self.request.workspace)
            if resolution.status == "cancelled":
                if current_snapshot != deferred_frontier.workspace_snapshot:
                    error = CodingRunWorkspaceChanged(
                        "cancelled Solver changed workspace without terminal artifact evidence"
                    )
                    self._save_resume_state(
                        "needs_reconcile",
                        usage,
                        clarifications,
                        started,
                        error=error,
                    )
                    raise error
                if deferred_frontier.state == "waiting":
                    deferred_frontier = write_deferred_tool_frontier(
                        self.request.state_dir,
                        deferred_frontier.model_copy(
                            update={
                                "state": "resuming",
                                "tool_failure": resolution.error
                                or "Solver execution was cancelled.",
                            }
                        ),
                    )
                if not deferred_frontier.tool_failure:
                    error = CodingRunResumeError(
                        "deferred Solver resume is incomplete after cancellation"
                    )
                    self._save_resume_state(
                        "needs_reconcile",
                        usage,
                        clarifications,
                        started,
                        error=error,
                    )
                    raise error
                self._deferred_input = DeferredToolResults(
                    calls={
                        deferred_frontier.tool_call_id: ToolFailed(
                            deferred_frontier.tool_failure
                        )
                    }
                )
            else:
                result = resolution.result
                if result is None:
                    error = CodingRunResumeError(
                        "terminal deferred Solver is missing its execution result"
                    )
                    self._save_resume_state(
                        "needs_reconcile",
                        usage,
                        clarifications,
                        started,
                        error=error,
                    )
                    raise error
                _validate_deferred_workspace(
                    deferred_frontier.workspace_snapshot,
                    current_snapshot,
                    result,
                )
                if deferred_frontier.state == "waiting":
                    deferred_frontier = write_deferred_tool_frontier(
                        self.request.state_dir,
                        deferred_frontier.model_copy(update={"state": "resuming"}),
                    )
                    payload = await self._finalize_deferred_solver_result(
                        deferred_frontier,
                        result,
                    )
                    deferred_frontier = write_deferred_tool_frontier(
                        self.request.state_dir,
                        deferred_frontier.model_copy(
                            update={"tool_result_payload": payload}
                        ),
                    )
                elif deferred_frontier.tool_result_payload is None:
                    error = CodingRunResumeError(
                        "deferred Solver resume stopped during result finalization"
                    )
                    self._save_resume_state(
                        "needs_reconcile",
                        usage,
                        clarifications,
                        started,
                        error=error,
                    )
                    raise error
                self._deferred_input = DeferredToolResults(
                    calls={
                        deferred_frontier.tool_call_id: deferred_frontier.tool_result_payload
                    }
                )
            self._pending_tool_frontier = deferred_frontier
        assert self.step_store is not None
        try:
            continuation = await select_resume_history(
                self.step_store,
                self.request,
                deferred_tool_effect=(
                    (deferred_frontier.tool_name, deferred_frontier.tool_call_id)
                    if deferred_frontier is not None
                    else None
                ),
            )
        except CodingRunReconciliationRequired as exc:
            self._append_event(
                "resume_needs_reconcile",
                {
                    "message": str(exc),
                    "tools": [
                        {
                            "tool_name": effect.tool_name,
                            "tool_call_id": effect.tool_call_id,
                            "idempotency_key": effect.idempotency_key,
                            "effect_summary": effect.effect_summary,
                        }
                        for effect in exc.effects
                    ],
                },
            )
            self._save_resume_state(
                "needs_reconcile",
                usage,
                clarifications,
                started,
                error=exc,
            )
            raise

        current_snapshot = WorkspaceResolver(
            captured_cwd=self.request.workspace
        ).snapshot(self.request.workspace)
        if self._deferred_input is None:
            expected_snapshot = (
                effective.revision_workspace_snapshot
                if task_revision_changed
                else persisted.workspace_snapshot
            )
            if current_snapshot != expected_snapshot:
                changed_paths = _changed_snapshot_paths(
                    expected_snapshot,
                    current_snapshot,
                )
                self._append_event(
                    "resume_workspace_changed",
                    {"changed_paths": list(changed_paths)},
                )
                detail = ", ".join(changed_paths[:10]) or "unknown workspace change"
                raise CodingRunWorkspaceChanged(
                    "workspace changed after the last persisted CodingRun boundary: "
                    + detail
                )

        resume_messages = (
            list(_frontier_history(deferred_frontier))
            if self._deferred_input is not None and deferred_frontier is not None
            else list(continuation.messages)
        )
        if self.question_protocol == "ask_user_v1" and self._deferred_input is None:
            ledger = load_questions(self.request.state_dir, self.request.run_id)
            if persisted.pending_question_id and not any(
                q.question_id == persisted.pending_question_id for q in ledger.questions
            ):
                raise CodingRunResumeError("pending question record is missing")
            if ledger.questions:
                q = ledger.questions[-1]
                q_history = question_history(self.request.state_dir, q)
                if q.answer is not None and q.answer_revision is None:
                    raise CodingRunResumeError(
                        "answer revision transaction needs recovery before composing resume"
                    )
                host_history = list(load_history_fallback(self.request.state_dir))

                def answered_in(messages):
                    returns = [
                        p
                        for m in messages
                        for p in m.parts
                        if isinstance(p, ToolReturnPart)
                        and p.tool_name == "ask_user"
                        and p.tool_call_id == q.question_id
                    ]
                    if len(returns) > 1 or any(
                        q.answer is not None and p.content != q.answer for p in returns
                    ):
                        raise CodingRunResumeError(
                            "saved tool answer conflicts with the question receipt"
                        )
                    return bool(returns)

                if q.pending:
                    if q.task_revision != self._current_task_revision:
                        raise CodingRunResumeError("pending question revision mismatch")
                    self._pending_question = q
                    resume_messages = q_history
                elif not answered_in(resume_messages):
                    if answered_in(host_history):
                        resume_messages = host_history
                    else:
                        if not task_revision_changed and (
                            hashlib.sha256(
                                current_snapshot.model_dump_json().encode()
                            ).hexdigest()
                            != q.workspace_sha256
                        ):
                            raise CodingRunResumeError(
                                "missing answer frontier after workspace changes"
                            )
                        self._pending_question = q
                        resume_messages = q_history
                        self._deferred_input = DeferredToolResults(
                            calls={
                                q.question_id: ToolFailed(
                                    "The user cancelled this question without supplying an answer."
                                )
                                if q.cancelled
                                else q.answer
                            }
                        )
        self._prepare_document_policy(restoring=True, clarifications=clarifications)
        self._append_event(
            "run_resumed",
            {
                "from_turn": persisted.turn,
                "from_amendment_sequence": persisted.amendment_sequence,
                "to_amendment_sequence": effective.amendment_sequence,
                "from_task_revision": persisted.task_revision,
                "to_task_revision": effective.task_revision,
                "from_continuation_id": persisted.continuation_id,
                "to_continuation_id": self._current_continuation_id,
                "history_source": continuation.source,
                "prior_step_run_id": continuation.prior_step_run_id,
                "unresolved_non_runtime_tools": list(
                    continuation.unresolved_non_runtime_tools
                ),
            },
        )
        self._save_resume_state("running", usage, clarifications, started)
        return _RunStartState(
            started=started,
            usage=usage,
            clarifications=clarifications,
            history=resume_messages,
            prompt=None
            if self._deferred_input is not None and not task_revision_changed
            else _compose_resume_prompt(
                continuation.prompt,
                amendment_prompt(
                    effective.ledger,
                    after_sequence=persisted.amendment_sequence,
                ),
                user_prompt=self.continuation_prompt,
                new_continuation=new_continuation,
            ),
            turn=persisted.turn,
            remaining_wall_time_seconds=self.dependencies.active_wall.remaining_seconds(
                self.request.limits.active_wall_seconds
            ),
        )

    async def _finalize_budget_exhaustion(
        self,
        stage: str,
        summary: str,
        usage: RunUsage,
        clarifications: list[ClarificationEvent],
        started: float,
        *,
        verification: VerificationAttemptT | None = None,
    ) -> CodingRunResult[VerificationAttemptT]:
        if self.request.document_policy is not None:
            denied = await self._document_verification_denial()
            if denied is not None:
                return self._failed(
                    "knowledge_evidence",
                    denied,
                    usage,
                    clarifications,
                    started,
                    verification=verification,
                )
        if not self.verify_on_budget_exhaustion:
            return self._failed(
                stage,
                summary,
                usage,
                clarifications,
                started,
                verification=verification,
            )
        try:
            terminal_verification = await self.verifier.verify(
                self.request,
                self.dependencies.runner,
                self._verification_snapshot(),
                self._verification_executions(),
                None,
            )
        except Exception as exc:
            self._append_event(
                "budget_exhaustion_verification_error",
                {
                    "stage": stage,
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )
            return self._failed(
                stage,
                summary,
                usage,
                clarifications,
                started,
                verification=verification,
            )
        self._append_event(
            "verification",
            terminal_verification.model_dump(mode="json"),
        )
        self._append_event(
            "budget_exhaustion_finalization",
            {"stage": stage, "accepted": terminal_verification.passed},
        )
        if not terminal_verification.passed:
            return self._failed(
                stage,
                summary,
                usage,
                clarifications,
                started,
                verification=terminal_verification,
            )
        outcome = CodingRunOutcome(
            run_id=self.request.run_id,
            task_revision=self._current_task_revision,
            amendment_sequence=self._current_amendment_sequence,
            status=CodingRunStatus.COMPLETED,
            summary=(
                f"Independent verifier accepted terminal artifacts after {stage}: "
                f"{summary}"
            ),
            usage=_usage(usage, self.dependencies, started),
            artifacts=terminal_verification.artifacts,
            clarifications=tuple(clarifications),
            verification_scope=terminal_verification.scope,
        )
        self._write_outcome(outcome)
        self._save_resume_state(
            "completed",
            usage,
            clarifications,
            started,
        )
        self._progress(f"Coding run completed by terminal verification after {stage}")
        return CodingRunResult(
            outcome=outcome,
            verification=terminal_verification,
        )

    def _failed(
        self,
        stage: str,
        summary: str,
        usage: RunUsage,
        clarifications: list[ClarificationEvent],
        started: float,
        *,
        verification: VerificationAttemptT | None = None,
    ) -> CodingRunResult[VerificationAttemptT]:
        current_snapshot = WorkspaceResolver(
            captured_cwd=self.request.workspace
        ).snapshot(self.request.workspace)
        artifacts, _freshness = artifact_evidence_from_snapshots(
            self._verification_snapshot(),
            current_snapshot,
        )
        fresh_artifacts = tuple(artifact for artifact in artifacts if artifact.fresh)
        outcome = CodingRunOutcome(
            run_id=self.request.run_id,
            task_revision=self._current_task_revision,
            amendment_sequence=self._current_amendment_sequence,
            status=CodingRunStatus.FAILED,
            summary=summary,
            usage=_usage(usage, self.dependencies, started),
            artifacts=fresh_artifacts,
            clarifications=tuple(clarifications),
            verification_scope=(verification.scope if verification else None),
            failure_stage=stage,
        )
        self._write_outcome(outcome)
        self._save_resume_state(
            "failed",
            usage,
            clarifications,
            started,
        )
        self._progress(f"Coding run failed at {stage}: {summary}")
        return CodingRunResult(outcome=outcome, verification=verification)

    def _persist_history_json(self, history_json: bytes) -> list[ModelMessage]:
        write_bytes_atomic(self.request.state_dir / "history.json", history_json)
        self._view_history = history_json
        return ModelMessagesTypeAdapter.validate_json(history_json)

    def _append_event(self, event: str, data: object) -> None:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "amendment_sequence": self._current_amendment_sequence,
            "task_revision": self._current_task_revision,
            "data": data,
        }
        with (self.request.state_dir / "events.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            stream.write("\n")

    def _write_json(self, name: str, value: object) -> None:
        self._write_text(
            name,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        )

    def _write_text(self, name: str, value: str) -> None:
        write_text_atomic(self.request.state_dir / name, value)

    def _write_outcome(self, outcome: CodingRunOutcome) -> None:
        value = outcome.model_dump_json(indent=2)
        archive = (
            self.request.state_dir
            / "outcomes"
            / f"task-{self._current_task_revision:04d}"
            / (
                f"sequence-{self._current_amendment_sequence:04d}-"
                f"turn-{self._current_turn:04d}.json"
            )
        )
        write_text_atomic(archive, value)
        self._write_text("outcome.json", value)
        self._view_outcome = outcome.model_dump_json().encode()

    def _verification_snapshot(self) -> WorkspaceSnapshot:
        return self._revision_workspace_snapshot or self.initial_snapshot

    def _waiting(
        self, usage: RunUsage, clarifications: list[ClarificationEvent], started: float
    ) -> WaitingForUser:
        assert self._pending_question is not None
        self._save_resume_state("waiting_for_user", usage, clarifications, started)
        return WaitingForUser(
            run_id=self.request.run_id,
            state_dir=self.request.state_dir,
            task_revision=self._current_task_revision,
            question=self._pending_question,
            usage=_usage(usage, self.dependencies, started),
            question_wait_seconds=load_questions(
                self.request.state_dir, self.request.run_id
            ).wait_seconds,
        )

    def _defer_solver_tool(
        self,
        decision: DeferredToolRequests,
        history_json: bytes,
        usage: RunUsage,
        clarifications: list[ClarificationEvent],
        started: float,
    ) -> WaitingForToolExecution:
        if decision.approvals or len(decision.calls) != 1:
            raise CodingRunResumeError(
                "deferred Solver requires exactly one tool call and no approvals"
            )
        call = decision.calls[0]
        if call.tool_name != "run_solver_job":
            raise CodingRunResumeError(f"unsupported deferred tool: {call.tool_name}")
        metadata = decision.metadata.get(call.tool_call_id)
        if not isinstance(metadata, dict) or metadata.get("kind") != "solver_job":
            raise CodingRunResumeError("deferred Solver metadata is missing or invalid")

        execution_id = metadata.get("execution_id")
        execution_count_before = metadata.get("execution_count_before")
        journal_count_before = metadata.get("execution_journal_count_before")
        snapshot_value = metadata.get("workspace_snapshot")
        if not isinstance(execution_id, str):
            raise CodingRunResumeError("deferred Solver execution id is missing")
        if not isinstance(execution_count_before, int) or isinstance(
            execution_count_before, bool
        ):
            raise CodingRunResumeError("deferred Solver execution baseline is invalid")
        if not isinstance(journal_count_before, int) or isinstance(
            journal_count_before, bool
        ):
            raise CodingRunResumeError("deferred Solver journal baseline is invalid")
        if isinstance(snapshot_value, dict) and isinstance(
            snapshot_value.get("files"), list
        ):
            snapshot_value = {
                **snapshot_value,
                "files": tuple(snapshot_value["files"]),
            }
        try:
            workspace_snapshot = WorkspaceSnapshot.model_validate(snapshot_value)
        except ValueError as exc:
            raise CodingRunResumeError(
                "deferred Solver workspace baseline is invalid"
            ) from exc

        if execution_count_before != len(self.dependencies.executions):
            raise CodingRunResumeError(
                "dependency executions changed while deferring Solver"
            )
        if journal_count_before != len(load_execution_results(self.request.state_dir)):
            raise CodingRunResumeError(
                "execution journal changed while deferring Solver"
            )

        try:
            history_text = history_json.decode("utf-8")
            ModelMessagesTypeAdapter.validate_json(history_json)
        except (UnicodeError, ValueError) as exc:
            raise CodingRunResumeError("deferred Solver history is invalid") from exc

        now = datetime.now(UTC)
        frontier = DeferredToolFrontier(
            run_id=self.request.run_id,
            tool_call_id=call.tool_call_id,
            execution_id=execution_id,
            metadata=dict(metadata),
            turn=self._current_turn,
            amendment_sequence=self._current_amendment_sequence,
            task_revision=self._current_task_revision,
            continuation_id=self._current_continuation_id,
            history_json=history_text,
            history_sha256=hashlib.sha256(history_json).hexdigest(),
            workspace_snapshot=workspace_snapshot,
            execution_count_before=execution_count_before,
            execution_journal_count_before=journal_count_before,
            created_at=now,
            updated_at=now,
        )
        frontier = write_deferred_tool_frontier(self.request.state_dir, frontier)
        self._pending_tool_frontier = frontier
        self._save_resume_state(
            "waiting_for_tool_execution",
            usage,
            clarifications,
            started,
        )
        return self._tool_waiting_result(frontier)

    def _tool_waiting_result(
        self, frontier: DeferredToolFrontier
    ) -> WaitingForToolExecution:
        return WaitingForToolExecution(
            run_id=frontier.run_id,
            tool_call_id=frontier.tool_call_id,
            execution_id=frontier.execution_id,
        )

    async def _finalize_deferred_solver_result(
        self,
        frontier: DeferredToolFrontier,
        result: AbaqusExecutionResult,
    ) -> dict[str, object]:
        journal = load_execution_results(self.request.state_dir)
        if len(journal) < frontier.execution_journal_count_before:
            raise CodingRunResumeError("execution journal moved behind Solver baseline")
        journal_tail = journal[frontier.execution_journal_count_before :]
        if len(journal_tail) > 1:
            raise CodingRunResumeError(
                "multiple execution records appeared while Solver was deferred"
            )
        if journal_tail and journal_tail[0] != result:
            raise CodingRunResumeError(
                "execution journal result does not match deferred Solver result"
            )

        execution_count = len(self.dependencies.executions)
        if journal_tail:
            if execution_count != frontier.execution_count_before + 1:
                raise CodingRunResumeError(
                    "dependency execution count does not match recovered Solver result"
                )
            if self.dependencies.executions[frontier.execution_count_before] != result:
                raise CodingRunResumeError(
                    "dependency execution does not match recovered Solver result"
                )
        else:
            if execution_count != frontier.execution_count_before:
                raise CodingRunResumeError(
                    "dependency executions changed before Solver finalization"
                )
            append_execution_result(self.request.state_dir, result)
            self.dependencies.record_runtime_execution(result)

        api_mode = frontier.metadata.get("api_catalog_mode")
        if api_mode not in {
            "off",
            "full",
            "automatic_verified_only",
            "automatic_exact_signature",
        }:
            raise CodingRunResumeError("deferred Solver API catalog mode is invalid")
        diagnostic_mode = frontier.metadata.get("diagnostic_retrieval_mode")
        if diagnostic_mode not in {"off", "advisory"}:
            raise CodingRunResumeError(
                "deferred Solver diagnostic retrieval mode is invalid"
            )
        payload = await finalize_runtime_result(
            self.dependencies,
            result,
            api_catalog_mode=cast(ApiCatalogMode, api_mode),
            diagnostic_retrieval_mode=cast(DiagnosticRetrievalMode, diagnostic_mode),
            record_execution=False,
        )
        if "recorded_probe_result" in frontier.metadata:
            payload["recorded_probe_result"] = frontier.metadata[
                "recorded_probe_result"
            ]
        return payload

    def _verification_executions(self):
        return self.dependencies.executions[self._revision_execution_start_index :]

    def _save_resume_state(
        self,
        status: ResumeStatus,
        usage: RunUsage,
        clarifications: list[ClarificationEvent],
        started: float,
        *,
        error: BaseException | None = None,
    ) -> None:
        state = new_resume_state(
            self.request,
            status=status,
            turn=self._current_turn,
            amendment_sequence=self._current_amendment_sequence,
            task_revision=self._current_task_revision,
            continuation_id=self._current_continuation_id,
            revision_workspace_snapshot=self._verification_snapshot(),
            revision_execution_start_index=self._revision_execution_start_index,
            active_wall_time_seconds=self.dependencies.active_wall.elapsed_seconds,
            workspace_snapshot=WorkspaceResolver(
                captured_cwd=self.request.workspace
            ).snapshot(self.request.workspace),
            usage=usage,
            clarifications=clarifications,
            dependencies=self.dependencies,
            error=error,
            question_protocol="ask_user_v1"
            if self.question_protocol == "ask_user_v1"
            else None,
            pending_question_id=self._pending_question.question_id
            if self._pending_question
            else None,
        )
        if self.request.document_policy is not None:
            if self.dependencies.document_policy is not None:
                self._document_checkpoint = (
                    self.dependencies.document_policy.checkpoint()
                )
            state = state.model_copy(
                update={
                    "schema_version": "coding-run-resume-v4",
                    "document_policy_checkpoint": self._document_checkpoint,
                }
            )
        if self.question_protocol == "ask_user_v1":
            state = state.model_copy(
                update={
                    "schema_version": "coding-run-resume-v5",
                    "question_protocol": "ask_user_v1",
                    "pending_question_id": self._pending_question.question_id
                    if self._pending_question
                    else None,
                }
            )
        write_resume_state(self.request.state_dir, state)
        notify(
            self.observer,
            CodingProgress(
                "checkpoint",
                status,
                self._current_turn,
                self._current_task_revision,
                checkpoint_json=state.model_dump_json().encode(),
                history_json=self._view_history,
                question_json=self._pending_question.model_dump_json().encode()
                if self._pending_question
                else None,
                outcome_json=self._view_outcome,
                verification_json=self._view_verification,
                stopped_reply_json=self._view_stopped,
            ),
        )

    def _prepare_document_policy(
        self, *, restoring: bool, clarifications: list[ClarificationEvent]
    ) -> None:
        binding = self.request.document_policy
        if binding is None:
            if self.dependencies.document_policy is not None:
                raise CodingRunResumeError("unexpected document policy on an off run")
            return
        if (
            self._run_lock is None
            or self.dependencies.document_port is None
            or self.dependencies.document_validator is None
        ):
            raise CodingRunResumeError(
                "bounded document policy requires host-composed search and validation ports"
            )
        policy = DocumentPolicy(
            state_dir=self.request.state_dir,
            workspace=self.request.workspace,
            lock=self._run_lock,
            port=self.dependencies.document_port,
            validator=self.dependencies.document_validator,
            usage=self.dependencies,
        )
        configuration = PolicyConfiguration(
            run_id=self.request.run_id,
            binding=binding.evidence_binding,
            scope=binding.spec.scope,
        )
        needs = tuple(n.host_need() for n in binding.spec.host_needs)
        evaluation_limits = self.request.evaluation_tool_limits
        document_lookup_limit = (
            evaluation_limits.concept_lookup_calls
            if evaluation_limits is not None
            and evaluation_limits.concept_lookup_calls is not None
            else 2
        )
        if restoring:
            policy.restore(configuration, checkpoint=self._document_checkpoint)
            if policy.journal.state.revision > self._current_task_revision:
                raise CodingRunResumeError(
                    "document revision exceeds host task revision"
                )
            while policy.journal.state.revision < self._current_task_revision:
                policy.host_start_revision(needs, scope=binding.spec.scope)
            if policy.journal.state.limit != document_lookup_limit:
                policy.host_amend_lookup_limit(document_lookup_limit)
        else:
            policy.create(
                configuration,
                max_lookup_calls=document_lookup_limit,
                host_needs=needs,
            )
        self.dependencies.document_policy = policy
        for operation in binding.spec.independent_operations:
            selected = tuple(
                n.id
                for n in policy.needs
                if n.origin != "model_proposed"
                and " ".join(n.condition.split()).casefold()
                in {
                    " ".join(c.split()).casefold()
                    for c in operation.independent_of_conditions
                }
            )
            if policy.journal.state.independent.get(operation.operation_id) != set(
                selected
            ):
                policy.host_declare_independent(
                    operation.operation_id, selected, operation.host_reference
                )
        for clarification in clarifications:
            self._document_clarification(clarification)
        self._document_checkpoint = policy.checkpoint()

    def _document_clarification(self, event: ClarificationEvent) -> None:
        binding, policy = (
            self.request.document_policy,
            self.dependencies.document_policy,
        )
        if binding is None or policy is None or event.answer is None:
            return
        for declared in binding.spec.host_needs:
            if declared.clarification_field_id != event.field_id:
                continue
            matching = [
                n
                for n in policy.needs
                if n.condition == declared.condition and n.kind == "physical_input"
            ]
            if len(matching) != 1:
                raise CodingRunResumeError(
                    "declared physical input is missing from document policy"
                )
            need = matching[0]
            if policy.journal.state.statuses[need.id] != "provided":
                policy.host_provide_input(
                    need.id, "host clarification: " + event.field_id
                )

    async def _document_verification_denial(self) -> str | None:
        if self.request.document_policy is None:
            return None
        policy = self.dependencies.document_policy
        if policy is None:
            return "document policy is required but unavailable"
        try:
            gate = await policy.validate_execution_gate(
                "ready_for_verification",
                deadline=self.dependencies.document_operation_deadline(),
            )
        except (DocumentPolicyDenied, TimeoutError) as exc:
            return str(exc)
        return (
            None
            if gate.allowed
            else gate.reason + ": " + ", ".join(gate.unresolved_need_ids)
        )

    def _progress(self, message: str) -> None:
        if self.progress_sink is not None:
            self.progress_sink(message)


def _changed_snapshot_paths(
    expected: WorkspaceSnapshot,
    actual: WorkspaceSnapshot,
) -> tuple[str, ...]:
    expected_by_path = expected.by_path()
    actual_by_path = actual.by_path()
    return tuple(
        sorted(
            path
            for path in expected_by_path.keys() | actual_by_path.keys()
            if expected_by_path.get(path) != actual_by_path.get(path)
        )
    )


def _frontier_history(frontier: DeferredToolFrontier) -> list[ModelMessage]:
    try:
        raw = frontier.history_json.encode("utf-8")
    except UnicodeError as exc:
        raise CodingRunResumeError("deferred Solver history is not UTF-8") from exc
    if hashlib.sha256(raw).hexdigest() != frontier.history_sha256:
        raise CodingRunResumeError("deferred Solver history checksum mismatch")
    try:
        return ModelMessagesTypeAdapter.validate_json(raw)
    except ValueError as exc:
        raise CodingRunResumeError(
            "deferred Solver history cannot be restored"
        ) from exc


def _validate_deferred_workspace(
    before: WorkspaceSnapshot,
    current: WorkspaceSnapshot,
    result: AbaqusExecutionResult,
) -> None:
    changed_paths = set(_changed_snapshot_paths(before, current))
    before_by_path = before.by_path()
    current_by_path = current.by_path()
    evidence_paths: set[str] = set()

    for change in result.artifact_changes:
        if change.path in evidence_paths:
            raise CodingRunWorkspaceChanged(
                f"deferred Solver reported duplicate artifact evidence: {change.path}"
            )
        evidence_paths.add(change.path)
        old = before_by_path.get(change.path)
        new = current_by_path.get(change.path)

        if change.before is not None and change.before.path != change.path:
            raise CodingRunWorkspaceChanged(
                f"deferred Solver before fingerprint path mismatch: {change.path}"
            )
        if change.after is not None and change.after.path != change.path:
            raise CodingRunWorkspaceChanged(
                f"deferred Solver after fingerprint path mismatch: {change.path}"
            )

        if change.kind is ArtifactChangeKind.CREATED:
            valid = (
                old is None
                and new is not None
                and change.before is None
                and change.after is not None
                and change.after.size_bytes == new.size_bytes
                and change.after.sha256 == new.sha256
            )
        elif change.kind is ArtifactChangeKind.MODIFIED:
            valid = (
                old is not None
                and new is not None
                and change.before is not None
                and change.after is not None
                and change.before.size_bytes == old.size_bytes
                and change.before.sha256 == old.sha256
                and change.after.size_bytes == new.size_bytes
                and change.after.sha256 == new.sha256
            )
        elif change.kind is ArtifactChangeKind.DELETED:
            valid = (
                old is not None
                and new is None
                and change.before is not None
                and change.after is None
                and change.before.size_bytes == old.size_bytes
                and change.before.sha256 == old.sha256
            )
        else:
            valid = False
        if not valid:
            raise CodingRunWorkspaceChanged(
                f"deferred Solver artifact evidence does not match workspace: {change.path}"
            )

    if changed_paths != evidence_paths:
        unexplained = sorted(changed_paths - evidence_paths)
        stale = sorted(evidence_paths - changed_paths)
        detail = []
        if unexplained:
            detail.append("unexplained=" + ", ".join(unexplained[:10]))
        if stale:
            detail.append("stale=" + ", ".join(stale[:10]))
        raise CodingRunWorkspaceChanged(
            "deferred Solver workspace evidence mismatch: " + "; ".join(detail)
        )


def _merge_continuation_prompt(
    continuation_prompt: str,
    pending_amendment_prompt: str | None,
) -> str:
    if pending_amendment_prompt is None:
        return continuation_prompt
    return f"{continuation_prompt}\n\n{pending_amendment_prompt}"


def _compose_resume_prompt(
    continuation_prompt: str,
    pending_amendment_prompt: str | None,
    *,
    user_prompt: str | None,
    new_continuation: bool,
) -> str:
    if user_prompt is None:
        base = continuation_prompt
    elif new_continuation:
        base = user_prompt
    else:
        base = (
            f"{continuation_prompt}\n\nThe active user request is still:\n{user_prompt}"
        )
    return _merge_continuation_prompt(base, pending_amendment_prompt)


def _usage(
    usage: RunUsage,
    dependencies: CodingAgentDependencies,
    _started: float,
) -> CodingRunUsage:
    return CodingRunUsage(
        model_requests=usage.requests,
        input_tokens=usage.input_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        output_tokens=usage.output_tokens,
        provider_details=dict(usage.details),
        tool_calls=usage.tool_calls,
        api_lookup_calls=dependencies.api_lookup_calls,
        concept_lookup_calls=dependencies.concept_lookup_calls,
        diagnostic_retrieval_calls=dependencies.diagnostic_retrieval_calls,
        syntax_edit_checks=(
            dependencies.syntax_gate.edit_checks
            if dependencies.syntax_gate is not None
            else 0
        ),
        syntax_runtime_checks=(
            dependencies.syntax_gate.runtime_checks
            if dependencies.syntax_gate is not None
            else 0
        ),
        syntax_denials=(
            dependencies.syntax_gate.denials
            if dependencies.syntax_gate is not None
            else 0
        ),
        static_preflight_calls=dependencies.static_preflight_calls,
        abaqus_calls=dependencies.abaqus_calls,
        solver_calls=dependencies.solver_calls,
        wall_time_seconds=dependencies.active_wall.elapsed_seconds,
        estimated_cost_usd=float(usage.cost) if usage.cost is not None else None,
    )
