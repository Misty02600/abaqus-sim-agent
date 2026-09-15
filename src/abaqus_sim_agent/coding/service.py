from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic, perf_counter

from pydantic import BaseModel, ConfigDict
from pydantic_ai import (
    Agent,
    DeferredToolRequests,
    DeferredToolResults,
    ModelMessagesTypeAdapter,
    RunUsage,
    UsageLimitExceeded,
    UsageLimits,
)
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai_harness.step_persistence import StepStore

from abaqus_sim_agent.coding.amendments import amendment_prompt
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
)
from abaqus_sim_agent.coding.dependencies import (
    CodingAgentDependencies,
    RuntimeBudgetExceeded,
)
from abaqus_sim_agent.coding.document_policy import DocumentPolicy
from abaqus_sim_agent.coding.document_policy_contracts import (
    PolicyCheckpoint,
    PolicyConfiguration,
)
from abaqus_sim_agent.coding.document_policy_state import DocumentPolicyDenied
from abaqus_sim_agent.coding.persistence import write_bytes_atomic, write_text_atomic
from abaqus_sim_agent.coding.resume import (
    CodingRunReconciliationRequired,
    CodingRunResumeError,
    CodingRunWorkspaceChanged,
    ResumeStatus,
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
    remaining_wall_time_seconds: float


@dataclass(slots=True)
class CodingRunService[VerificationAttemptT: VerificationAttempt]:
    agent: Agent[CodingAgentDependencies, CodingAgentOutput]
    request: CodingRunRequest
    dependencies: CodingAgentDependencies
    clarification_provider: ClarificationProvider
    verifier: CodingRunVerifier[VerificationAttemptT]
    initial_snapshot: WorkspaceSnapshot
    progress_sink: ProgressSink | None = None
    verify_on_budget_exhaustion: bool = False
    resume: bool = False
    step_store: StepStore | None = None
    launch_manifest_json: str | None = None
    expected_amendment_sequence: int | None = None
    question_protocol: QuestionProtocol = "legacy_decision"
    _pending_question: UserQuestion | None = field(default=None, init=False, repr=False)
    _deferred_input: DeferredToolResults | None = field(
        default=None, init=False, repr=False
    )
    _current_turn: int = field(default=0, init=False, repr=False)
    _current_amendment_sequence: int = field(default=0, init=False, repr=False)
    _current_task_revision: int = field(default=0, init=False, repr=False)
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

    async def run_step(self) -> CodingRunResult[VerificationAttemptT] | WaitingForUser:
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
    ) -> CodingRunResult[VerificationAttemptT] | WaitingForUser:
        start = await self._prepare_start()
        started = start.started
        shared_usage = start.usage
        clarifications = start.clarifications
        history = start.history
        prompt = start.prompt
        verification: VerificationAttemptT | None = None
        turn = start.turn
        if self._pending_question is not None and self._pending_question.pending:
            return self._waiting(shared_usage, clarifications, started)
        self.dependencies.document_deadline = (
            monotonic() + start.remaining_wall_time_seconds
        )

        if start.remaining_wall_time_seconds <= 0:
            return self._failed(
                "timeout",
                "Coding run exhausted its cumulative wall-clock budget",
                shared_usage,
                clarifications,
                started,
            )

        try:
            async with asyncio.timeout(start.remaining_wall_time_seconds):
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
                            request_limit=self.request.budget.max_model_requests,
                        ),
                    ) as agent_run:
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
                                previous_node_completed = True
                        finally:
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
                    if isinstance(decision, DeferredToolRequests):
                        if (
                            self.question_protocol != "ask_user_v1"
                            or decision.approvals
                            or len(decision.calls) != 1
                            or decision.calls[0].tool_name != "ask_user"
                        ):
                            raise ValueError("unsupported deferred tool request")
                        ledger = load_questions(
                            self.request.state_dir, self.request.run_id
                        )
                        if (
                            len(ledger.questions)
                            >= self.request.budget.max_clarifications
                        ):
                            return self._failed(
                                "clarification_budget",
                                "User question budget exhausted",
                                shared_usage,
                                clarifications,
                                started,
                            )
                        call = decision.calls[0]
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

                    if isinstance(decision, NeedsInputDecision):
                        if self.dependencies.abaqus_calls:
                            return self._failed(
                                "clarification",
                                "Agent invoked Abaqus before obtaining required input",
                                shared_usage,
                                clarifications,
                                started,
                            )
                        if (
                            len(clarifications)
                            >= self.request.budget.max_clarifications
                        ):
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

                    if (
                        self.dependencies.abaqus_calls
                        >= self.request.budget.max_abaqus_calls
                    ):
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

    async def _prepare_start(self) -> _RunStartState:
        from .launch import LaunchManifest

        launch_path = self.request.state_dir / "launch.json"
        raw_launch = (
            launch_path.read_bytes()
            if self.resume and launch_path.is_file()
            else self.launch_manifest_json
        )
        if raw_launch is not None:
            launch = LaunchManifest.model_validate_json(raw_launch)
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
        return self._prepare_new_start()

    def _prepare_new_start(self) -> _RunStartState:
        ensure_new_run_state_available(self.request.state_dir)
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
            remaining_wall_time_seconds=self.request.budget.wall_time_seconds,
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
        if persisted.status in {"completed", "failed"} and not has_pending_amendment:
            raise CodingRunResumeError(
                f"CodingRun is terminal and has no new amendment: {persisted.status}"
            )
        if effective.task_revision < persisted.task_revision:
            raise CodingRunResumeError("task revision moved backwards")
        task_revision_changed = effective.task_revision > persisted.task_revision
        self._current_amendment_sequence = effective.amendment_sequence
        self._current_task_revision = effective.task_revision
        self.dependencies.current_amendment_sequence = effective.amendment_sequence
        self.dependencies.current_task_revision = effective.task_revision
        self._revision_workspace_snapshot = effective.revision_workspace_snapshot
        self._revision_execution_start_index = effective.revision_execution_start_index
        usage = persisted.usage.restore()
        clarifications = [] if task_revision_changed else list(persisted.clarifications)
        persisted.dependencies.restore_into(
            self.dependencies,
            journaled_executions=load_execution_results(self.request.state_dir),
            journaled_api_lookups=load_api_lookup_traces(self.request.state_dir),
            journaled_api_discoveries=load_api_discovery_traces(
                self.request.state_dir
            ),
            journaled_diagnostic_retrievals=load_diagnostic_retrieval_traces(
                self.request.state_dir
            ),
            journaled_document_lookups=load_document_lookup_traces(
                self.request.state_dir
            ),
        )
        if task_revision_changed:
            self.dependencies.set_concept_lookup_trigger(
                "task_revision",
                "feedback",
                reference=f"task_revision:{effective.task_revision}",
                evidence=effective.request.goal,
            )
        if self._revision_execution_start_index > len(self.dependencies.executions):
            raise CodingRunResumeError(
                "task revision execution boundary exceeds persisted executions"
            )
        self._current_turn = persisted.turn
        started = perf_counter() - persisted.active_wall_time_seconds
        assert self.step_store is not None
        try:
            continuation = await select_resume_history(
                self.step_store,
                self.request,
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

        resume_messages = list(continuation.messages)
        if self.question_protocol == "ask_user_v1":
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
            else _merge_continuation_prompt(
                continuation.prompt,
                amendment_prompt(
                    effective.ledger,
                    after_sequence=persisted.amendment_sequence,
                ),
            ),
            turn=persisted.turn,
            remaining_wall_time_seconds=max(
                0.0,
                self.request.budget.wall_time_seconds - (perf_counter() - started),
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
            revision_workspace_snapshot=self._verification_snapshot(),
            revision_execution_start_index=self._revision_execution_start_index,
            active_wall_time_seconds=max(0.0, perf_counter() - started),
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
        if restoring:
            policy.restore(configuration, checkpoint=self._document_checkpoint)
            if policy.journal.state.revision > self._current_task_revision:
                raise CodingRunResumeError(
                    "document revision exceeds host task revision"
                )
            while policy.journal.state.revision < self._current_task_revision:
                policy.host_start_revision(needs, scope=binding.spec.scope)
            if (
                policy.journal.state.limit
                != self.request.budget.max_concept_lookup_calls
            ):
                policy.host_amend_lookup_limit(
                    self.request.budget.max_concept_lookup_calls
                )
        else:
            policy.create(
                configuration,
                max_lookup_calls=self.request.budget.max_concept_lookup_calls,
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
                "ready_for_verification", deadline=self.dependencies.document_deadline
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


def _merge_continuation_prompt(
    continuation_prompt: str,
    pending_amendment_prompt: str | None,
) -> str:
    if pending_amendment_prompt is None:
        return continuation_prompt
    return f"{continuation_prompt}\n\n{pending_amendment_prompt}"


def _usage(
    usage: RunUsage,
    dependencies: CodingAgentDependencies,
    started: float,
) -> CodingRunUsage:
    return CodingRunUsage(
        model_requests=usage.requests,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
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
        wall_time_seconds=perf_counter() - started,
        estimated_cost_usd=float(usage.cost) if usage.cost is not None else None,
    )
