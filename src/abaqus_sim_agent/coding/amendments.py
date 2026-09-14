from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunOutcome,
    CodingRunRequest,
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


class CodingRunBudgetPatch(_StrictModel):
    max_clarifications: int | None = Field(default=None, ge=0)
    max_model_requests: int | None = Field(default=None, ge=1)
    max_api_lookup_calls: int | None = Field(default=None, ge=0)
    max_concept_lookup_calls: int | None = Field(default=None, ge=0)
    max_diagnostic_retrieval_calls: int | None = Field(default=None, ge=0)
    max_static_preflight_calls: int | None = Field(default=None, ge=0)
    max_abaqus_calls: int | None = Field(default=None, ge=0)
    max_solver_calls: int | None = Field(default=None, ge=0)
    wall_time_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_value(self) -> "CodingRunBudgetPatch":
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("explicit null is not a supported budget patch value")
        if not self.model_dump(exclude_none=True):
            raise ValueError("budget patch must set at least one field")
        return self

    def apply(self, budget: CodingRunBudget) -> CodingRunBudget:
        values = budget.model_dump(mode="python")
        values.update(self.model_dump(mode="python", exclude_none=True))
        return CodingRunBudget.model_validate(values)


class CodingRunConfigPatch(_StrictModel):
    budget: CodingRunBudgetPatch | None = None
    model_provider: DeepSeekGateway | None = None
    model_name: str | None = Field(default=None, min_length=1)
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

    @model_validator(mode="after")
    def check_kind_payload(self) -> "CodingRunAmendment":
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
    schema_version: Literal["coding-run-amendments-v1"] = AMENDMENT_SCHEMA_VERSION
    run_id: str
    amendments: tuple[CodingRunAmendment, ...] = ()

    @model_validator(mode="after")
    def check_sequence_and_revision(self) -> "CodingRunAmendmentLedger":
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
    budget = request.budget
    revision_snapshot = initial_snapshot
    execution_start_index = 0
    for amendment in ledger.amendments:
        if amendment.config is not None:
            if amendment.config.budget is not None:
                budget = amendment.config.budget.apply(budget)
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
            "budget": budget.model_dump(mode="python"),
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
) -> CodingRunAmendment:
    return _append_amendment(
        state_dir,
        kind="operational",
        reason=reason,
        config=patch,
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
) -> CodingRunAmendment:
    resolved = state_dir.resolve(strict=True)
    with CodingRunFileLock(resolved) as lock:
        return _append_amendment_locked(
            resolved, lock=lock, kind=kind, reason=reason, config=config, goal=goal
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
) -> CodingRunAmendment:
    """在调用方持有锁时追加修订；仅答复事务可跳过待答检查。"""
    lock.assert_held(resolved)
    from abaqus_sim_agent.coding.resume import load_resume_state

    persisted = load_resume_state(resolved)
    if not resolving_question and persisted.question_protocol == "ask_user_v1":
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
    request_path = resolved / "request.json"
    initial_path = resolved / "initial_snapshot.json"
    if not request_path.is_file() or not initial_path.is_file():
        raise FileNotFoundError("CodingRun state is missing request or snapshot")
    request = CodingRunRequest.model_validate_json(
        request_path.read_text(encoding="utf-8")
    )
    if request.state_dir != resolved:
        raise ValueError("request.json state_dir does not match amendment target")
    initial_snapshot = WorkspaceSnapshot.model_validate_json(
        initial_path.read_text(encoding="utf-8")
    )
    ledger = load_amendment_ledger(resolved, run_id=request.run_id)
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
    )
    updated = CodingRunAmendmentLedger(
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
