from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from ..coding.contracts import CodingRunRequest
from ..coding.launch import LaunchManifest
from ..coding.persistence import write_text_atomic
from ..coding.workspace import WorkspaceSnapshot
from ..execution import AbaqusExecutionResult
from .contracts import (
    AgentInvocation,
    Registration,
    ThreadContextCheckpoint,
    ThreadMessage,
    ThreadRecord,
    ToolExecution,
    WebError,
)

RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def plain_path(path: Path, *, directory: bool | None = None) -> Path:
    """拒绝已有路径及祖先中的链接/junction；不创建文件。"""
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise WebError("unsafe_path", "路径中不允许符号链接或目录联接", 400)
    if path.exists() and directory is not None and path.is_dir() != directory:
        raise WebError("unsafe_path", "路径类型不正确", 400)
    return path


def selected_workspace(value: str | Path, managed_root: Path) -> Path:
    """校验用户选择的现有工程目录，保持整个宿主管理根与工程互不包含。"""
    path = local_directory(value)
    if not str(path).isascii():
        raise WebError(
            "ascii_workspace", "工作区路径须只包含 ASCII 字符，请选择英文路径。", 400
        )
    root = managed_root.resolve()
    if path == root or path.is_relative_to(root) or root.is_relative_to(path):
        raise WebError(
            "workspace_overlap", "工作区不能包含宿主管理目录，也不能位于其中。", 400
        )
    if not os.access(path, os.W_OK):
        raise WebError("workspace_readonly", "所选目录不可写。", 400)
    return path


def local_directory(value: str | Path) -> Path:
    raw = str(value)
    path = Path(raw)
    if (
        not path.is_absolute()
        or raw.startswith(("\\\\", "//"))
        or "\x00" in raw
        or any(":" in part for part in path.parts[1:])
    ):
        raise WebError("invalid_directory", "请选择本机绝对目录路径。", 400)
    path = plain_path(path, directory=True)
    try:
        if not path.exists():
            raise WebError("directory_not_found", "目录不存在。", 404)
        return path.resolve(strict=True)
    except OSError as exc:
        raise WebError("directory_unavailable", "无法访问这个目录。", 400) from exc


def digest(model: BaseModel) -> str:
    return hashlib.sha256(model.model_dump_json().encode()).hexdigest()


def save_model(path: Path, model: BaseModel) -> None:
    plain_path(path, directory=False)
    write_text_atomic(path, model.model_dump_json(indent=2))


