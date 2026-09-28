from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from abaqus_sim_agent.coding.contracts import (
    CodingRunOutcome,
    CodingRunRequest,
    DurationLimit,
    RuntimeExecutionPolicy,
    RuntimePermission,
)
from abaqus_sim_agent.coding.model import DeepSeekGateway
from abaqus_sim_agent.coding.persistence import (
    write_bytes_atomic as _write_bytes_atomic,
)
from abaqus_sim_agent.coding.persistence import (
    write_text_atomic as _write_text_atomic,
)
from abaqus_sim_agent.coding.run_lock import CodingRunFileLock
from abaqus_sim_agent.coding.workspace import WorkspaceResolver, WorkspaceSnapshot

AMENDMENT_LEDGER_NAME = "amendments.json"
AMENDMENT_SCHEMA_VERSION = "coding-run-amendments-v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RuntimeExecutionPolicyPatch(_StrictModel):
    abaqus: RuntimePermission | None = None
    solver: RuntimePermission | None = None
    cae_timeout: DurationLimit | None = None
    abaqus_python_timeout: DurationLimit | None = None
    solver_timeout: DurationLimit | None = None

    @model_validator(mode="after")
    def require_value(self) -> "RuntimeExecutionPolicyPatch":
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("explicit null is not a supported runtime policy patch")
        if not self.model_dump(exclude_none=True):
            raise ValueError("runtime policy patch must set at least one field")
        return self

    def apply(self, policy: RuntimeExecutionPolicy) -> RuntimeExecutionPolicy:
        values = policy.model_dump(mode="python")
        values.update(self.model_dump(mode="python", exclude_none=True))
        return RuntimeExecutionPolicy.model_validate(values)


class CodingRunConfigPatch(_StrictModel):
    runtime_policy: RuntimeExecutionPolicyPatch | None = None
    model_provider: DeepSeekGateway | None = None
    model_name: str | None = Field(default=None, min_length=1)
    context_window: int | None = Field(default=None, ge=1024)
    provider_environment: dict[str, str] | None = None
    http_retries: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_value(self) -> "CodingRunConfigPatch":
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("explicit null is not a supported config patch value")
        if not self.model_dump(exclude_none=True):
            raise ValueError("config patch must set at least one field")
        return self


class CodingRunAmendment(_StrictModel):
    sequence: int = Field(ge=1)
    kind: Literal["operational", "task_revision"]
    reason: str = Field(min_length=1)
    config: CodingRunConfigPatch | None = None
    goal: str | None = Field(default=None, min_length=1)
    task_revision: int = Field(ge=0)
    revision_workspace_snapshot: WorkspaceSnapshot | None = None
    revision_execution_start_index: int | None = Field(default=None, ge=0)
    created_at: datetime
    message_id: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{32}$", exclude_if=lambda v: v is None
    )
    submitted_after_turn: int | None = Field(
        default=None, ge=0, exclude_if=lambda v: v is None
    )
    operation_id: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{32}$", exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def check_kind_payload(self) -> "CodingRunAmendment":
        if (self.message_id is None) != (self.submitted_after_turn is None):
            raise ValueError("conversation metadata must include id and turn")
        if self.message_id is not None and self.kind != "task_revision":
            raise ValueError("conversation messages require a task revision")
        if self.operation_id is not None and self.kind != "operational":
            raise ValueError("operation id requires an operational amendment")
        if self.kind == "operational":
            if self.config is None or self.goal is not None:
                raise ValueError("operational amendment requires only config")
            if (
                self.revision_workspace_snapshot is not None
                or self.revision_execution_start_index is not None
            ):
                raise ValueError("operational amendment cannot reset evidence scope")
        else:
            if self.goal is None:
                raise ValueError("task revision requires goal")
            if (
                self.revision_workspace_snapshot is None
                or self.revision_execution_start_index is None
            ):
                raise ValueError("task revision requires a new evidence boundary")
        return self


