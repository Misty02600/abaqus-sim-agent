"""宿主拥有的问题、答复与deferred交接；不识别或补造物理参数。"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import (
    CallDeferred,
    DeferredToolRequests,
    ModelMessagesTypeAdapter,
    ModelRetry,
    RunContext,
)
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ToolCallPart

from .contracts import CodingRunDecision, CodingRunUsage, ResponseDecision
from .dependencies import CodingAgentDependencies
from .persistence import write_bytes_atomic, write_text_atomic
from .run_lock import CodingRunFileLock

QuestionProtocol = Literal["legacy_decision", "ask_user_v1"]
CodingAgentOutput = CodingRunDecision | ResponseDecision | DeferredToolRequests
QUESTION_FILE = "user_questions.json"


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class UserQuestion(_Strict):
    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    task_revision: int = Field(ge=0)
    turn: int = Field(ge=1)
    abaqus_calls_before: int = Field(ge=0)
    revision_had_runtime: bool
    goal_at_question: str
    execution_count: int = Field(ge=0)
    history_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    asked_at: AwareDatetime
    answer: str | None = None
    source: Literal["console", "file", "scripted", "web"] | None = None
    resolved_at: AwareDatetime | None = None
    cancelled: bool = False
    answer_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def consistent(self):
        if not self.question.strip() or not self.question_id.strip():
            raise ValueError("question and id must be nonblank")
        if self.answer is not None and (not self.answer.strip() or self.cancelled):
            raise ValueError(
                "answer must be nonblank and cannot coexist with cancellation"
            )
        if (self.resolved_at is not None) != (
            self.answer is not None or self.cancelled
        ):
            raise ValueError("resolution requires its timestamp")
        if (self.source is not None) != (self.answer is not None):
            raise ValueError("answer requires an explicit source")
        if self.answer_revision is not None and (
            self.answer is None
            or self.answer_revision
            != self.task_revision + int(self.revision_had_runtime)
        ):
            raise ValueError(
                "answer revision must match the question's evidence boundary"
            )
        return self

    @property
    def pending(self) -> bool:
        return self.resolved_at is None


class QuestionLedger(_Strict):
    schema_version: Literal["user-questions-v1"] = "user-questions-v1"
    run_id: str
    questions: tuple[UserQuestion, ...] = ()

    @model_validator(mode="after")
    def unique(self):
        ids = [q.question_id for q in self.questions]
        if len(ids) != len(set(ids)) or any(q.pending for q in self.questions[:-1]):
            raise ValueError(
                "questions must be unique and only the last may be pending"
            )
        return self

    @property
    def wait_seconds(self) -> float:
        return sum(
            max(
                0.0, ((q.resolved_at or datetime.now(UTC)) - q.asked_at).total_seconds()
            )
            for q in self.questions
        )


class WaitingForUser(_Strict):
    status: Literal["waiting_for_user"] = "waiting_for_user"
    run_id: str
    state_dir: Path
    task_revision: int
    question: UserQuestion
    usage: CodingRunUsage
    question_wait_seconds: float


class UserAnswerInput(_Strict):
    question_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)


@dataclass
class UserQuestionCapability(AbstractCapability):
    async def after_model_request(self, ctx, *, request_context, response):
        calls = [part for part in response.parts if isinstance(part, ToolCallPart)]
        if any(call.tool_name == "ask_user" for call in calls) and len(calls) != 1:
            raise ModelRetry(
                "Call ask_user alone. Choose all further actions in a new response after the user answers."
            )
        return response


def register_ask_user(agent):
    @agent.tool(
        sequential=True,
        description="Ask the user for blocking information or intent, explaining its impact in the question. Never guess missing physical values. Call this tool alone.",
    )
    def ask_user(ctx: RunContext[CodingAgentDependencies], question: str) -> str:
        """校验问题和预算后交回宿主，答复由延期工具结果提供。

        Raises:
            ModelRetry: 问题为空或已达到提问上限。
            CallDeferred: 问题可交给宿主接收用户答复。
        """
        if not question.strip():
            raise ModelRetry("Provide a nonblank question.")
        if (
            len(
                load_questions(
                    ctx.deps.request.state_dir, ctx.deps.request.run_id
                ).questions
            )
            >= ctx.deps.request.budget.max_clarifications
        ):
            raise ModelRetry(
                "User question budget exhausted. Do not ask again; report the evidenced failure."
            )
        raise CallDeferred


def _history_path(state_dir: Path, question_id: str) -> Path:
    return (
        state_dir
        / "user-questions"
        / (hashlib.sha256(question_id.encode()).hexdigest() + ".json")
    )


def load_questions(state_dir: Path, run_id: str) -> QuestionLedger:
    path = state_dir / QUESTION_FILE
    if not path.exists():
        return QuestionLedger(run_id=run_id)
    if path.is_symlink() or path.resolve().parent != state_dir.resolve():
        raise ValueError("question ledger cannot redirect outside state")
    result = QuestionLedger.model_validate_json(path.read_bytes())
    if result.run_id != run_id:
        raise ValueError("question ledger belongs to another run")
    return result


def _save(state_dir: Path, ledger: QuestionLedger, lock: CodingRunFileLock):
    lock.assert_held(state_dir)
    checked = QuestionLedger.model_validate_json(ledger.model_dump_json())
    write_text_atomic(state_dir / QUESTION_FILE, checked.model_dump_json(indent=2))


def _resolved_frontier(state_dir: Path, q: UserQuestion, lock: CodingRunFileLock):
    """将已解决问题的停顿状态同步为待继续，不重开terminal运行。"""
    from .resume import load_resume_state, write_resume_state

    lock.assert_held(state_dir)
    state = load_resume_state(state_dir)
    if (
        not q.pending
        and state.turn == q.turn
        and state.status in ("waiting_for_user", "running")
    ):
        write_resume_state(
            state_dir,
            state.model_copy(
                update={"status": "interrupted", "pending_question_id": q.question_id}
            ),
        )


def record_question(
    state_dir: Path,
    run_id: str,
    question: UserQuestion,
    history: bytes,
    lock: CodingRunFileLock,
):
    lock.assert_held(state_dir)
    ledger = load_questions(state_dir, run_id)
    if any(q.question_id == question.question_id for q in ledger.questions):
        raise ValueError("question call id was already used")
    if hashlib.sha256(history).hexdigest() != question.history_sha256:
        raise ValueError("question history hash mismatch")
    path = _history_path(state_dir, question.question_id)
    if path.parent.is_symlink() or path.exists():
        raise ValueError("question history must be a new state-owned file")
    write_bytes_atomic(path, history)
    _save(
        state_dir,
        ledger.model_copy(update={"questions": (*ledger.questions, question)}),
        lock,
    )


def question_history(state_dir: Path, question: UserQuestion) -> list[ModelMessage]:
    path = _history_path(state_dir, question.question_id)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("question history cannot be redirected")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != question.history_sha256:
        raise ValueError("question history changed")
    messages = ModelMessagesTypeAdapter.validate_json(raw)
    calls = (
        [p for p in messages[-1].parts if isinstance(p, ToolCallPart)]
        if messages
        else []
    )
    if (
        len(calls) != 1
        or calls[0].tool_name != "ask_user"
        or calls[0].tool_call_id != question.question_id
        or calls[0].args_as_dict() != {"question": question.question}
    ):
        raise ValueError("question does not match its deferred history")
    return messages


async def _check_runtime_frontier(state_dir: Path, request):
    from .resume import (
        CodingRunReconciliationRequired,
        create_step_store,
        load_resume_state,
        select_resume_history,
        write_resume_state,
    )

    try:
        await select_resume_history(create_step_store(state_dir), request)
    except CodingRunReconciliationRequired as exc:
        state = load_resume_state(state_dir)
        write_resume_state(
            state_dir,
            state.model_copy(
                update={
                    "status": "needs_reconcile",
                    "last_error_type": type(exc).__name__,
                    "last_error_message": str(exc),
                }
            ),
        )
        raise


async def inspect_question(state_dir: Path) -> UserQuestion | None:
    """恢复前核对锁、工作区及未决runtime，不能以答题绕过reconcile。"""
    from .resume import (
        load_effective_run_for_resume,
        load_resume_state,
        write_resume_state,
    )
    from .workspace import WorkspaceResolver

    state_dir = await asyncio.to_thread(state_dir.resolve, strict=True)
    with CodingRunFileLock(state_dir) as lock:
        effective = load_effective_run_for_resume(state_dir)
        state = load_resume_state(state_dir)
        if state.question_protocol != "ask_user_v1":
            return None
        if state.status == "needs_reconcile":
            raise ValueError("runtime reconciliation required")
        await _check_runtime_frontier(state_dir, effective.request)
        ledger = load_questions(state_dir, effective.request.run_id)
        if state.pending_question_id and not any(
            q.question_id == state.pending_question_id for q in ledger.questions
        ):
            raise ValueError("pending question is missing")
        expected_snapshot = (
            effective.revision_workspace_snapshot
            if effective.task_revision > state.task_revision
            else state.workspace_snapshot
        )
        if (
            WorkspaceResolver().snapshot(effective.request.workspace)
            != expected_snapshot
        ):
            raise ValueError("workspace changed while awaiting a user answer")
        if ledger.questions:
            question_history(state_dir, ledger.questions[-1])
        ledger = ensure_answer_revision(state_dir, ledger, lock)
        effective = load_effective_run_for_resume(state_dir)
        if not ledger.questions or not ledger.questions[-1].pending:
            if ledger.questions:
                _resolved_frontier(state_dir, ledger.questions[-1], lock)
            return None
        q = ledger.questions[-1]
        if state.status in ("completed", "failed") or q.turn != state.turn:
            raise ValueError("pending question conflicts with the saved run frontier")
        if q.task_revision != effective.task_revision:
            raise ValueError("pending question belongs to an older task revision")
        question_history(state_dir, q)
        if (
            state.status != "waiting_for_user"
            or state.pending_question_id != q.question_id
        ):
            write_resume_state(
                state_dir,
                state.model_copy(
                    update={
                        "status": "waiting_for_user",
                        "pending_question_id": q.question_id,
                    }
                ),
            )
        return q


def ensure_answer_revision(
    state_dir: Path, ledger: QuestionLedger, lock: CodingRunFileLock
) -> QuestionLedger:
    """晚到答复先保存，再以稳定reason关联revision；崩溃重入不追加第二次修订。"""
    from .amendments import _append_amendment_locked
    from .resume import load_effective_run_for_resume

    lock.assert_held(state_dir)
    if not ledger.questions:
        return ledger
    q = ledger.questions[-1]
    if q.answer is None or q.answer_revision is not None:
        return ledger
    effective = load_effective_run_for_resume(state_dir)
    marker = (
        "user-question-answer:" + hashlib.sha256(q.question_id.encode()).hexdigest()
    )
    matches = [a for a in effective.ledger.amendments if a.reason == marker]
    expected_goal = (
        q.goal_at_question
        + f"\n\nUser clarification:\n{q.question}\nAnswer: {q.answer}"
    )
    if matches:
        if (
            len(matches) != 1
            or matches[0].kind != "task_revision"
            or matches[0].goal != expected_goal
            or matches[0].task_revision != q.task_revision + 1
            or matches[0].revision_execution_start_index != q.execution_count
            or matches[0].revision_workspace_snapshot is None
            or hashlib.sha256(
                matches[0].revision_workspace_snapshot.model_dump_json().encode()
            ).hexdigest()
            != q.workspace_sha256
        ):
            raise ValueError("answer revision receipt does not match its question")
        revision = matches[0].task_revision
    else:
        if effective.task_revision != q.task_revision:
            raise ValueError("question revision changed before the answer was applied")
        revision = effective.task_revision
        if q.revision_had_runtime:
            amendment = _append_amendment_locked(
                state_dir,
                lock=lock,
                kind="task_revision",
                reason=marker,
                goal=expected_goal,
                resolving_question=True,
            )
            revision = amendment.task_revision
    updated = q.model_copy(update={"answer_revision": revision})
    ledger = ledger.model_copy(update={"questions": (*ledger.questions[:-1], updated)})
    _save(state_dir, ledger, lock)
    return ledger


async def submit_answer(
    state_dir: Path,
    answer: UserAnswerInput,
    *,
    source: Literal["console", "file", "scripted", "web"] = "file",
    expected_task_revision: int | None = None,
) -> UserQuestion:
    """在同一state锁内保存答复，必要时完成幂等的验收revision切换。

    Note:
        同ID同答复重入不覆盖记录；冲突、错revision、工作区漂移或未决runtime拒绝。
        答复先落盘，因此revision提交中断后可由inspect_question恢复，不再次询问用户。
    """
    from .resume import (
        load_effective_run_for_resume,
        load_resume_state,
    )
    from .workspace import WorkspaceResolver

    state_dir = await asyncio.to_thread(state_dir.resolve, strict=True)
    with CodingRunFileLock(state_dir) as lock:
        effective = load_effective_run_for_resume(state_dir)
        state = load_resume_state(state_dir)
        if (
            expected_task_revision is not None
            and effective.task_revision != expected_task_revision
        ):
            raise ValueError("task revision changed before the answer was saved")
        if (
            state.question_protocol != "ask_user_v1"
            or state.status == "needs_reconcile"
        ):
            raise ValueError("run cannot accept a question answer")
        await _check_runtime_frontier(state_dir, effective.request)
        ledger = load_questions(state_dir, effective.request.run_id)
        if (
            not ledger.questions
            or ledger.questions[-1].question_id != answer.question_id
        ):
            raise ValueError("answer must match the latest question id")
        q = ledger.questions[-1]
        question_history(state_dir, q)
        if (
            effective.task_revision not in (q.task_revision, q.answer_revision)
            or q.cancelled
        ):
            raise ValueError("answer belongs to an obsolete or cancelled question")
        if (
            WorkspaceResolver().snapshot(effective.request.workspace)
            != state.workspace_snapshot
        ):
            raise ValueError("workspace changed while awaiting a user answer")
        if q.answer is not None:
            if q.answer != answer.answer:
                raise ValueError("cannot overwrite a previously recorded answer")
            resolved = ensure_answer_revision(state_dir, ledger, lock).questions[-1]
            _resolved_frontier(state_dir, resolved, lock)
            return resolved
        if state.status in ("completed", "failed") or q.turn != state.turn:
            raise ValueError("pending question conflicts with the saved run frontier")
        if not answer.answer.strip():
            raise ValueError("answer must be nonblank")
        updated = q.model_copy(
            update={
                "answer": answer.answer,
                "source": source,
                "resolved_at": datetime.now(UTC),
            }
        )
        ledger = ledger.model_copy(
            update={"questions": (*ledger.questions[:-1], updated)}
        )
        _save(state_dir, ledger, lock)
        resolved = ensure_answer_revision(state_dir, ledger, lock).questions[-1]
        _resolved_frontier(state_dir, resolved, lock)
        return resolved


async def cancel_question(state_dir: Path, question_id: str) -> None:
    """显式撤销最近的未答问题；不伪造答复，不绕过未决runtime。"""
    from .resume import (
        load_effective_run_for_resume,
        load_resume_state,
    )

    state_dir = await asyncio.to_thread(state_dir.resolve, strict=True)
    with CodingRunFileLock(state_dir) as lock:
        effective = load_effective_run_for_resume(state_dir)
        state = load_resume_state(state_dir)
        if state.status == "needs_reconcile":
            raise ValueError("runtime reconciliation required")
        if state.status in ("completed", "failed"):
            raise ValueError("terminal run has no cancellable pending question")
        await _check_runtime_frontier(state_dir, effective.request)
        ledger = load_questions(state_dir, effective.request.run_id)
        if not ledger.questions or ledger.questions[-1].question_id != question_id:
            raise ValueError("cancellation must match the latest question")
        q = ledger.questions[-1]
        question_history(state_dir, q)
        if q.answer is not None or q.task_revision != effective.task_revision:
            raise ValueError("cannot cancel an answered or obsolete question")
        if not q.cancelled:
            q = q.model_copy(
                update={"cancelled": True, "resolved_at": datetime.now(UTC)}
            )
            _save(
                state_dir,
                ledger.model_copy(update={"questions": (*ledger.questions[:-1], q)}),
                lock,
            )
        _resolved_frontier(state_dir, q, lock)
