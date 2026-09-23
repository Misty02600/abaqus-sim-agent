from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..coding.contracts import (
    CodingRunLimits,
    CodingRunRequest,
    CodingRunUsage,
    RuntimeExecutionPolicy,
    RuntimePermission,
)
from ..coding.domain_knowledge import DomainKnowledgePackBinding
from ..coding.launch import LaunchManifest
from ..coding.read_model import CodingRunView, FileView, MessageView
from ..coding.workspace import WorkspaceSnapshot


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class CreateRun(StrictModel):
    idempotency_key: UUID
    goal: str = Field(min_length=1, max_length=20000)
    knowledge_pack_ids: tuple[str, ...] = Field(default=(), max_length=32)
    abaqus_permission: RuntimePermission | None = None
    solver_permission: RuntimePermission | None = None
    workspace: str | None = Field(default=None, min_length=1, max_length=4096)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def parse_uuid(cls, value: object) -> object:
        return UUID(value) if isinstance(value, str) else value

    @field_validator("goal")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("任务目标不能为空")
        return value

    @field_validator("knowledge_pack_ids", mode="before")
    @classmethod
    def valid_knowledge_pack_ids(cls, values: object) -> tuple[str, ...]:
        if isinstance(values, list):
            values = tuple(values)
        if not isinstance(values, tuple) or not all(
            isinstance(value, str) for value in values
        ):
            raise ValueError("知识包标识必须是字符串列表")
        if len(values) != len(set(values)):
            raise ValueError("知识包不能重复选择")
        if any(
            re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value) is None for value in values
        ):
            raise ValueError("知识包标识格式不正确")
        return values


class RunCommand(StrictModel):
    command_id: UUID

    @field_validator("command_id", mode="before")
    @classmethod
    def parse_uuid(cls, value: object) -> object:
        return UUID(value) if isinstance(value, str) else value


class AnswerCommand(RunCommand):
    question_id: str = Field(min_length=1, max_length=512)
    expected_task_revision: int = Field(ge=0)
    answer: str = Field(min_length=1, max_length=20000)

    @field_validator("answer")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("答复不能为空")
        return value


class MessageCommand(RunCommand):
    text: str = Field(min_length=1, max_length=20000)
    expected_task_revision: int = Field(ge=0)
    expected_amendment_sequence: int = Field(ge=0)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("消息不能为空")
        return value


class StopCommand(RunCommand):
    expected_operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class RenameCommand(RunCommand):
    title: str = Field(min_length=1, max_length=120)
    expected_title_version: int = Field(ge=0)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class RuntimePolicyCommand(RunCommand):
    expected_task_revision: int = Field(ge=0)
    expected_amendment_sequence: int = Field(ge=0)
    abaqus: RuntimePermission
    solver: RuntimePermission


class DeleteRunResponse(StrictModel):
    run_id: str
    deleted: Literal[True] = True
    workspace: str


class DisplayMetadata(StrictModel):
    title: str
    version: int = 0
    receipts: dict[str, str] = Field(default_factory=dict)


class ThreadMessage(StrictModel):
    message_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    sequence: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=20000)
    created_at: datetime
    submitted_after_turn: int = Field(ge=0)
    base_task_revision: int = Field(ge=0)
    base_amendment_sequence: int = Field(ge=0)
    execution_task_revision: int | None = Field(default=None, ge=0)
    execution_amendment_sequence: int | None = Field(default=None, ge=0)
    execution_continuation_id: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{32}$"
    )
    execution_invocation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @model_validator(mode="after")
    def execution_link_is_complete(self) -> "ThreadMessage":
        if (self.execution_task_revision is None) != (
            self.execution_amendment_sequence is None
        ):
            raise ValueError(
                "execution link must include revision and amendment sequence"
            )
        if (
            self.execution_continuation_id is not None
            and self.execution_task_revision is None
        ):
            raise ValueError("continuation link requires an execution link")
        if (
            self.execution_invocation_id is not None
            and self.execution_task_revision is None
        ):
            raise ValueError("invocation link requires an execution link")
        return self


