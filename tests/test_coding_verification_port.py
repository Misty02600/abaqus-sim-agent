from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.coding import (
    CodingRunVerifier as PublicCodingRunVerifier,
)
from abaqus_sim_agent.coding import (
    VerificationAttempt as PublicVerificationAttempt,
)
from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    ReadyForVerificationDecision,
)
from abaqus_sim_agent.coding.verification_port import (
    CodingRunVerifier,
    VerificationAttempt,
)
from abaqus_sim_agent.coding.workspace import WorkspaceSnapshot
from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult


class _TaskAttempt(VerificationAttempt):
    measurement: float | None = None


class _GenericVerifier:
    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> VerificationAttempt:
        del request, runner, initial_snapshot, executions, decision
        return VerificationAttempt(passed=True, feedback="verified")


def test_task_attempt_extends_task_agnostic_verification_attempt() -> None:
    attempt = _TaskAttempt(
        passed=False,
        feedback="ODB verification failed",
        failure_stage="verification",
    )

    assert isinstance(attempt, VerificationAttempt)
    assert attempt.failure_stage == "verification"


def test_verification_attempt_is_strict_and_frozen() -> None:
    attempt = VerificationAttempt(passed=True, feedback="verified")

    assert attempt.scope == "task_oracle"

    with pytest.raises(ValidationError):
        VerificationAttempt.model_validate(
            {"passed": True, "feedback": "verified", "unexpected": True}
        )
    with pytest.raises(ValidationError):
        attempt.__setattr__("feedback", "changed")


def test_generic_verifier_satisfies_protocol_statically() -> None:
    verifier: CodingRunVerifier[VerificationAttempt] = _GenericVerifier()

    assert isinstance(verifier, _GenericVerifier)


def test_coding_facade_reexports_coding_owned_port() -> None:
    assert PublicCodingRunVerifier is CodingRunVerifier
    assert PublicVerificationAttempt is VerificationAttempt