class RunRegistry:
    def __init__(self, root: Path, *, state_root: Path | None = None):
        self.root = plain_path(root, directory=True).resolve()
        self.state_root = plain_path(
            state_root or self.root / "states", directory=True
        ).resolve()
        if not str(self.root).isascii():
            raise WebError("ascii_root", "Web工作目录必须是ASCII路径", 400)

    def initialize(self) -> None:
        for name in ("registry", "cache", "control"):
            plain_path(self.root / name, directory=True).mkdir(
                parents=True, exist_ok=True
            )
        plain_path(self.state_root, directory=True).mkdir(parents=True, exist_ok=True)

    def directory(self, run_id: str) -> Path:
        if RUN_ID.fullmatch(run_id) is None:
            raise WebError("not_found", "任务不存在", 404)
        return plain_path(self.root / "registry" / run_id, directory=True)

    def state_dir(self, run_id: str) -> Path:
        self.directory(run_id)
        current = plain_path(self.state_root / run_id, directory=True)
        previous = plain_path(self.root / "states" / run_id, directory=True)
        if previous != current and previous.exists():
            if current.exists():
                raise WebError(
                    "state_conflict", "同一会话标识存在两份状态，不能选择恢复目标"
                )
            return previous
        return current

    def load(self, run_id: str) -> Registration:
        if self.deletion_pending(run_id):
            raise WebError("run_deleted", "会话已进入永久删除流程。", 410)
        file = plain_path(self.directory(run_id) / "registration.json", directory=False)
        if not file.exists():
            data = self._stored_run(run_id)
        else:
            data = Registration.model_validate_json(file.read_bytes())
        request = data.request
        if request.run_id != run_id or request.state_dir != self.state_dir(run_id):
            raise WebError("registration_corrupt", "任务路径与登记不一致")
        if data.workspace_kind == "managed":
            if request.workspace != self.root / "workspaces" / run_id:
                raise WebError("registration_corrupt", "托管工作区路径与登记不一致")
        elif selected_workspace(request.workspace, self.root) != request.workspace:
            raise WebError("registration_corrupt", "所选工作区路径与登记不一致")
        if data.workspace_kind == "selected":
            selected_workspace(request.workspace, self.state_root)
        plain_path(request.workspace, directory=True)
        plain_path(request.state_dir, directory=True)
        return data

    def _stored_run(self, run_id: str) -> Registration:
        """从普通 CLI 的原始运行文件读取入口信息，不补写 Web 登记或启动运行。"""
        state = self.state_dir(run_id)
        request_path = plain_path(state / "request.json", directory=False)
        launch_path = plain_path(state / "launch.json", directory=False)
        if not request_path.is_file() or not launch_path.is_file():
            raise WebError("not_found", "会话不存在或初始化尚未完成", 404)
        request = CodingRunRequest.model_validate_json(request_path.read_bytes())
        launch = LaunchManifest.model_validate_json(launch_path.read_bytes())
        if (
            launch.schema_version != "coding-run-launch-v11"
            or launch.question_protocol != "ask_user_v1"
        ):
            raise WebError("profile_mismatch", "仅支持当前格式的普通 CLI/Web 会话")
        initial = WorkspaceSnapshot.model_validate_json(
            plain_path(state / "initial_snapshot.json", directory=False).read_bytes()
        )
        return Registration(
            workspace_kind="selected",
            request_digest=digest(request),
            created_at=datetime.fromtimestamp(request_path.stat().st_mtime, UTC),
            request=request,
            initial_snapshot=initial,
            launch=launch,
        )

    def ids(self) -> list[str]:
        ids = {
            p.name
            for p in (self.root / "registry").iterdir()
            if RUN_ID.fullmatch(p.name) and not self.deletion_pending(p.name)
        }
        ids.update(
            p.name
            for p in self.state_root.iterdir()
            if RUN_ID.fullmatch(p.name)
            and p.is_dir()
            and (p / "request.json").is_file()
            and (p / "launch.json").is_file()
            and not self.deletion_pending(p.name)
        )
        return sorted(ids)

    def deletion_pending(self, run_id: str) -> bool:
        self.directory(run_id)
        return plain_path(
            self.root / "control" / "deletions" / f"{run_id}.json", directory=False
        ).exists()