InvocationStatus = Literal[
    "created",
    "running",
    "waiting_for_user",
    "waiting_for_tool_execution",
    "interrupted",
    "needs_reconcile",
    "completed",
    "failed",
    "stopped",
]


class AgentInvocation(StrictModel):
    """一次 Thread 请求对应的 Agent 执行记录；不拥有第二份对话历史。"""

    schema_version: Literal["web-agent-invocation-v2"] = "web-agent-invocation-v2"
    invocation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str
    source_message_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    source_message_sequence: int | None = Field(default=None, ge=1)
    origin_action: Literal["start", "message", "answer", "continue"]
    origin_command_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: InvocationStatus = "created"
    base_task_revision: int = Field(ge=0)
    base_amendment_sequence: int = Field(ge=0)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    limits: CodingRunLimits
    runtime_policy: RuntimeExecutionPolicy
    step_persistence_reference: str = Field(min_length=1)
    compatibility_continuation_id: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{32}$"
    )
    execution_task_revision: int | None = Field(default=None, ge=0)
    execution_amendment_sequence: int | None = Field(default=None, ge=0)
    usage_before: CodingRunUsage = CodingRunUsage()
    usage_after: CodingRunUsage | None = None
    resume_count: int = Field(default=0, ge=0)
    last_error: str | None = None

    @model_validator(mode="after")
    def check_links(self) -> "AgentInvocation":
        if (self.source_message_id is None) != (self.source_message_sequence is None):
            raise ValueError("source message id and sequence must be present together")
        if (self.execution_task_revision is None) != (
            self.execution_amendment_sequence is None
        ):
            raise ValueError("execution revision link must be complete")
        if self.finished_at is not None and self.status not in {
            "completed",
            "failed",
            "stopped",
        }:
            raise ValueError("only terminal invocations may have finished_at")
        return self


ToolExecutionStatus = Literal[
    "starting",
    "running",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
    "needs_reconcile",
]


class ToolExecution(StrictModel):
    """宿主拥有的长时工具执行记录；Agent 对话与执行证据仍由各自原记录负责。"""

    schema_version: Literal["web-tool-execution-v2"] = "web-tool-execution-v2"
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    thread_id: str
    invocation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    tool_name: Literal["run_solver_job", "solver_job"] = "run_solver_job"
    tool_call_id: str | None = None
    job_name: str = Field(min_length=1, max_length=80)
    input_file: str = Field(min_length=1)
    timeout_seconds: float | None = Field(default=None, gt=0)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: ToolExecutionStatus = "starting"
    process_id: int | None = Field(default=None, ge=1)
    elapsed_seconds: float = Field(default=0.0, ge=0)
    cancel_requested: bool = False
    exit_code: int | None = None
    result_succeeded: bool | None = None
    result_stage_succeeded: bool | None = None
    result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    stdout_raw_path: str | None = None
    stderr_raw_path: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def check_lifecycle(self) -> "ToolExecution":
        terminal = {"completed", "failed", "cancelled", "timed_out"}
        if self.finished_at is not None and self.status not in terminal:
            raise ValueError("only terminal tool executions may have finished_at")
        if self.status in terminal and self.finished_at is None:
            raise ValueError("terminal tool executions require finished_at")
        if self.started_at is None and self.status not in {
            "starting",
            "needs_reconcile",
        }:
            raise ValueError("started tool execution requires started_at")
        return self


class ToolExecutionList(StrictModel):
    items: tuple[ToolExecution, ...] = ()


class ToolExecutionCancelCommand(RunCommand):
    pass


class ThreadContextCheckpoint(StrictModel):
    through_message_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    through_sequence: int = Field(ge=1)
    summary: str = Field(min_length=1)
    created_at: datetime
    source_history_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ThreadRecord(StrictModel):
    schema_version: Literal["web-thread-v1"] = "web-thread-v1"
    run_id: str
    created_at: datetime
    updated_at: datetime
    messages: tuple[ThreadMessage, ...] = ()
    context_checkpoint: ThreadContextCheckpoint | None = None

    @model_validator(mode="after")
    def ordered_unique_messages(self) -> "ThreadRecord":
        if [m.sequence for m in self.messages] != list(
            range(1, len(self.messages) + 1)
        ):
            raise ValueError("thread message sequence must be contiguous")
        ids = [m.message_id for m in self.messages]
        if len(ids) != len(set(ids)):
            raise ValueError("thread message ids must be unique")
        checkpoint = self.context_checkpoint
        if checkpoint is not None:
            target = next(
                (
                    message
                    for message in self.messages
                    if message.message_id == checkpoint.through_message_id
                ),
                None,
            )
            if target is None or target.sequence != checkpoint.through_sequence:
                raise ValueError(
                    "thread context checkpoint target must match a message"
                )
        return self


