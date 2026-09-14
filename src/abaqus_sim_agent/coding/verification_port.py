from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.coding.contracts import (
    ArtifactRef,
    CodingRunRequest,
    ReadyForVerificationDecision,
    VerificationScope,
)
from abaqus_sim_agent.coding.workspace import WorkspaceSnapshot
from abaqus_sim_agent.execution.abaqus2024 import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
)

__all__ = ["CodingRunVerifier", "VerificationAttempt"]


class VerificationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    feedback: str = Field(min_length=1)
    artifacts: tuple[ArtifactRef, ...] = ()
    scope: VerificationScope = "task_oracle"
    failure_stage: str | None = None


VerificationAttemptT_co = TypeVar(
    "VerificationAttemptT_co",
    bound=VerificationAttempt,
    covariant=True,
)


class CodingRunVerifier(Protocol[VerificationAttemptT_co]):
    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> VerificationAttemptT_co: ...
