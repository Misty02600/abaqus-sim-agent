from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.domain.models import (
    ArtifactManifest,
    DiagnosisReport,
    ModelingPlan,
    RequirementResult,
    SimulationSpec,
    VerificationReport,
)


class SupervisorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    next_role: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class SupervisorAgent(Protocol):
    async def decide(
        self,
        state: Mapping[str, Any],
        allowed_roles: Sequence[str],
    ) -> SupervisorDecision: ...


class InterpreterAgent(Protocol):
    async def interpret(
        self,
        user_request: str,
        clarification_history: Sequence[Mapping[str, str]],
    ) -> RequirementResult: ...


class ArchitectAgent(Protocol):
    async def design(
        self,
        spec: SimulationSpec,
        retrieved_context: Sequence[str],
    ) -> ModelingPlan: ...


class InputWriterAgent(Protocol):
    async def build(
        self,
        spec: SimulationSpec,
        plan: ModelingPlan,
        patch_instructions: Sequence[str],
    ) -> ArtifactManifest: ...


class ReviewerAgent(Protocol):
    async def diagnose(
        self,
        spec: SimulationSpec,
        plan: ModelingPlan,
        logs: Sequence[str],
        retrieved_context: Sequence[str],
    ) -> DiagnosisReport: ...


class VerifierAgent(Protocol):
    async def verify(
        self,
        spec: SimulationSpec,
        result_artifacts: Mapping[str, str],
    ) -> VerificationReport: ...
