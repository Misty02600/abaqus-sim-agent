from typing import TYPE_CHECKING, Any

from abaqus_sim_agent.coding.amendments import (
    CodingRunAmendment,
    CodingRunAmendmentLedger,
    CodingRunConfigPatch,
    RuntimeExecutionPolicyPatch,
    append_operational_amendment,
    append_task_revision,
    load_amendment_ledger,
)
from abaqus_sim_agent.coding.clarification import (
    ClarificationProvider,
    ConsoleClarificationProvider,
    ScriptedClarificationProvider,
)
from abaqus_sim_agent.coding.contracts import (
    CLARIFICATION_POLICY_VERSION,
    ApiCatalogBinding,
    ApiLookupTrace,
    ArtifactKind,
    ArtifactRef,
    ClarificationAnswer,
    ClarificationEvent,
    ClarificationKind,
    ClarificationRequest,
    CodingRunBudget,
    CodingRunDecision,
    CodingRunLimits,
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
    CodingRunUsage,
    ConceptIndexBinding,
    ConceptLookupTrace,
    DeliverableDeclaration,
    FailedDecision,
    NeedsInputDecision,
    ReadyForVerificationDecision,
    RuntimeExecutionPolicy,
    RuntimeProfile,
    RuntimeScriptPolicy,
    StaticPreflightBinding,
    VerificationScope,
)
from abaqus_sim_agent.coding.execution_verifier import (
    ExecutionEvidenceVerification,
    ExecutionEvidenceVerifier,
)
from abaqus_sim_agent.coding.verification_port import (
    CodingRunVerifier,
    VerificationAttempt,
)
from abaqus_sim_agent.coding.workspace import (
    FileSnapshot,
    ResolvedWorkspace,
    WorkspaceResolutionError,
    WorkspaceResolver,
    WorkspaceSnapshot,
)

if TYPE_CHECKING:
    from abaqus_sim_agent.coding.resume import (
        CodingRunReconciliationRequired,
        CodingRunResumeError,
        CodingRunWorkspaceChanged,
    )
    from abaqus_sim_agent.coding.run_lock import CodingRunAlreadyLocked
    from abaqus_sim_agent.coding.service import (
        CodingRunInfrastructureError,
        CodingRunResult,
        CodingRunService,
    )

__all__ = [
    "CLARIFICATION_POLICY_VERSION",
    "ApiCatalogBinding",
    "ApiLookupTrace",
    "ArtifactKind",
    "ArtifactRef",
    "ClarificationAnswer",
    "ClarificationEvent",
    "ClarificationKind",
    "ClarificationProvider",
    "ClarificationRequest",
    "CodingRunAlreadyLocked",
    "CodingRunAmendment",
    "CodingRunAmendmentLedger",
    "CodingRunBudget",
    "CodingRunConfigPatch",
    "CodingRunDecision",
    "CodingRunInfrastructureError",
    "CodingRunLimits",
    "CodingRunOutcome",
    "CodingRunReconciliationRequired",
    "CodingRunRequest",
    "CodingRunResult",
    "CodingRunResumeError",
    "CodingRunService",
    "CodingRunStatus",
    "CodingRunUsage",
    "CodingRunVerifier",
    "CodingRunWorkspaceChanged",
    "ConceptIndexBinding",
    "ConceptLookupTrace",
    "ConsoleClarificationProvider",
    "DeliverableDeclaration",
    "ExecutionEvidenceVerification",
    "ExecutionEvidenceVerifier",
    "FailedDecision",
    "FileSnapshot",
    "NeedsInputDecision",
    "ReadyForVerificationDecision",
    "ResolvedWorkspace",
    "RuntimeExecutionPolicy",
    "RuntimeExecutionPolicyPatch",
    "RuntimeProfile",
    "RuntimeScriptPolicy",
    "ScriptedClarificationProvider",
    "StaticPreflightBinding",
    "VerificationAttempt",
    "VerificationScope",
    "WorkspaceResolutionError",
    "WorkspaceResolver",
    "WorkspaceSnapshot",
    "append_operational_amendment",
    "append_task_revision",
    "load_amendment_ledger",
]


def __getattr__(name: str) -> Any:
    if name in {
        "CodingRunReconciliationRequired",
        "CodingRunResumeError",
        "CodingRunWorkspaceChanged",
    }:
        from abaqus_sim_agent.coding import resume

        return getattr(resume, name)
    if name == "CodingRunAlreadyLocked":
        from abaqus_sim_agent.coding import run_lock

        return run_lock.CodingRunAlreadyLocked
    if name in {
        "CodingRunInfrastructureError",
        "CodingRunResult",
        "CodingRunService",
    }:
        from abaqus_sim_agent.coding import service

        return getattr(service, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
