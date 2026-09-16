"""产品证据的只读投影；磁盘读取要求持有state锁，活动段使用宿主传入的bytes。"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)

from .application import ConversationMessage, PreparedCodingRun
from .contracts import CodingRunOutcome, CodingRunUsage
from .execution_verifier import ExecutionEvidenceVerification
from .progress import CodingProgress
from .response_stream import LiveReply, StoppedReply, public_response_text
from .resume import CodingRunResumeState
from .run_lock import CodingRunFileLock
from .user_questions import QuestionLedger, UserQuestion, question_history

MAX_RECORD_BYTES = 16 * 1024 * 1024


class ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ToolSummary(ViewModel):
    target: str | None = None
    stage_succeeded: bool | None = None
    duration_seconds: float | None = None
    timed_out: bool = False
    warning_count: int = 0
    error: str | None = None
    changed_paths: tuple[str, ...] = ()
    changed_count: int = 0


class MessageView(ViewModel):
    id: str
    role: Literal["user", "assistant"]
    kind: Literal["text", "tool"] = "text"
    text: str = ""
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments: str = ""
    output: str | None = None
    tool_summary: ToolSummary | None = None
    status: Literal[
        "pending", "completed", "failed", "waiting_for_user", "interrupted"
    ] = "completed"


class QuestionView(ViewModel):
    question_id: str
    question: str
    task_revision: int
    pending: bool
    answer: str | None = None


class FileView(ViewModel):
    id: str
    relative_path: str
    size_bytes: int
    sha256: str
    fresh: bool


class CodingRunView(ViewModel):
    run_id: str
    goal: str
    task_revision: int = 0
    amendment_sequence: int = 0
    status: Literal[
        "registered",
        "running",
        "waiting_for_user",
        "waiting_for_tool_execution",
        "interrupted",
        "needs_reconcile",
        "completed",
        "failed",
        "unavailable",
    ] = "registered"
    phase: str = "registered"
    question: QuestionView | None = None
    outcome: CodingRunOutcome | None = None
    verification: ExecutionEvidenceVerification | None = None
    usage: CodingRunUsage = CodingRunUsage()
    messages: tuple[MessageView, ...] = ()
    files: tuple[FileView, ...] = ()
    error: str | None = None
    live_reply: LiveReply | None = None
    latest_model_input_tokens: int | None = None


def record_bytes(path: Path) -> bytes | None:
    if not path.exists():
        return None
    if path.is_symlink() or path.is_junction() or not path.is_file():
        raise ValueError("state record is not a regular file")
    with path.open("rb") as stream:
        data = stream.read(MAX_RECORD_BYTES + 1)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("state record exceeds display limit")
    return data


def preview(value: object) -> str:
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, default=str)
    )
    return text[:16_384] + ("\n…（详情已截断）" if len(text) > 16_384 else "")


def _target(arguments: object) -> str | None:
    if isinstance(arguments, dict):
        for key in ("script_path", "input_file", "path", "job_name"):
            value = arguments.get(key)
            if isinstance(value, str):
                return value[:500]
    return None


def _tool_summary(previous: ToolSummary | None, output: object) -> ToolSummary:
    """从未截断的结构化返回提取摘要，不解析日志或按exit code推断成功。"""
    data = output if isinstance(output, dict) else {}
    duration = data.get("duration_seconds")
    stage = data.get("stage_succeeded")
    diagnostics = data.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, (list, tuple)) else ()
    warnings = sum(
        isinstance(item, dict) and item.get("level") == "warning"
        for item in diagnostics
    )
    error = next(
        (
            item.get("message")
            for item in diagnostics
            if isinstance(item, dict)
            and item.get("level") == "error"
            and isinstance(item.get("message"), str)
        ),
        None,
    )
    if error is None and (
        data.get("succeeded") is False
        or data.get("stage_succeeded") is False
        or data.get("blocked") is True
    ):
        error = next(
            (
                data.get(key)
                for key in ("error", "feedback")
                if isinstance(data.get(key), str)
            ),
            None,
        )
    changes = data.get("artifact_changes")
    changes = changes if isinstance(changes, (list, tuple)) else ()
    paths = tuple(
        item["path"][:500]
        for item in changes
        if isinstance(item, dict)
        and item.get("kind") in ("created", "modified")
        and isinstance(item.get("path"), str)
    )
    duration_seconds = (
        float(duration)
        if isinstance(duration, (int, float))
        and not isinstance(duration, bool)
        and math.isfinite(duration)
        and duration >= 0
        else None
    )
    return ToolSummary(
        target=previous.target if previous else None,
        stage_succeeded=stage if isinstance(stage, bool) else None,
        duration_seconds=duration_seconds,
        timed_out=data.get("timed_out") is True,
        warning_count=warnings,
        error=error[:300] if isinstance(error, str) else None,
        changed_paths=paths[:6],
        changed_count=len(paths),
    )


def message_views(
    run_id: str,
    goal: str,
    history: bytes | None,
    question: QuestionView | None,
    conversation_messages: tuple[ConversationMessage, ...] = (),
    stopped_replies: tuple[StoppedReply, ...] = (),
) -> tuple[MessageView, ...]:
    result = [MessageView(id=f"{run_id}:goal", role="user", text=goal)]
    pending: dict[str, int] = {}
    answered: set[str] = set()
    displayed: set[str] = set()
    stopped_displayed: set[str] = set()
    response_counts: dict[int, int] = {}

    def append_stopped(item: StoppedReply) -> None:
        if item.request_id not in stopped_displayed:
            result.append(
                MessageView(
                    id=f"{run_id}:stopped:{item.request_id}",
                    role="assistant",
                    text=item.text,
                    status="interrupted",
                )
            )
            stopped_displayed.add(item.request_id)

    def append_messages(before_turn: int | None = None) -> None:
        for item in conversation_messages:
            if item.message_id not in displayed and (
                before_turn is None or item.submitted_after_turn < before_turn
            ):
                result.append(
                    MessageView(
                        id=f"{run_id}:message:{item.message_id}",
                        role="user",
                        text=item.text,
                    )
                )
                displayed.add(item.message_id)

    if history:
        for index, message in enumerate(
            ModelMessagesTypeAdapter.validate_json(history)
        ):
            message_turn = 0
            prefix = f"{run_id}-turn-"
            if message.run_id and message.run_id.startswith(prefix):
                suffix = message.run_id[len(prefix) :]
                if suffix.isdigit():
                    message_turn = int(suffix)
                    for stopped in stopped_replies:
                        if stopped.turn < message_turn:
                            append_stopped(stopped)
                    append_messages(message_turn)
            if isinstance(message, ModelResponse):
                response_counts[message_turn] = response_counts.get(message_turn, 0) + 1
                stopped = next(
                    (
                        r
                        for r in stopped_replies
                        if r.turn == message_turn
                        and r.response_number == response_counts[message_turn]
                    ),
                    None,
                )
                if stopped is not None:
                    append_stopped(stopped)
                    continue
                if message.state == "interrupted":
                    result.append(
                        MessageView(
                            id=f"{run_id}:{index}:partial",
                            role="assistant",
                            text=public_response_text(message, partial=True),
                            status="interrupted",
                        )
                    )
                    continue
            for part_index, part in enumerate(message.parts):
                identity = f"{run_id}:{index}:{part_index}"
                if isinstance(part, ToolCallPart):
                    try:
                        arguments = part.args_as_dict()
                    except (ValueError, TypeError):
                        arguments = {"unparsed_arguments": part.args}
                    if part.tool_name.startswith("final_result"):
                        summary = arguments.get("summary")
                        if isinstance(summary, str):
                            result.append(
                                MessageView(id=identity, role="assistant", text=summary)
                            )
                        continue
                    pending[part.tool_call_id] = len(result)
                    result.append(
                        MessageView(
                            id=identity,
                            role="assistant",
                            kind="tool",
                            tool_name=part.tool_name,
                            tool_call_id=part.tool_call_id,
                            arguments=preview(arguments),
                            tool_summary=ToolSummary(target=_target(arguments)),
                            status="pending",
                        )
                    )
                elif isinstance(part, ToolReturnPart) and part.tool_call_id in pending:
                    pos = pending[part.tool_call_id]
                    failed = isinstance(part.content, dict) and (
                        part.content.get("succeeded") is False
                        or part.content.get("stage_succeeded") is False
                    )
                    result[pos] = result[pos].model_copy(
                        update={
                            "output": preview(part.content),
                            "status": "failed" if failed else "completed",
                            "tool_summary": _tool_summary(
                                result[pos].tool_summary, part.content
                            ),
                        }
                    )
                    if part.tool_name == "ask_user" and isinstance(part.content, str):
                        answered.add(part.tool_call_id)
                        result.append(
                            MessageView(
                                id=f"{run_id}:answer:{part.tool_call_id}",
                                role="user",
                                text=preview(part.content),
                            )
                        )
                elif isinstance(part, RetryPromptPart) and part.tool_call_id in pending:
                    pos = pending[part.tool_call_id]
                    result[pos] = result[pos].model_copy(
                        update={"status": "failed", "output": preview(part.content)}
                    )
                elif (
                    isinstance(message, ModelResponse)
                    and isinstance(part, TextPart)
                    and part.content
                ):
                    result.append(
                        MessageView(id=identity, role="assistant", text=part.content)
                    )
    if question:
        if question.question_id in pending:
            pos = pending[question.question_id]
            result[pos] = result[pos].model_copy(
                update={
                    "status": "waiting_for_user" if question.pending else "completed",
                    "output": question.answer,
                }
            )
        if question.answer is not None and question.question_id not in answered:
            result.append(
                MessageView(
                    id=f"{run_id}:answer:{question.question_id}",
                    role="user",
                    text=question.answer,
                )
            )
    for stopped in stopped_replies:
        append_stopped(stopped)
    append_messages()
    return tuple(result)


def latest_model_input_tokens(history: bytes | None) -> int | None:
    """返回最近一次真正记录到输入 token 的模型响应。"""
    if not history:
        return None
    for message in reversed(ModelMessagesTypeAdapter.validate_json(history)):
        if isinstance(message, ModelResponse) and message.usage.input_tokens > 0:
            return message.usage.input_tokens
    return None


def project_checkpoint(
    prepared: PreparedCodingRun,
    event: CodingProgress,
    previous: CodingRunView | None = None,
) -> CodingRunView:
    """只从已经发布的宿主bytes生成视图，不接触正在写入的文件。"""
    request = prepared.request
    stopped_replies = prepared.stopped_replies
    if event.stopped_reply_json:
        stopped = StoppedReply.model_validate_json(event.stopped_reply_json)
        stopped_replies = (
            *(r for r in stopped_replies if r.request_id != stopped.request_id),
            stopped,
        )
    if event.checkpoint_json is None:
        base = previous or CodingRunView(run_id=request.run_id, goal=request.goal)
        changes: dict[str, object] = {"phase": event.phase}
        if event.kind in {"model_started", "model_delta"} and event.reply_json:
            changes["live_reply"] = LiveReply.model_validate_json(event.reply_json)
        return base.model_copy(update=changes)
    state = CodingRunResumeState.model_validate_json(event.checkpoint_json)
    if state.run_id != request.run_id or state.question_protocol != "ask_user_v1":
        raise ValueError("checkpoint does not belong to the registered protocol/run")
    q = (
        UserQuestion.model_validate_json(event.question_json)
        if event.question_json
        else None
    )
    if q is not None and state.task_revision not in (
        q.task_revision,
        q.answer_revision,
    ):
        q = None
    question = (
        QuestionView(
            question_id=q.question_id,
            question=q.question,
            task_revision=state.task_revision,
            pending=q.pending,
            answer=q.answer,
        )
        if q
        else (
            previous.question
            if previous and previous.task_revision == state.task_revision
            else None
        )
    )
    if state.status == "waiting_for_user" and (
        question is None
        or question.question_id != state.pending_question_id
        or not question.pending
    ):
        raise ValueError("waiting checkpoint has no matching pending question")
    outcome = (
        CodingRunOutcome.model_validate_json(event.outcome_json)
        if event.outcome_json
        else None
    )
    if outcome is not None and (
        outcome.run_id != state.run_id or outcome.task_revision != state.task_revision
    ):
        outcome = None
    verification = (
        ExecutionEvidenceVerification.model_validate_json(event.verification_json)
        if event.verification_json
        else None
    )
    if outcome is not None and outcome.completion_kind == "response":
        verification = None
    baseline = state.revision_workspace_snapshot.by_path()
    files = tuple(
        FileView(
            id=hashlib.sha256(
                f"{state.task_revision}:{file.relative_path}:{file.sha256}".encode()
            ).hexdigest(),
            relative_path=file.relative_path,
            size_bytes=file.size_bytes,
            sha256=file.sha256,
            fresh=file.relative_path not in baseline
            or baseline[file.relative_path].sha256 != file.sha256,
        )
        for file in state.workspace_snapshot.files
    )
    usage = CodingRunUsage(
        model_requests=state.usage.requests,
        input_tokens=state.usage.input_tokens,
        output_tokens=state.usage.output_tokens,
        tool_calls=state.usage.tool_calls,
        abaqus_calls=state.dependencies.abaqus_calls,
        solver_calls=state.dependencies.solver_calls,
        wall_time_seconds=state.active_wall_time_seconds,
    )
    return CodingRunView(
        run_id=state.run_id,
        goal=request.goal,
        task_revision=state.task_revision,
        amendment_sequence=state.amendment_sequence,
        status=state.status,
        phase="stopped" if state.last_error_type == "UserStoppedError" else event.phase,
        question=question,
        outcome=outcome,
        verification=verification,
        usage=outcome.usage if outcome else usage,
        messages=message_views(
            state.run_id,
            prepared.initial_goal or request.goal,
            event.history_json,
            question,
            prepared.conversation_messages,
            stopped_replies,
        )
        if event.history_json
        else (
            previous.messages
            if previous
            else message_views(
                state.run_id,
                prepared.initial_goal or request.goal,
                None,
                question,
                prepared.conversation_messages,
                stopped_replies,
            )
        ),
        files=files,
        latest_model_input_tokens=(
            latest_model_input_tokens(event.history_json)
            if event.history_json
            else (previous.latest_model_input_tokens if previous else None)
        ),
    )


def read_coding_view(
    prepared: PreparedCodingRun, lock: CodingRunFileLock
) -> CodingRunView:
    """调用者持锁时读取完整记录；不恢复工具、不补写问题或状态。"""
    state_dir = prepared.request.state_dir
    lock.assert_held(state_dir)
    raw = record_bytes(state_dir / "resume_state.json")
    if raw is None:
        if (state_dir / "request.json").exists():
            raise ValueError("run initialization is incomplete")
        return CodingRunView(
            run_id=prepared.request.run_id,
            goal=prepared.request.goal,
            messages=message_views(
                prepared.request.run_id, prepared.request.goal, None, None
            ),
        )
    state = CodingRunResumeState.model_validate_json(raw)
    question = None
    ledger_raw = record_bytes(state_dir / "user_questions.json")
    if ledger_raw:
        ledger = QuestionLedger.model_validate_json(ledger_raw)
        if ledger.run_id != state.run_id:
            raise ValueError("question ledger belongs to another run")
        if ledger.questions:
            q = ledger.questions[-1]
            question_history(state_dir, q)
            question = q.model_dump_json().encode()
    verification = None
    events = record_bytes(state_dir / "events.jsonl")
    if events:
        for line in events.splitlines():
            data = json.loads(line)
            if (
                data.get("event") == "verification"
                and data.get("task_revision", 0) == state.task_revision
            ):
                verification = json.dumps(data["data"]).encode()
    view = project_checkpoint(
        prepared,
        CodingProgress(
            "checkpoint",
            state.status,
            state.turn,
            state.task_revision,
            checkpoint_json=raw,
            history_json=record_bytes(state_dir / "history.json"),
            question_json=question,
            outcome_json=record_bytes(state_dir / "outcome.json"),
            verification_json=verification,
        ),
    )
    if view.status in {"completed", "failed"}:
        if view.outcome is None or view.outcome.status != view.status:
            raise ValueError("terminal checkpoint has no matching outcome")
        if (
            view.status == "completed"
            and view.outcome.completion_kind != "response"
            and (view.verification is None or not view.verification.passed)
        ):
            raise ValueError("completed checkpoint has no passing verification")
    if (
        prepared.amendment_sequence is not None
        and prepared.amendment_sequence != state.amendment_sequence
    ):
        # A new task revision is authoritative only when the host resumes and captures its checkpoint.
        view = view.model_copy(
            update={
                "status": "interrupted",
                "phase": "revision_pending",
                "outcome": None,
                "verification": None,
                "files": (),
                "question": None,
                "amendment_sequence": prepared.amendment_sequence,
                "task_revision": prepared.task_revision
                if prepared.task_revision is not None
                else state.task_revision,
            }
        )
    return view