class ThreadStore:
    """Web Thread 的文件真相；CodingRun 只在实际执行时关联对应消息。"""

    def __init__(self, registry: RunRegistry):
        self.registry = registry

    def _path(self, run_id: str) -> Path:
        return plain_path(
            self.registry.directory(run_id) / "thread.json", directory=False
        )

    def initialize(self, run_id: str, *, created_at: datetime) -> ThreadRecord:
        path = self._path(run_id)
        if path.exists():
            record = ThreadRecord.model_validate_json(path.read_bytes())
            if record.run_id != run_id:
                raise WebError("thread_corrupt", "会话记录与任务不一致")
            return record
        record = ThreadRecord(
            run_id=run_id,
            created_at=created_at,
            updated_at=created_at,
        )
        save_model(path, record)
        return record

    def load(self, run_id: str) -> ThreadRecord | None:
        path = self._path(run_id)
        if not path.exists():
            return None
        record = ThreadRecord.model_validate_json(path.read_bytes())
        if record.run_id != run_id:
            raise WebError("thread_corrupt", "会话记录与任务不一致")
        return record

    def append_message(
        self,
        run_id: str,
        *,
        message_id: str,
        text: str,
        submitted_after_turn: int,
        base_task_revision: int,
        base_amendment_sequence: int,
        created_at: datetime | None = None,
    ) -> ThreadMessage:
        record = self.load(run_id)
        if record is None:
            registration = self.registry.load(run_id)
            record = self.initialize(run_id, created_at=registration.created_at)
        existing = next(
            (m for m in record.messages if m.message_id == message_id), None
        )
        if existing is not None:
            if (
                existing.text != text
                or existing.base_task_revision != base_task_revision
                or existing.base_amendment_sequence != base_amendment_sequence
            ):
                raise WebError("command_conflict", "相同消息标识不能提交不同内容")
            return existing
        now = created_at or datetime.now(UTC)
        message = ThreadMessage(
            message_id=message_id,
            sequence=len(record.messages) + 1,
            text=text,
            created_at=now,
            submitted_after_turn=submitted_after_turn,
            base_task_revision=base_task_revision,
            base_amendment_sequence=base_amendment_sequence,
        )
        save_model(
            self._path(run_id),
            record.model_copy(
                update={"updated_at": now, "messages": (*record.messages, message)}
            ),
        )
        return message

    def mark_execution(
        self,
        run_id: str,
        message_id: str,
        *,
        task_revision: int,
        amendment_sequence: int,
        continuation_id: str | None = None,
        invocation_id: str | None = None,
    ) -> ThreadMessage:
        record = self.load(run_id)
        if record is None:
            raise WebError("thread_missing", "会话记录不存在")
        found = next(
            (
                index
                for index, item in enumerate(record.messages)
                if item.message_id == message_id
            ),
            None,
        )
        if found is None:
            raise WebError("message_missing", "待执行消息不存在")
        current = record.messages[found]
        if current.execution_task_revision is not None:
            if (
                current.execution_task_revision != task_revision
                or current.execution_amendment_sequence != amendment_sequence
                or current.execution_continuation_id != continuation_id
                or current.execution_invocation_id != invocation_id
            ):
                raise WebError("message_execution_conflict", "消息已关联到其他执行段")
            return current
        linked = current.model_copy(
            update={
                "execution_task_revision": task_revision,
                "execution_amendment_sequence": amendment_sequence,
                "execution_continuation_id": continuation_id,
                "execution_invocation_id": invocation_id,
            }
        )
        messages = list(record.messages)
        messages[found] = linked
        now = datetime.now(UTC)
        save_model(
            self._path(run_id),
            record.model_copy(update={"updated_at": now, "messages": tuple(messages)}),
        )
        return linked

    def pending(self, run_id: str) -> ThreadMessage | None:
        record = self.load(run_id)
        if record is None:
            return None
        return next(
            (m for m in record.messages if m.execution_task_revision is None),
            None,
        )

    def set_context_checkpoint(
        self, run_id: str, checkpoint: ThreadContextCheckpoint
    ) -> ThreadContextCheckpoint:
        """保存派生的会话摘要锚点；原始消息始终保留。"""
        record = self.load(run_id)
        if record is None:
            raise WebError("thread_missing", "会话记录不存在")
        if record.context_checkpoint == checkpoint:
            return checkpoint
        if (
            record.context_checkpoint is not None
            and checkpoint.through_sequence < record.context_checkpoint.through_sequence
        ):
            raise WebError("context_checkpoint_regression", "上下文检查点不能向前回退")
        now = datetime.now(UTC)
        data = record.model_dump(mode="python")
        data.update({"updated_at": now, "context_checkpoint": checkpoint})
        try:
            updated = ThreadRecord.model_validate(data)
        except ValueError as exc:
            raise WebError(
                "context_checkpoint_invalid", "上下文检查点与会话消息不一致"
            ) from exc
        save_model(self._path(run_id), updated)
        return checkpoint


