"""有界查证的应用合同。supported仅是model-assessed，不是物理或语义验收。"""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from abaqus_sim_agent.retrieval.evidence_contracts import (
    Binding,
    Contract,
    Digest,
    EvidenceResult,
    Product,
    SearchId,
    SearchRequest,
)

NeedId = SearchId
RunId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
Statement = Annotated[str, Field(min_length=1, max_length=1000)]


class DocumentScope(Contract):
    product: Product | None = None
    source_release: str | None = Field(default=None, min_length=1, max_length=80)
    allowed_sources: tuple[str, ...] = Field(default=(), max_length=64)
    constraints_origin: Literal["host_typed", "user_confirmed", "model_interpreted"]


class PolicyConfiguration(Contract):
    mode: Literal["bounded_v1"] = "bounded_v1"
    concept_retrieval_mode: Literal["tool"] = "tool"
    run_id: RunId
    binding: Binding
    scope: DocumentScope


class ProposedNeed(Contract):
    condition: Statement
    critical: bool = True
    affects_step: Statement

    @model_validator(mode="after")
    def nonblank(self):
        if not self.condition.strip() or not self.affects_step.strip():
            raise ValueError("need text must not be blank")
        return self


class HostNeed(ProposedNeed):
    origin: Literal["user_declared", "task_contract"]
    kind: Literal["knowledge", "physical_input"] = "knowledge"


class EvidenceNeed(ProposedNeed):
    id: NeedId
    origin: Literal["user_declared", "task_contract", "model_proposed"]
    kind: Literal["knowledge", "physical_input"] = "knowledge"


class _Query(Contract):
    question: str = Field(min_length=1, max_length=20000)
    observed_context: str = Field(default="", max_length=20000)


class FirstQuery(_Query):
    kind: Literal["first"] = "first"
    proposed_needs: tuple[ProposedNeed, ...] = Field(default=(), max_length=8)


class FollowUpQuery(_Query):
    kind: Literal["follow_up"] = "follow_up"
    follow_up_of: SearchId
    unresolved_need_ids: tuple[NeedId, ...] = Field(min_length=1, max_length=8)


PolicyQuery = Annotated[FirstQuery | FollowUpQuery, Field(discriminator="kind")]
QUERY_ADAPTER = TypeAdapter(PolicyQuery)


class Citation(Contract):
    search_id: SearchId
    evidence_id: str = Field(min_length=1, max_length=512)
    quote: str = Field(min_length=1, max_length=2000)
    applicability: Literal["as_documented", "unknown"]

    @model_validator(mode="after")
    def nonblank(self):
        if not self.quote.strip():
            raise ValueError("quote must not be blank")
        return self


class NeedAssessment(Contract):
    need_id: NeedId
    status: Literal["supported", "partial", "missing", "conflict"]
    citations: tuple[Citation, ...] = Field(default=(), max_length=8)
    explanation: Statement
    limitations: tuple[Statement, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def support_requires_citation(self):
        if self.status != "missing" and not self.citations:
            raise ValueError("support/partial/conflict requires exact citations")
        return self


class DocumentAssessment(Contract):
    search_id: SearchId
    scope: DocumentScope
    needs: tuple[NeedAssessment, ...] = Field(min_length=1, max_length=8)
    next_action: Literal["continue", "follow_up", "stop"]
    judgment_origin: Literal["model_assessed_not_verified"] = (
        "model_assessed_not_verified"
    )


class PolicySearchReply(Contract):
    root_search_id: SearchId
    result: EvidenceResult
    needs: tuple[EvidenceNeed, ...]
    cache_hit: bool
    cumulative_lookup_calls: int = Field(ge=0)
    requires_assessment: bool


class PolicyGate(Contract):
    allowed: bool
    reason: Literal[
        "original_checks_required", "assessment_pending", "needs_input", "knowledge_gap"
    ]
    unresolved_need_ids: tuple[NeedId, ...] = ()


class PolicyCheckpoint(Contract):
    run_id: RunId
    sequence: int = Field(ge=0)
    head_sha256: Digest
    task_revision: int = Field(ge=0)
    cumulative_lookup_calls: int = Field(ge=0)


class Initialized(Contract):
    kind: Literal["initialized"] = "initialized"
    configuration: PolicyConfiguration
    max_lookup_calls: int = Field(ge=0)
    initial_lookup_calls: int = Field(ge=0)


class RevisionStarted(Contract):
    kind: Literal["revision_started"] = "revision_started"
    task_revision: int = Field(ge=0)
    host_needs: tuple[EvidenceNeed, ...] = Field(max_length=8)
    scope: DocumentScope


class NeedsRegistered(Contract):
    kind: Literal["needs_registered"] = "needs_registered"
    needs: tuple[EvidenceNeed, ...] = Field(min_length=1, max_length=8)


class SearchReserved(Contract):
    kind: Literal["search_reserved"] = "search_reserved"
    search_id: SearchId
    root_search_id: SearchId
    query: PolicyQuery
    request: SearchRequest
    need_ids: tuple[NeedId, ...] = Field(min_length=1, max_length=8)
    cumulative_lookup_calls: int = Field(ge=1)


class EvidenceReceived(Contract):
    kind: Literal["evidence_received"] = "evidence_received"
    search_id: SearchId
    result: EvidenceResult


class AssessmentRecorded(Contract):
    kind: Literal["assessment_recorded"] = "assessment_recorded"
    assessment: DocumentAssessment


class SearchAbandoned(Contract):
    kind: Literal["search_abandoned"] = "search_abandoned"
    search_id: SearchId


class EvidenceInvalidated(Contract):
    kind: Literal["evidence_invalidated"] = "evidence_invalidated"
    search_id: SearchId


class QuotaAmended(Contract):
    kind: Literal["quota_amended"] = "quota_amended"
    max_lookup_calls: int = Field(ge=0)


class InputProvided(Contract):
    kind: Literal["input_provided"] = "input_provided"
    need_id: NeedId
    host_reference: Statement


class IndependentOperationDeclared(Contract):
    kind: Literal["independent_operation_declared"] = "independent_operation_declared"
    operation_id: SearchId
    independent_of_need_ids: tuple[NeedId, ...] = Field(min_length=1, max_length=8)
    host_reference: Statement


PolicyEvent = Annotated[
    Initialized
    | RevisionStarted
    | NeedsRegistered
    | SearchReserved
    | EvidenceReceived
    | AssessmentRecorded
    | SearchAbandoned
    | EvidenceInvalidated
    | QuotaAmended
    | InputProvided
    | IndependentOperationDeclared,
    Field(discriminator="kind"),
]


class JournalEntry(Contract):
    protocol: Literal["document-policy-journal-v1"] = "document-policy-journal-v1"
    sequence: int = Field(ge=0)
    previous_sha256: Digest
    event: PolicyEvent
    sha256: Digest
