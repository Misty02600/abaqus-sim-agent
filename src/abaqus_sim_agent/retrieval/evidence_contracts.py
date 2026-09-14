"""独立文档证据协议；found 只代表候选，不代表相关、充分或物理验收。"""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SearchId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,120}$")]
Text = Annotated[str, Field(max_length=4000)]
Product = Literal["Abaqus/Standard", "Abaqus/Explicit"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class QuestionQuery(Contract):
    kind: Literal["question"] = "question"
    question: str = Field(min_length=1, max_length=20000)
    observed_context: str = Field(default="", max_length=20000)
    product: Product | None = None
    source_release: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.question.strip():
            raise ValueError("question must not be blank")
        return self


class SymbolQuery(Contract):
    kind: Literal["symbol"] = "symbol"
    symbol: str = Field(min_length=1, max_length=80)
    symbol_kind: Literal["keyword", "element", "output_variable"]
    product: Product | None = None
    source_release: str | None = Field(default=None, max_length=80)


Query = Annotated[QuestionQuery | SymbolQuery, Field(discriminator="kind")]


class SearchRequest(Contract):
    query: Query
    constraints_origin: Literal["host_typed", "user_confirmed", "model_interpreted"]
    # Exact source page URLs, without fragments; empty means the bound corpus.
    allowed_sources: tuple[Text, ...] = Field(default=(), max_length=64)
    observed_context_origin: Literal["agent_provided_unverified"] = (
        "agent_provided_unverified"
    )


class Applicability(Contract):
    products: tuple[Product, ...] = Field(max_length=2)
    origin: Literal["official_page", "verified_section", "unknown"]
    evidence: Text


class EquationReference(Contract):
    id: Digest
    source: Text
    member_xpaths: tuple[Text, ...] = Field(min_length=1, max_length=64)
    projection_status: Literal[
        "text", "deferred_math", "deferred_table", "deferred_list"
    ]
    relation: Literal["same_original_equation_group_not_semantic_coreference"]
    body_included: Literal[False]


class Evidence(Contract):
    id: str = Field(min_length=1, max_length=512)
    document_id: Text
    title: Text
    section: Text
    source: Text
    source_sha256: Digest
    release: str = Field(min_length=1, max_length=80)
    applicability: Applicability
    body_scope: Literal[
        "complete_stored_evidence_unit",
        "complete_source_paragraph_with_equation_context_reference",
        "indexed_symbol_card",
    ]
    body: str = Field(min_length=1, max_length=100000)
    body_sha256: Digest
    member_xpaths: tuple[Text, ...] = Field(min_length=1, max_length=64)
    equation_context: tuple[EquationReference, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def check_body(self):
        if hashlib.sha256(self.body.encode()).hexdigest() != self.body_sha256:
            raise ValueError("body hash mismatch")
        if bool(self.equation_context) != (
            self.body_scope
            == "complete_source_paragraph_with_equation_context_reference"
        ):
            raise ValueError("body scope and equation references disagree")
        return self


class Binding(Contract):
    # Actual validated files, never just the configured directory names.
    profile: Literal["rag-evidence-baseline-v1"] = "rag-evidence-baseline-v1"
    snapshot_sha256: Digest
    prepared_sha256: Digest
    candidate_index_sha256: Digest
    symbol_vector_sha256: Digest
    ranker_acquisition_sha256: Digest


class DeliveryLimit(Contract):
    id: str = Field(max_length=512)
    reason: Literal[
        "unit_body_budget",
        "serialized_context_budget",
        "serialized_context_budget_including_equation_references",
        "serialized_protocol_evidence_budget",
        "source_constraint",
    ]


class Cost(Contract):
    stage: Literal["not_started", "loading", "searching", "validating", "delivered"]
    candidate_count: int = Field(default=0, ge=0, le=64)
    scored_count: int = Field(default=0, ge=0, le=64)
    body_tokens: tuple[int, ...] = Field(default=(), max_length=4)
    evidence_tokens: int = Field(default=0, ge=0, le=4000)
    baseline_evidence_tokens: int | None = Field(default=None, ge=0, le=4000)
    load_seconds: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    search_seconds: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    validation_seconds: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    tokenizer: Literal["MiniLM WordPiece proxy; not provider billing"] = (
        "MiniLM WordPiece proxy; not provider billing"
    )

    @model_validator(mode="after")
    def body_budget(self):
        if any(not 0 <= n <= 1200 for n in self.body_tokens):
            raise ValueError("body token budget violated")
        return self


class _Result(Contract):
    protocol: Literal["doc-evidence-result-v1"] = "doc-evidence-result-v1"
    search_id: SearchId
    backend_profile: Literal["rag-evidence-baseline-v1"] = "rag-evidence-baseline-v1"
    cost: Cost = Cost(stage="not_started")
    message: str = Field(default="", max_length=320)
    limits: tuple[DeliveryLimit, ...] = Field(default=(), max_length=128)


class Found(_Result):
    status: Literal["found"] = "found"
    binding: Binding
    evidence: tuple[Evidence, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def counts(self):
        if len({e.id for e in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate evidence")
        if len(self.evidence) != len(self.cost.body_tokens):
            raise ValueError("body counts missing")
        return self


class NotFound(_Result):
    status: Literal["not_found"] = "not_found"
    binding: Binding
    reason_code: Literal[
        "no_candidates",
        "scope_filtered",
        "requested_release_not_indexed",
        "no_unit_fits_budget",
    ]
    evidence: tuple[()] = ()


class Unavailable(_Result):
    binding: Binding | None = None
    status: Literal["unavailable"] = "unavailable"
    reason_code: Literal[
        "integrity_mismatch", "model_unavailable", "worker_timeout", "worker_failed"
    ]
    evidence: tuple[()] = ()


class Rejected(_Result):
    binding: Binding | None = None
    status: Literal["rejected"] = "rejected"
    reason_code: Literal["invalid_request", "query_too_long", "unsupported_query_shape"]
    evidence: tuple[()] = ()


EvidenceResult = Annotated[
    Found | NotFound | Unavailable | Rejected, Field(discriminator="status")
]
RESULT_ADAPTER = TypeAdapter(EvidenceResult)