class InvocationStore:
    """Web AgentInvocation 的文件真相；不复制 Thread 文本或 Harness history。"""

    _RESUMABLE = frozenset(
        {
            "created",
            "running",
            "waiting_for_user",
            "waiting_for_tool_execution",
            "interrupted",
            "needs_reconcile",
        }
    )

    def __init__(self, registry: RunRegistry):
        self.registry = registry

    def _directory(self, run_id: str) -> Path:
        directory = plain_path(
            self.registry.directory(run_id) / "invocations", directory=True
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _path(self, run_id: str, invocation_id: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{32}", invocation_id) is None:
            raise WebError("invocation_not_found", "执行记录不存在", 404)
        return plain_path(
            self._directory(run_id) / f"{invocation_id}.json", directory=False
        )

    def create(self, invocation: AgentInvocation) -> AgentInvocation:
        self.registry.load(invocation.thread_id)
        path = self._path(invocation.thread_id, invocation.invocation_id)
        if path.exists():
            existing = AgentInvocation.model_validate_json(path.read_bytes())
            if existing != invocation:
                raise WebError("invocation_conflict", "执行标识已用于其他记录")
            return existing
        save_model(path, invocation)
        return invocation

    def load(self, run_id: str, invocation_id: str) -> AgentInvocation:
        path = self._path(run_id, invocation_id)
        if not path.exists():
            raise WebError("invocation_not_found", "执行记录不存在", 404)
        invocation = AgentInvocation.model_validate_json(path.read_bytes())
        if invocation.thread_id != run_id or invocation.invocation_id != invocation_id:
            raise WebError("invocation_corrupt", "执行记录与会话不一致")
        return invocation

    def list(self, run_id: str) -> tuple[AgentInvocation, ...]:
        directory = self._directory(run_id)
        invocations = []
        for path in directory.glob("*.json"):
            invocation = AgentInvocation.model_validate_json(
                plain_path(path, directory=False).read_bytes()
            )
            if invocation.thread_id != run_id or path.stem != invocation.invocation_id:
                raise WebError("invocation_corrupt", "执行记录与会话不一致")
            invocations.append(invocation)
        return tuple(sorted(invocations, key=lambda item: item.created_at))

    def latest_for_message(
        self, run_id: str, message_id: str
    ) -> AgentInvocation | None:
        return next(
            (
                item
                for item in reversed(self.list(run_id))
                if item.source_message_id == message_id
            ),
            None,
        )

    def latest_resumable(self, run_id: str) -> AgentInvocation | None:
        return next(
            (
                item
                for item in reversed(self.list(run_id))
                if item.status in self._RESUMABLE
            ),
            None,
        )

    def update(self, invocation: AgentInvocation, **changes: object) -> AgentInvocation:
        current = self.load(invocation.thread_id, invocation.invocation_id)
        if current != invocation:
            raise WebError("invocation_conflict", "执行记录已改变，请刷新后重试")
        changes.setdefault("updated_at", datetime.now(UTC))
        updated = current.model_copy(update=changes)
        updated = AgentInvocation.model_validate(updated.model_dump(mode="python"))
        save_model(self._path(updated.thread_id, updated.invocation_id), updated)
        return updated


class ToolExecutionStore:
    """Web ToolExecution 的文件真相；目前只记录宿主控制的 Solver 长任务。"""

    _UNRESOLVED = frozenset({"starting", "running"})

    def __init__(self, registry: RunRegistry):
        self.registry = registry

    def _directory(self, run_id: str) -> Path:
        directory = plain_path(
            self.registry.directory(run_id) / "tool-executions", directory=True
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _path(self, run_id: str, execution_id: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{32}", execution_id) is None:
            raise WebError("tool_execution_not_found", "工具执行记录不存在", 404)
        return plain_path(
            self._directory(run_id) / f"{execution_id}.json", directory=False
        )

    def _result_directory(self, run_id: str) -> Path:
        directory = plain_path(
            self.registry.directory(run_id) / "tool-execution-results", directory=True
        )
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _result_path(self, run_id: str, execution_id: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{32}", execution_id) is None:
            raise WebError("tool_execution_not_found", "工具执行记录不存在", 404)
        return plain_path(
            self._result_directory(run_id) / f"{execution_id}.json", directory=False
        )

    def _workspace_path(self, run_id: str, execution_id: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{32}", execution_id) is None:
            raise WebError("tool_execution_not_found", "工具执行记录不存在", 404)
        return plain_path(
            self._result_directory(run_id) / f"{execution_id}.workspace.json",
            directory=False,
        )

    def create(self, execution: ToolExecution) -> ToolExecution:
        self.registry.load(execution.thread_id)
        path = self._path(execution.thread_id, execution.execution_id)
        if path.exists():
            existing = ToolExecution.model_validate_json(path.read_bytes())
            if existing != execution:
                raise WebError("tool_execution_conflict", "工具执行标识已用于其他记录")
            return existing
        save_model(path, execution)
        return execution

    def load(self, run_id: str, execution_id: str) -> ToolExecution:
        path = self._path(run_id, execution_id)
        if not path.exists():
            raise WebError("tool_execution_not_found", "工具执行记录不存在", 404)
        execution = ToolExecution.model_validate_json(path.read_bytes())
        if execution.thread_id != run_id or execution.execution_id != execution_id:
            raise WebError("tool_execution_corrupt", "工具执行记录与会话不一致")
        return execution

    def list(self, run_id: str) -> tuple[ToolExecution, ...]:
        directory = plain_path(
            self.registry.directory(run_id) / "tool-executions", directory=True
        )
        executions = []
        for path in directory.glob("*.json"):
            execution = ToolExecution.model_validate_json(
                plain_path(path, directory=False).read_bytes()
            )
            if execution.thread_id != run_id or path.stem != execution.execution_id:
                raise WebError("tool_execution_corrupt", "工具执行记录与会话不一致")
            executions.append(execution)
        return tuple(sorted(executions, key=lambda item: item.created_at))

    def latest(self, run_id: str) -> ToolExecution | None:
        executions = self.list(run_id)
        return executions[-1] if executions else None

    def find_for_call(
        self,
        run_id: str,
        invocation_id: str,
        tool_call_id: str,
    ) -> ToolExecution | None:
        matches = tuple(
            execution
            for execution in self.list(run_id)
            if execution.invocation_id == invocation_id
            and execution.tool_call_id == tool_call_id
        )
        if len(matches) > 1:
            raise WebError(
                "tool_execution_corrupt",
                "同一 Agent 工具调用存在多个 Solver 执行记录。",
            )
        return matches[0] if matches else None

    def save_result(
        self,
        execution: ToolExecution,
        result: AbaqusExecutionResult,
    ) -> None:
        current = self.load(execution.thread_id, execution.execution_id)
        if current.execution_id != execution.execution_id:
            raise WebError(
                "tool_execution_conflict", "工具执行记录已改变，请刷新后重试"
            )
        write_text_atomic(
            self._result_path(execution.thread_id, execution.execution_id),
            result.model_dump_json(indent=2),
        )

    def load_result(
        self, run_id: str, execution_id: str
    ) -> AbaqusExecutionResult | None:
        self.load(run_id, execution_id)
        path = self._result_path(run_id, execution_id)
        if not path.is_file():
            return None
        try:
            return AbaqusExecutionResult.model_validate_json(path.read_bytes())
        except ValueError as exc:
            raise WebError(
                "tool_execution_corrupt",
                f"Solver 结果记录无法读取：{exc}",
            ) from exc

    def save_workspace_snapshot(
        self, execution: ToolExecution, snapshot: WorkspaceSnapshot
    ) -> None:
        self.load(execution.thread_id, execution.execution_id)
        write_text_atomic(
            self._workspace_path(execution.thread_id, execution.execution_id),
            snapshot.model_dump_json(indent=2),
        )

    def load_workspace_snapshot(
        self, run_id: str, execution_id: str
    ) -> WorkspaceSnapshot | None:
        self.load(run_id, execution_id)
        path = self._workspace_path(run_id, execution_id)
        if not path.is_file():
            return None
        try:
            return WorkspaceSnapshot.model_validate_json(path.read_bytes())
        except ValueError as exc:
            raise WebError(
                "tool_execution_corrupt",
                f"Solver 工作区终态无法读取：{exc}",
            ) from exc

    def update(self, execution: ToolExecution, **changes: object) -> ToolExecution:
        current = self.load(execution.thread_id, execution.execution_id)
        if current != execution:
            raise WebError(
                "tool_execution_conflict", "工具执行记录已改变，请刷新后重试"
            )
        changes.setdefault("updated_at", datetime.now(UTC))
        updated = current.model_copy(update=changes)
        updated = ToolExecution.model_validate(updated.model_dump(mode="python"))
        save_model(self._path(updated.thread_id, updated.execution_id), updated)
        return updated

    def mark_interrupted_as_needs_reconcile(self) -> tuple[ToolExecution, ...]:
        """服务重启后不猜测仍在运行的外部副作用，把旧活动记录转为待核查。"""
        changed: list[ToolExecution] = []
        now = datetime.now(UTC)
        for run_id in self.registry.ids():
            for execution in self.list(run_id):
                if execution.status not in self._UNRESOLVED:
                    continue
                updated = self.update(
                    execution,
                    status="needs_reconcile",
                    error="宿主在工具执行期间重启；外部 Solver 状态需要核查。",
                    elapsed_seconds=max(
                        execution.elapsed_seconds,
                        (
                            now - (execution.started_at or execution.created_at)
                        ).total_seconds(),
                    ),
                )
                changed.append(updated)
        return tuple(changed)
