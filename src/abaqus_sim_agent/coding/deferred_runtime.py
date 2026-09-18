from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.coding.persistence import write_text_atomic
from abaqus_sim_agent.coding.workspace import WorkspaceSnapshot
from abaqus_sim_agent.execution import AbaqusExecutionResult

DEFERRED_TOOL_FRONTIER_NAME = "deferred_tool_frontier.json"


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class WaitingForToolExecution(_StrictModel):
    """一个 Agent 工作段已释放，宿主仍在执行原工具调用。"""

    status: Literal["waiting_for_tool_execution"] = "waiting_for_tool_execution"
    run_id: str
    tool_name: Literal["run_solver_job"] = "run_solver_job"
    tool_call_id: str = Field(min_length=1)
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class DeferredToolFrontier(_StrictModel):
    """持久化一次已离开模型调用栈、仍由宿主执行的 Solver 工具调用。"""

    schema_version: Literal["deferred-tool-frontier-v1"] = "deferred-tool-frontier-v1"
    state: Literal["waiting", "resuming", "resolved"] = "waiting"
    run_id: str = Field(min_length=1)
    tool_name: Literal["run_solver_job"] = "run_solver_job"
    tool_call_id: str = Field(min_length=1)
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    metadata: dict[str, object]
    turn: int = Field(ge=0)
    amendment_sequence: int = Field(ge=0)
    task_revision: int = Field(ge=0)
    continuation_id: str | None = None
    history_json: str = Field(min_length=2)
    history_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_snapshot: WorkspaceSnapshot
    execution_count_before: int = Field(ge=0)
    execution_journal_count_before: int = Field(ge=0)
    tool_result_payload: dict[str, object] | None = None
    tool_failure: str | None = None
    created_at: datetime
    updated_at: datetime


def load_deferred_tool_frontier(state_dir: Path) -> DeferredToolFrontier | None:
    path = state_dir / DEFERRED_TOOL_FRONTIER_NAME
    if not path.is_file():
        return None
    return DeferredToolFrontier.model_validate_json(path.read_text(encoding="utf-8"))


def write_deferred_tool_frontier(
    state_dir: Path,
    frontier: DeferredToolFrontier,
) -> DeferredToolFrontier:
    updated = frontier.model_copy(update={"updated_at": datetime.now(UTC)})
    write_text_atomic(
        state_dir / DEFERRED_TOOL_FRONTIER_NAME,
        updated.model_dump_json(indent=2),
    )
    return updated


@dataclass(frozen=True, slots=True)
class DeferredSolverResolution:
    status: Literal[
        "starting",
        "running",
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "needs_reconcile",
    ]
    result: AbaqusExecutionResult | None = None
    error: str | None = None


@runtime_checkable
class DeferredSolverRunner(Protocol):
    """宿主可选实现；普通 CLI runner 不需要支持。"""

    def start_deferred_solver_job(
        self,
        *,
        tool_call_id: str,
        job_name: str,
        input_file: str,
        timeout_seconds: float | None,
    ) -> str: ...

    def resolve_deferred_solver_job(
        self, execution_id: str
    ) -> DeferredSolverResolution: ...