class CodingRunAmendmentLedger(_StrictModel):
    schema_version: Literal[
        "coding-run-amendments-v1",
        "coding-run-amendments-v2",
        "coding-run-amendments-v3",
    ] = AMENDMENT_SCHEMA_VERSION
    run_id: str
    amendments: tuple[CodingRunAmendment, ...] = ()

    @model_validator(mode="after")
    def check_sequence_and_revision(self) -> "CodingRunAmendmentLedger":
        ids = [
            item.message_id for item in self.amendments if item.message_id is not None
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("conversation message ids must be unique")
        if ids and self.schema_version not in {
            "coding-run-amendments-v2",
            "coding-run-amendments-v3",
        }:
            raise ValueError("conversation metadata requires amendments v2")
        operation_ids = [
            item.operation_id
            for item in self.amendments
            if item.operation_id is not None
        ]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("operational amendment ids must be unique")
        if operation_ids and self.schema_version != "coding-run-amendments-v3":
            raise ValueError("operation ids require amendments v3")
        expected_sequence = 1
        expected_revision = 0
        for amendment in self.amendments:
            if amendment.sequence != expected_sequence:
                raise ValueError("amendment sequence must be contiguous")
            if amendment.kind == "task_revision":
                expected_revision += 1
            if amendment.task_revision != expected_revision:
                raise ValueError("amendment task_revision is inconsistent")
            expected_sequence += 1
        return self

    @property
    def sequence(self) -> int:
        return len(self.amendments)

    @property
    def task_revision(self) -> int:
        return sum(item.kind == "task_revision" for item in self.amendments)


@dataclass(frozen=True, slots=True)
class EffectiveCodingRun:
    initial_snapshot: WorkspaceSnapshot
    ledger: CodingRunAmendmentLedger
    request: CodingRunRequest
    amendment_sequence: int
    task_revision: int
    revision_workspace_snapshot: WorkspaceSnapshot
    revision_execution_start_index: int


def load_amendment_ledger(
    state_dir: Path,
    *,
    run_id: str,
) -> CodingRunAmendmentLedger:
    path = state_dir / AMENDMENT_LEDGER_NAME
    if not path.is_file():
        return CodingRunAmendmentLedger(run_id=run_id)
    ledger = CodingRunAmendmentLedger.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    if ledger.run_id != run_id:
        raise ValueError("amendment ledger run_id does not match request.json")
    return ledger


def resolve_effective_run(
    request: CodingRunRequest,
    initial_snapshot: WorkspaceSnapshot,
    ledger: CodingRunAmendmentLedger,
) -> EffectiveCodingRun:
    goal = request.goal
    runtime_policy = request.runtime_policy
    revision_snapshot = initial_snapshot
    execution_start_index = 0
    for amendment in ledger.amendments:
        if amendment.config is not None:
            if amendment.config.runtime_policy is not None:
                runtime_policy = amendment.config.runtime_policy.apply(runtime_policy)
        if amendment.kind == "task_revision":
            assert amendment.goal is not None
            assert amendment.revision_workspace_snapshot is not None
            assert amendment.revision_execution_start_index is not None
            goal = amendment.goal
            revision_snapshot = amendment.revision_workspace_snapshot
            execution_start_index = amendment.revision_execution_start_index
    effective_request = CodingRunRequest.model_validate(
        {
            **request.model_dump(mode="python"),
            "goal": goal,
            "runtime_policy": runtime_policy.model_dump(mode="python"),
        }
    )
    return EffectiveCodingRun(
        initial_snapshot=initial_snapshot,
        ledger=ledger,
        request=effective_request,
        amendment_sequence=ledger.sequence,
        task_revision=ledger.task_revision,
        revision_workspace_snapshot=revision_snapshot,
        revision_execution_start_index=execution_start_index,
    )


def append_operational_amendment(
    state_dir: Path,
    *,
    patch: CodingRunConfigPatch,
    reason: str,
    operation_id: str | None = None,
    allow_pending_question: bool = False,
    expected_sequence: int | None = None,
) -> CodingRunAmendment:
    return _append_amendment(
        state_dir,
        kind="operational",
        reason=reason,
        config=patch,
        operation_id=operation_id,
        allow_pending_question=allow_pending_question,
        expected_sequence=expected_sequence,
    )


def append_task_revision(
    state_dir: Path,
    *,
    goal: str,
    reason: str,
    config: CodingRunConfigPatch | None = None,
) -> CodingRunAmendment:
    return _append_amendment(
        state_dir,
        kind="task_revision",
        reason=reason,
        config=config,
        goal=goal,
    )


def amendment_prompt(
    ledger: CodingRunAmendmentLedger,
    *,
    after_sequence: int,
) -> str | None:
    pending = tuple(
        item for item in ledger.amendments if item.sequence > after_sequence
    )
    if not pending:
        return None
    lines = ["Apply the following user-authorized changes in this continuation:"]
    for item in pending:
        if item.kind == "task_revision":
            lines.append(f"- New task revision: {item.goal}")
        else:
            lines.append(f"- Runtime configuration changed: {item.reason}")
    lines.append(
        "Preserve useful history, but satisfy and verify the current task revision."
    )
    return "\n".join(lines)


def _append_amendment(
    state_dir: Path,
    *,
    kind: Literal["operational", "task_revision"],
    reason: str,
    config: CodingRunConfigPatch | None = None,
    goal: str | None = None,
    operation_id: str | None = None,
    allow_pending_question: bool = False,
    expected_sequence: int | None = None,
) -> CodingRunAmendment:
    resolved = state_dir.resolve(strict=True)
    with CodingRunFileLock(resolved) as lock:
        return _append_amendment_locked(
            resolved,
            lock=lock,
            kind=kind,
            reason=reason,
            config=config,
            goal=goal,
            operation_id=operation_id,
            allow_pending_question=allow_pending_question,
            expected_sequence=expected_sequence,
        )


def _append_amendment_locked(
    resolved: Path,
    *,
    lock: CodingRunFileLock,
    kind: Literal["operational", "task_revision"],
    reason: str,
    config: CodingRunConfigPatch | None = None,
    goal: str | None = None,
    resolving_question: bool = False,
    message_id: str | None = None,
    operation_id: str | None = None,
    allow_pending_question: bool = False,
    expected_sequence: int | None = None,
) -> CodingRunAmendment:
    """在调用方持有锁时追加修订，并对宿主操作提供幂等重放。

    Args:
        resolved: 已解析且存在的 state 目录。
        lock: 调用方持有的同一 state OS 锁。
        kind: operational 配置覆盖或 task revision。
        reason: 写入模型续跑提示和审计记录的原因。
        config: 可选配置覆盖。
        goal: task revision 的新任务目标。
        resolving_question: 答复事务内部追加 task revision 时跳过待答门禁。
        message_id: Web Thread 消息 ID，仅用于 task revision 兼容记录。
        operation_id: operational amendment 的宿主幂等 ID。
        allow_pending_question: 允许只改运行配置并保留当前待答问题。
        expected_sequence: 可选的乐观并发 amendment sequence。

    Returns:
        新追加或按 operation_id 找到的既有修订。

    Raises:
        ValueError: 状态、待答门禁、幂等内容或修订合同不一致。
    """
    lock.assert_held(resolved)
    from abaqus_sim_agent.coding.resume import load_resume_state

    persisted = load_resume_state(resolved)
    request_path = resolved / "request.json"
    initial_path = resolved / "initial_snapshot.json"
    if not request_path.is_file() or not initial_path.is_file():
        raise FileNotFoundError("CodingRun state is missing request or snapshot")
    request = CodingRunRequest.model_validate_json(
        request_path.read_text(encoding="utf-8")
    )
    if request.state_dir != resolved:
        raise ValueError("request.json state_dir does not match amendment target")
    ledger = load_amendment_ledger(resolved, run_id=request.run_id)
    if operation_id is not None:
        existing = next(
            (
                amendment
                for amendment in ledger.amendments
                if amendment.operation_id == operation_id
            ),
            None,
        )
        if existing is not None:
            if (
                existing.kind != kind
                or existing.reason != reason
                or existing.config != config
                or existing.goal != goal
            ):
                raise ValueError(
                    "operation id was already used for a different amendment"
                )
            return existing
    if expected_sequence is not None and ledger.sequence != expected_sequence:
        raise ValueError("amendment sequence changed before append")
    if (
        not resolving_question
        and not allow_pending_question
        and persisted.question_protocol == "ask_user_v1"
    ):
        from .user_questions import load_questions

        questions = load_questions(resolved, persisted.run_id).questions
        if questions and (
            questions[-1].pending
            or (
                questions[-1].answer is not None
                and questions[-1].answer_revision is None
            )
        ):
            raise ValueError(
                "resolve or explicitly cancel the pending question before amending"
            )
    if persisted.status == "needs_reconcile":
        raise ValueError("cannot amend a run that requires runtime reconciliation")
    initial_snapshot = WorkspaceSnapshot.model_validate_json(
        initial_path.read_text(encoding="utf-8")
    )
    workspace_snapshot: WorkspaceSnapshot | None = None
    execution_start_index: int | None = None
    if kind == "task_revision":
        workspace_snapshot = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
            request.workspace
        )
        execution_start_index = _execution_record_count(resolved)
    next_revision = ledger.task_revision + (kind == "task_revision")
    amendment = CodingRunAmendment(
        sequence=ledger.sequence + 1,
        kind=kind,
        reason=reason,
        config=config,
        goal=goal,
        task_revision=next_revision,
        revision_workspace_snapshot=workspace_snapshot,
        revision_execution_start_index=execution_start_index,
        created_at=datetime.now(UTC),
        message_id=message_id,
        submitted_after_turn=persisted.turn if message_id is not None else None,
        operation_id=operation_id,
    )
    schema_version = ledger.schema_version
    if operation_id is not None:
        schema_version = "coding-run-amendments-v3"
    elif message_id is not None and schema_version == "coding-run-amendments-v1":
        schema_version = "coding-run-amendments-v2"
    updated = CodingRunAmendmentLedger(
        schema_version=schema_version,
        run_id=request.run_id,
        amendments=(*ledger.amendments, amendment),
    )
    resolve_effective_run(request, initial_snapshot, updated)
    launch_path = resolved / "launch.json"
    if launch_path.is_file():
        from abaqus_sim_agent.coding.launch import (
            LaunchManifest,
            apply_launch_amendments,
        )

        launch = LaunchManifest.model_validate_json(launch_path.read_bytes())
        apply_launch_amendments(launch, updated)
    _archive_current_projection(resolved, ledger)
    _write_text_atomic(
        resolved / AMENDMENT_LEDGER_NAME,
        updated.model_dump_json(indent=2, exclude_none=True),
    )
    _remove_stale_verification_projection(resolved)
    return amendment