class Registration(StrictModel):
    schema_version: Literal["web-registration-v3"] = "web-registration-v3"
    workspace_kind: Literal["managed", "selected"]
    request_digest: str
    created_at: datetime
    request: CodingRunRequest
    initial_snapshot: WorkspaceSnapshot
    launch: LaunchManifest


class CommandReceipt(StrictModel):
    schema_version: Literal["web-command-v1"] = "web-command-v1"
    command_id: UUID
    digest: str
    action: Literal["start", "answer", "continue", "message", "stop", "runtime_policy"]
    state: Literal["accepted", "settled", "rejected"]
    answer_saved: bool = False
    message_saved: bool = False
    message_revision: int | None = None
    error: str | None = None


class ContextUsage(StrictModel):
    latest_input_tokens: int | None = Field(default=None, ge=0)
    warning_threshold_tokens: int | None = Field(default=None, ge=1)
    warning: bool = False


class RunSnapshot(StrictModel):
    view: CodingRunView
    version: str
    created_at: datetime
    model: str
    workspace: str
    title: str
    title_version: int = 0
    reasoning_policy: str
    limits: CodingRunLimits
    runtime_policy: RuntimeExecutionPolicy
    knowledge_packs: tuple[DomainKnowledgePackBinding, ...] = ()
    can_start: bool
    can_answer: bool
    can_continue: bool
    can_message: bool = False
    message_block_reason: str | None = None
    command_uncertain: bool = False
    active: bool = False
    active_operation_id: str | None = None
    model_request_id: str | None = None
    can_stop: bool = False
    stop_requested: bool = False
    tool_execution: ToolExecution | None = None
    can_cancel_tool_execution: bool = False
    can_update_runtime_policy: bool = False
    runtime_policy_block_reason: str | None = None
    context_usage: ContextUsage = ContextUsage()


class CommandResponse(StrictModel):
    command_id: UUID
    execution_accepted: bool
    answer_saved: bool
    message_saved: bool = False
    replayed: bool = False
    error: str | None = None
    snapshot: RunSnapshot


class RunIssue(StrictModel):
    run_id: str
    message: str
    can_retry_delete: bool = False


class RunList(StrictModel):
    runs: tuple[RunSnapshot, ...]
    unavailable: tuple[RunIssue, ...] = ()


class ViewPage(StrictModel):
    run_id: str
    version: str
    task_revision: int
    offset: int
    total: int
    next_offset: int | None


class TimelinePage(ViewPage):
    items: tuple[MessageView, ...]


class ArtifactPage(ViewPage):
    items: tuple[FileView, ...]


class WorkspaceEntry(StrictModel):
    relative_path: str
    name: str
    kind: Literal["file", "directory"]
    size_bytes: int | None = None


class WorkspaceDirectory(StrictModel):
    run_id: str
    path: str
    items: tuple[WorkspaceEntry, ...]
    truncated: bool = False


class DirectoryChoice(StrictModel):
    name: str
    path: str


class DirectoryBrowse(StrictModel):
    path: str | None = None
    parent_path: str | None = None
    directories: tuple[DirectoryChoice, ...] = ()
    drives: tuple[DirectoryChoice, ...] | None = None
    selectable: bool = False
    selection_error: str | None = None
    truncated: bool = False


class WorkspaceSearch(StrictModel):
    run_id: str
    query: str
    items: tuple[WorkspaceEntry, ...]
    truncated: bool = False


class SessionInput(StrictModel):
    token: str = Field(min_length=1, max_length=256)


class SessionInfo(StrictModel):
    csrf_token: str


class ApiError(StrictModel):
    code: str
    message: str


class WebError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)