def _archive_current_projection(
    state_dir: Path,
    ledger: CodingRunAmendmentLedger,
) -> None:
    projection_revision = ledger.task_revision
    projection_sequence = ledger.sequence
    outcome_path = state_dir / "outcome.json"
    if outcome_path.is_file():
        try:
            outcome = CodingRunOutcome.model_validate_json(
                outcome_path.read_text(encoding="utf-8")
            )
        except ValueError:
            pass
        else:
            projection_revision = outcome.task_revision
            projection_sequence = outcome.amendment_sequence
    archive = state_dir / "revisions" / f"task-{projection_revision:04d}"
    for name in ("outcome.json", "verification.json"):
        source = state_dir / name
        if not source.is_file():
            continue
        target = archive / f"sequence-{projection_sequence:04d}-{name}"
        if target.exists():
            continue
        _write_bytes_atomic(target, source.read_bytes())


def _execution_record_count(state_dir: Path) -> int:
    path = state_dir / "execution_results.jsonl"
    if not path.is_file():
        return 0
    return sum(
        bool(line.strip()) for line in path.read_text(encoding="utf-8").splitlines()
    )


def _remove_stale_verification_projection(state_dir: Path) -> None:
    try:
        (state_dir / "verification.json").unlink(missing_ok=True)
    except OSError:
        # The ledger is already committed. Consumers use its sequence to reject
        # this stale projection, so a Windows sharing violation must not make the
        # caller retry and accidentally append a duplicate amendment.
        return
