from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .document_evidence import DOCUMENT_BODY_BUDGET
from .document_policy_binding import DocumentPolicyBinding
from .edit_scope import ReferenceEditScope

CLARIFICATION_POLICY_VERSION = "physics-materiality-clarification-v1"
API_LOOKUP_JOURNAL_NAME = "api_lookups.jsonl"
API_DISCOVERY_JOURNAL_NAME = "api_discoveries.jsonl"
DIAGNOSTIC_RETRIEVAL_JOURNAL_NAME = "diagnostic_retrievals.jsonl"
VerificationScope = Literal["execution_evidence", "task_oracle"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class UnlimitedLimit(_StrictModel):
    mode: Literal["unlimited"] = "unlimited"


class LimitedCount(_StrictModel):
    mode: Literal["limited"] = "limited"
    value: int = Field(ge=0)


class LimitedDuration(_StrictModel):
    mode: Literal["limited"] = "limited"
    seconds: float = Field(gt=0, allow_inf_nan=False)


CountLimit = Annotated[UnlimitedLimit | LimitedCount, Field(discriminator="mode")]
DurationLimit = Annotated[
    UnlimitedLimit | LimitedDuration,
    Field(discriminator="mode"),
]


class CodingRunLimits(_StrictModel):
    model_requests: CountLimit = Field(default_factory=UnlimitedLimit)
    clarifications: CountLimit = Field(default_factory=UnlimitedLimit)
    active_wall: DurationLimit = Field(default_factory=UnlimitedLimit)

    @model_validator(mode="after")
    def model_request_limit_is_positive(self) -> "CodingRunLimits":
        if (
            isinstance(self.model_requests, LimitedCount)
            and self.model_requests.value < 1
        ):
            raise ValueError("limited model_requests must be at least one")
        return self

    @property
    def request_limit(self) -> int | None:
        return (
            self.model_requests.value
            if isinstance(self.model_requests, LimitedCount)
            else None
        )

    @property
    def clarification_limit(self) -> int | None:
        return (
            self.clarifications.value
            if isinstance(self.clarifications, LimitedCount)
            else None
        )

    @property
    def active_wall_seconds(self) -> float | None:
        return (
            self.active_wall.seconds
            if isinstance(self.active_wall, LimitedDuration)
            else None
        )


class RuntimeDenied(_StrictModel):
    mode: Literal["deny"] = "deny"


class RuntimeAllowed(_StrictModel):
    mode: Literal["allow"] = "allow"


class RuntimeAllowedWithLimit(_StrictModel):
    mode: Literal["allow_with_limit"] = "allow_with_limit"
    limit: int = Field(ge=1)


RuntimePermission = Annotated[
    RuntimeDenied | RuntimeAllowed | RuntimeAllowedWithLimit,
    Field(discriminator="mode"),
]


class RuntimeExecutionPolicy(_StrictModel):
    abaqus: RuntimePermission = Field(default_factory=RuntimeAllowed)
    solver: RuntimePermission = Field(default_factory=RuntimeDenied)
    cae_timeout: DurationLimit = Field(default_factory=UnlimitedLimit)
    abaqus_python_timeout: DurationLimit = Field(default_factory=UnlimitedLimit)
    solver_timeout: DurationLimit = Field(default_factory=UnlimitedLimit)

    def timeout_seconds(
        self, kind: Literal["cae", "abaqus_python", "solver"]
    ) -> float | None:
        value = {
            "cae": self.cae_timeout,
            "abaqus_python": self.abaqus_python_timeout,
            "solver": self.solver_timeout,
        }[kind]
        return value.seconds if isinstance(value, LimitedDuration) else None

    def runtime_denial(
        self, *, solver: bool, abaqus_used: int, solver_used: int
    ) -> str | None:
        parent = self.abaqus
        if isinstance(parent, RuntimeDenied):
            return "Abaqus execution is not authorized"
        if isinstance(parent, RuntimeAllowedWithLimit) and abaqus_used >= parent.limit:
            return "Abaqus invocation allowance is exhausted"
        if not solver:
            return None
        child = self.solver
        if isinstance(child, RuntimeDenied):
            return "Abaqus solver execution is not authorized"
        if isinstance(child, RuntimeAllowedWithLimit) and solver_used >= child.limit:
            return "Abaqus solver invocation allowance is exhausted"
        return None


class EvaluationToolLimits(_StrictModel):
    api_lookup_calls: int | None = Field(default=None, ge=0)
    concept_lookup_calls: int | None = Field(default=None, ge=0)
    diagnostic_retrieval_calls: int | None = Field(default=None, ge=0)
    static_preflight_calls: int | None = Field(default=None, ge=0)


class CodingRunControlValues(TypedDict):
    limits: CodingRunLimits
    runtime_policy: RuntimeExecutionPolicy
    evaluation_tool_limits: EvaluationToolLimits | None


class CodingRunBudget(_StrictModel):
    """冻结评测使用的旧式额度输入；产品 request 不持久化该聚合对象。"""

    max_clarifications: int = Field(default=2, ge=0)
    max_model_requests: int = Field(default=30, ge=1)
    max_api_lookup_calls: int | None = Field(default=4, ge=0)
    max_concept_lookup_calls: int | None = Field(default=2, ge=0)
    max_diagnostic_retrieval_calls: int | None = Field(default=2, ge=0)
    max_static_preflight_calls: int | None = Field(default=12, ge=0)
    max_abaqus_calls: int = Field(default=9, ge=0)
    max_solver_calls: int = Field(default=3, ge=0)
    wall_time_seconds: float = Field(default=2_700.0, gt=0)

    @model_validator(mode="after")
    def check_solver_call_budget(self) -> "CodingRunBudget":
        if self.max_solver_calls > self.max_abaqus_calls:
            raise ValueError("max_solver_calls cannot exceed max_abaqus_calls")
        return self

    def as_request_controls(self) -> CodingRunControlValues:
        """把冻结评测额度转换为当前 request 拥有的分层控制模型。"""
        return {
            "limits": CodingRunLimits(
                model_requests=LimitedCount(value=self.max_model_requests),
                clarifications=LimitedCount(value=self.max_clarifications),
                active_wall=LimitedDuration(seconds=self.wall_time_seconds),
            ),
            "runtime_policy": RuntimeExecutionPolicy(
                abaqus=(
                    RuntimeDenied()
                    if self.max_abaqus_calls == 0
                    else RuntimeAllowedWithLimit(limit=self.max_abaqus_calls)
                ),
                solver=(
                    RuntimeDenied()
                    if self.max_solver_calls == 0
                    else RuntimeAllowedWithLimit(limit=self.max_solver_calls)
                ),
            ),
            "evaluation_tool_limits": EvaluationToolLimits(
                api_lookup_calls=self.max_api_lookup_calls,
                concept_lookup_calls=self.max_concept_lookup_calls,
                diagnostic_retrieval_calls=self.max_diagnostic_retrieval_calls,
                static_preflight_calls=self.max_static_preflight_calls,
            ),
        }


class RuntimeProfile(_StrictModel):
    name: str = Field(default="abaqus-2024", min_length=1)
    version: str = Field(default="2024", min_length=1)
    launcher_path: Path

    @model_validator(mode="after")
    def check_launcher_path(self) -> "RuntimeProfile":
        if self.launcher_path == Path("abq2024"):
            return self
        if not self.launcher_path.is_absolute():
            raise ValueError("launcher_path must be absolute")
        if self.launcher_path.resolve() != self.launcher_path:
            raise ValueError("launcher_path must be resolved")
        return self


class CapabilityDefinitionBinding(_StrictModel):
    capability_id: str = Field(min_length=1)
    definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RuntimeScriptPolicy(_StrictModel):
    allowed_cae_scripts: tuple[str, ...] | None = None
    allowed_abaqus_python_scripts: tuple[str, ...] | None = None
    allowed_solver_inputs: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def check_relative_paths(self) -> "RuntimeScriptPolicy":
        fields = (
            ("allowed_cae_scripts", self.allowed_cae_scripts, ".py"),
            (
                "allowed_abaqus_python_scripts",
                self.allowed_abaqus_python_scripts,
                ".py",
            ),
            ("allowed_solver_inputs", self.allowed_solver_inputs, ".inp"),
        )
        for field_name, values, suffix in fields:
            if values is None:
                continue
            normalized = tuple(
                _runtime_policy_path(value, suffix=suffix) for value in values
            )
            if len({value.casefold() for value in normalized}) != len(normalized):
                raise ValueError(f"{field_name} must not contain duplicate paths")
            object.__setattr__(self, field_name, normalized)
        return self


def _runtime_policy_path(value: str, *, suffix: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    if (
        not normalized
        or normalized == "."
        or path.is_absolute()
        or bool(windows_path.drive)
        or ".." in path.parts
    ):
        raise ValueError("runtime policy paths must stay relative to the workspace")
    if path.suffix.casefold() != suffix:
        raise ValueError(f"runtime policy paths must use the {suffix} suffix")
    return path.as_posix()


class ApiCatalogBinding(_StrictModel):
    schema_version: str = Field(min_length=1)
    cards_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    card_count: int = Field(ge=0)
    runtime_release: str = Field(min_length=1)
    member_count: int = Field(default=0, ge=0)
    members_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def check_member_binding(self) -> "ApiCatalogBinding":
        if self.member_count and self.members_sha256 is None:
            raise ValueError("member catalog binding requires the sidecar digest")
        if self.schema_version == "1" and (self.member_count or self.members_sha256):
            raise ValueError("member catalog binding requires schema version 2")
        if self.schema_version == "2" and self.members_sha256 is None:
            raise ValueError("schema version 2 requires a member sidecar digest")
        return self


class ConceptIndexBinding(_StrictModel):
    schema_version: str = Field(min_length=1)
    release: str = Field(min_length=1)
    collection_name: str = Field(min_length=1)
    document_count: int = Field(ge=0)
    dense_manifest_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    dense_vector_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    bm25_manifest_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    bm25_database_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    semantic_corpus_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class StaticPreflightBinding(_StrictModel):
    schema_version: Literal["2"] = "2"
    policy_version: Literal["abqpy-runtime-feedback-v1"] = "abqpy-runtime-feedback-v1"
    engine: Literal["basedpyright"] = "basedpyright"
    engine_version: str = Field(min_length=1)
    api_source: Literal["abqpy"] = "abqpy"
    api_source_version: str = Field(min_length=1)
    api_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ApiDeliveredParameter(_StrictModel):
    card_id: str = Field(min_length=1)
    parameter_name: str = Field(min_length=1)


class ApiLookupTrace(_StrictModel):
    source: Literal["active", "automatic"]
    query: str
    status: str = Field(min_length=1)
    card_ids: tuple[str, ...] = ()
    delivered_card_ids: tuple[str, ...] = ()
    delivered_parameters: tuple[ApiDeliveredParameter, ...] = ()
    delivered_note_ids: tuple[str, ...] = ()
    counted: bool
    payload_bytes: int = Field(ge=0)


class ApiDiscoveryTrace(_StrictModel):
    receiver: str | None = None
    query: str | None = None
    limit: int = Field(ge=1, le=100)
    status: str = Field(min_length=1)
    candidate_card_ids: tuple[str, ...] = ()
    candidate_names: tuple[str, ...] = ()
    counted: bool
    payload_bytes: int = Field(ge=0)


ConceptTriggerSource = Literal[
    "task_start",
    "task_revision",
    "clarification",
    "cae",
    "solver",
    "abaqus_python",
    "verification",
    "probe",
    "resume",
    "unknown",
]
ConceptTriggerOutcome = Literal[
    "none",
    "success",
    "failure",
    "feedback",
    "interrupted",
    "unknown",
]


class ConceptLookupContext(_StrictModel):
    recorded_at: datetime | None = None
    turn: int | None = Field(default=None, ge=1)
    amendment_sequence: int = Field(default=0, ge=0)
    task_revision: int = Field(default=0, ge=0)
    tool_call_id: str | None = Field(default=None, min_length=1)
    trigger_source: ConceptTriggerSource = "unknown"
    trigger_outcome: ConceptTriggerOutcome = "unknown"
    trigger_reference: str | None = Field(default=None, min_length=1)
    trigger_evidence_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    execution_count_before_lookup: int = Field(default=0, ge=0)


class ConceptLookupHitTrace(_StrictModel):
    rank: int = Field(ge=1)
    document_id: str = Field(min_length=1)
    match: Literal["exact", "semantic", "bm25", "hybrid"]
    section_id: str | None = None


class AbaqusDocQuestion(_StrictModel):
    question: str = Field(
        min_length=1,
        max_length=2_000,
        description="需要官方文档确认的一个技术问题；保留相关 API、单元和输出变量名。",
    )
    observed_context: str | None = Field(
        default=None,
        max_length=4_000,
        description="与问题相关的已有模型条件或原始诊断；不要补造未知物理输入。",
    )

    @field_validator("question")
    @classmethod
    def check_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("a documentation question is required")
        return value

    @field_validator("observed_context")
    @classmethod
    def strip_context(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class AbaqusDocEvidence(_StrictModel):
    document_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    title: str
    section: str
    excerpt: str = Field(max_length=DOCUMENT_BODY_BUDGET)
    original_characters: int = Field(ge=0)
    truncated: bool
    truncation_reason: Literal["body_budget"] | None
    delivery_version: Literal["indexed-passage-body-budget-v1"]
    source: str
    release: str = Field(min_length=1)
    match: Literal["exact", "semantic"]
    heading_path: tuple[str, ...] = ()
    page_products: tuple[str, ...] = ()
    page_products_source: Literal[
        "official_page_header", "document_id_inference", "unknown", "unrecorded"
    ] = "unrecorded"
    section_product_mentions: tuple[str, ...] = ()
    section_product_heading: str | None = None

    @model_validator(mode="after")
    def check_excerpt_completeness(self) -> "AbaqusDocEvidence":
        if len(self.excerpt) > self.original_characters:
            raise ValueError("returned body exceeds original length")
        if self.truncated != (len(self.excerpt) < self.original_characters):
            raise ValueError("truncated flag must match original body length")
        if (self.truncation_reason is not None) != self.truncated:
            raise ValueError("truncation reason must match truncated flag")
        return self


class AbaqusDocEvidenceBundle(_StrictModel):
    status: Literal["found", "not_found", "duplicate", "unavailable", "error"]
    question: str
    observed_context: str | None = None
    query: str
    evidence: tuple[AbaqusDocEvidence, ...] = Field(default=(), max_length=4)
    notice: str
    error: str | None = None
    error_type: str | None = None

    @model_validator(mode="after")
    def check_evidence_status(self) -> "AbaqusDocEvidenceBundle":
        if (self.status == "found") != bool(self.evidence):
            raise ValueError("found status must match non-empty candidate evidence")
        if sum(len(item.excerpt) for item in self.evidence) > DOCUMENT_BODY_BUDGET:
            raise ValueError("document evidence exceeds shared body budget")
        return self


class ConceptLookupTrace(_StrictModel):
    tool_name: Literal["lookup_abaqus_concept", "search_abaqus_docs"] = (
        "lookup_abaqus_concept"
    )
    question: str | None = None
    observed_context: str | None = None
    formulation_version: str | None = None
    dedupe_key: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    index: ConceptIndexBinding | None = None
    query: str = Field(min_length=1)
    exact_terms: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    status: str = Field(min_length=1)
    document_ids: tuple[str, ...] = ()
    ranked_hits: tuple[ConceptLookupHitTrace, ...] = ()
    counted: bool
    payload_bytes: int = Field(ge=0)
    context: ConceptLookupContext = Field(default_factory=ConceptLookupContext)

    @model_validator(mode="after")
    def check_document_tool_trace(self) -> "ConceptLookupTrace":
        if self.tool_name == "search_abaqus_docs":
            if not self.question or not self.formulation_version or not self.dedupe_key:
                raise ValueError(
                    "document lookup requires its question and query binding"
                )
            if self.status not in {
                "found",
                "not_found",
                "duplicate",
                "unavailable",
                "error",
            }:
                raise ValueError("invalid document lookup status")
            if self.counted != (self.status in {"found", "not_found", "error"}):
                raise ValueError("document lookup counted flag disagrees with status")
            if (self.status == "found") != bool(self.ranked_hits):
                raise ValueError("found document lookup requires candidate hits")
        return self


class DiagnosticRetrievalIndexTrace(_StrictModel):
    release: str = Field(min_length=1)
    collection_name: str = Field(min_length=1)
    dense_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dense_vector_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    bm25_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bm25_database_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DiagnosticRetrievalHitTrace(_StrictModel):
    rank: int = Field(ge=1)
    document_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    section_title: str = Field(min_length=1)
    match: Literal["hybrid"] = "hybrid"


class DiagnosticRetrievalTrace(_StrictModel):
    schema_version: Literal["diagnostic-retrieval-trace-v1"] = (
        "diagnostic-retrieval-trace-v1"
    )
    run_id: str = Field(min_length=1)
    recorded_at: datetime
    turn: int | None = Field(default=None, ge=1)
    amendment_sequence: int = Field(ge=0)
    task_revision: int = Field(ge=0)
    execution_index: int = Field(ge=0)
    diagnostic_level: Literal["error", "warning"]
    diagnostic_source: str = Field(min_length=1)
    diagnostic_line: int = Field(ge=1)
    diagnostic_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formulation_version: str = Field(min_length=1)
    query: str = Field(min_length=1)
    query_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retriever: Literal["rrf"] = "rrf"
    top_k: Literal[4] = 4
    fetch_k: Literal[20] = 20
    rrf_k: Literal[60] = 60
    index: DiagnosticRetrievalIndexTrace | None = None
    status: Literal["found", "not_found", "duplicate", "unavailable", "error"]
    counted: bool
    ranked_hits: tuple[DiagnosticRetrievalHitTrace, ...] = ()
    evidence_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=0)
    elapsed_ms: float = Field(ge=0)
    error_type: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def check_status_evidence(self) -> "DiagnosticRetrievalTrace":
        attempted = self.status in {"found", "not_found", "error"}
        if self.counted != attempted:
            raise ValueError("diagnostic retrieval counted flag disagrees with status")
        if attempted and self.index is None:
            raise ValueError("attempted diagnostic retrieval requires index binding")
        if (self.status == "found") != bool(self.ranked_hits):
            raise ValueError("found status must match non-empty ranked hits")
        if self.status in {"found", "not_found"} and self.evidence_id is None:
            raise ValueError("successful retrieval attempt requires evidence_id")
        if self.status == "error" and self.error_type is None:
            raise ValueError("error status requires error_type")
        return self


ExecutionPurpose = Literal["production", "diagnostic"]
ProbeConclusionStatus = Literal["validated", "rejected", "inconclusive"]


class ProbeResultInput(_StrictModel):
    status: ProbeConclusionStatus
    claim: str = Field(min_length=1, max_length=2_000)
    evidence: str = Field(min_length=1, max_length=4_000)
    target_script: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def check_target_semantics(self) -> "ProbeResultInput":
        if self.status == "validated" and self.target_script is None:
            raise ValueError("validated probe requires target_script")
        if self.status != "validated" and self.target_script is not None:
            raise ValueError(
                "rejected or inconclusive probe must not declare a target script"
            )
        return self


class ProbeConclusionTrace(_StrictModel):
    event: Literal["conclusion"] = "conclusion"
    probe_id: str = Field(pattern=r"^probe-[0-9]{3,}$")
    diagnostic_script: str = Field(min_length=1)
    invocation: Literal["cae_nogui", "abaqus_python"]
    runtime_stage_succeeded: bool
    status: ProbeConclusionStatus
    claim: str = Field(min_length=1, max_length=2_000)
    target_script: str | None = Field(default=None, min_length=1)
    evidence: str = Field(min_length=1, max_length=4_000)
    target_sha256_before: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def check_validated_target(self) -> "ProbeConclusionTrace":
        if self.status == "validated":
            if not self.runtime_stage_succeeded:
                raise ValueError("validated probe requires a successful runtime stage")
            if self.target_script is None:
                raise ValueError("validated probe requires target_script")
        elif self.target_script is not None or self.target_sha256_before is not None:
            raise ValueError(
                "rejected or inconclusive probe must not declare a target script"
            )
        return self


class ProbeAdoptionTrace(_StrictModel):
    event: Literal["adoption"] = "adoption"
    probe_id: str = Field(pattern=r"^probe-[0-9]{3,}$")
    target_script: str = Field(min_length=1)
    target_sha256_before: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    target_sha256_after: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    production_invocation: Literal["cae_nogui", "abaqus_python"]
    production_stage_succeeded: bool


ProbeTrace = Annotated[
    ProbeConclusionTrace | ProbeAdoptionTrace,
    Field(discriminator="event"),
]


class CodingRunRequest(_StrictModel):
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    goal: str = Field(min_length=1)
    workspace: Path
    state_dir: Path
    runtime: RuntimeProfile
    runtime_script_policy: RuntimeScriptPolicy = Field(
        default_factory=RuntimeScriptPolicy
    )
    api_catalog: ApiCatalogBinding | None = None
    concept_index: ConceptIndexBinding | None = None
    document_policy: DocumentPolicyBinding | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    static_preflight: StaticPreflightBinding | None = None
    edit_scope: ReferenceEditScope | None = None
    clarification_policy_version: Literal["physics-materiality-clarification-v1"] = (
        CLARIFICATION_POLICY_VERSION
    )
    limits: CodingRunLimits = Field(default_factory=CodingRunLimits)
    runtime_policy: RuntimeExecutionPolicy = Field(
        default_factory=RuntimeExecutionPolicy
    )
    evaluation_tool_limits: EvaluationToolLimits | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def check_resolved_paths(self) -> "CodingRunRequest":
        workspace = self.workspace
        state_dir = self.state_dir
        if not workspace.is_absolute():
            raise ValueError("workspace must be absolute")
        if not state_dir.is_absolute():
            raise ValueError("state_dir must be absolute")
        if workspace.resolve() != workspace:
            raise ValueError("workspace must be resolved")
        if state_dir.resolve() != state_dir:
            raise ValueError("state_dir must be resolved")
        if state_dir == workspace or state_dir.is_relative_to(workspace):
            raise ValueError("state_dir must be outside workspace")
        if self.edit_scope is not None and self.static_preflight is not None:
            raise ValueError(
                "pure edit scope does not compose with runtime static preflight"
            )
        if self.document_policy is not None:
            spec = self.document_policy.spec
            for path in (
                spec.candidate,
                spec.symbol_index,
                spec.scope_rules,
                spec.ranker_directory,
            ):
                if path.is_relative_to(workspace) or path.is_relative_to(state_dir):
                    raise ValueError(
                        "document policy sources must be outside workspace and state"
                    )
        if (
            self.api_catalog is not None
            and self.api_catalog.runtime_release != self.runtime.version
        ):
            raise ValueError(
                "api_catalog runtime_release must match the selected Abaqus runtime"
            )
        return self


ClarificationKind = Literal[
    "missing_physical_input",
    "ambiguous_physical_intent",
    "conflicting_user_constraint",
]


class ClarificationRequest(_StrictModel):
    field_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    kind: ClarificationKind
    question: str = Field(min_length=1)
    impact: str = Field(min_length=1)


class ClarificationAnswer(_StrictModel):
    field_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    value: str = Field(min_length=1)
    source: Literal["console", "scripted"]


class ClarificationEvent(_StrictModel):
    field_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    question: str = Field(min_length=1)
    kind: ClarificationKind
    impact: str = Field(min_length=1)
    answer: str | None = Field(default=None, min_length=1)
    source: Literal["console", "scripted"] | None = None
    abaqus_calls_before_answer: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def check_answer_source(self) -> "ClarificationEvent":
        if (self.answer is None) != (self.source is None):
            raise ValueError(
                "answer and source must either both be set or both be absent"
            )
        return self


class NeedsInputDecision(_StrictModel):
    status: Literal["needs_input"] = "needs_input"
    clarification: ClarificationRequest
    summary: str = Field(min_length=1)


class DeliverableDeclaration(_StrictModel):
    relative_path: str = Field(min_length=1)
    role: str | None = Field(
        default=None, min_length=1, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def check_relative_path(self) -> "DeliverableDeclaration":
        object.__setattr__(
            self,
            "relative_path",
            _workspace_relative_path(self.relative_path),
        )
        return self


class ReadyForVerificationDecision(_StrictModel):
    status: Literal["ready_for_verification"] = "ready_for_verification"
    deliverables: tuple[DeliverableDeclaration, ...] = Field(min_length=1)
    summary: str = Field(min_length=1)

    @field_validator("deliverables", mode="before")
    @classmethod
    def normalize_deliverables(
        cls,
        value: object,
    ) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def check_unique_deliverables(self) -> "ReadyForVerificationDecision":
        paths = [item.relative_path.casefold() for item in self.deliverables]
        if len(paths) != len(set(paths)):
            raise ValueError("deliverables must not contain duplicate paths")
        return self


class ResponseDecision(_StrictModel):
    status: Literal["response"] = "response"
    summary: str = Field(min_length=1)

    @field_validator("summary")
    @classmethod
    def nonblank_response(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("response must not be blank")
        return value


class FailedDecision(_StrictModel):
    status: Literal["failed"] = "failed"
    failure_stage: str = Field(min_length=1)
    summary: str = Field(min_length=1)


CodingRunDecision = Annotated[
    NeedsInputDecision | ReadyForVerificationDecision | FailedDecision,
    Field(discriminator="status"),
]


class ArtifactKind(StrEnum):
    SCRIPT = "script"
    CAE = "cae"
    INPUT = "inp"
    ODB = "odb"
    RESULTS = "results"
    LOG = "log"
    OTHER = "other"


class ArtifactRef(_StrictModel):
    relative_path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: ArtifactKind
    fresh: bool

    @model_validator(mode="after")
    def check_relative_path(self) -> "ArtifactRef":
        object.__setattr__(
            self,
            "relative_path",
            _workspace_relative_path(self.relative_path),
        )
        return self


def _workspace_relative_path(value: str) -> str:
    raw_path = value.replace("\\", "/")
    path = PurePosixPath(raw_path)
    windows_path = PureWindowsPath(raw_path)
    if (
        path.is_absolute()
        or bool(windows_path.drive)
        or ".." in path.parts
        or raw_path in {"", "."}
    ):
        raise ValueError("relative_path must stay within the workspace")
    return path.as_posix()


class CodingRunUsage(_StrictModel):
    model_requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    provider_details: dict[str, int] = Field(default_factory=dict)
    tool_calls: int = Field(default=0, ge=0)
    api_lookup_calls: int = Field(default=0, ge=0)
    concept_lookup_calls: int = Field(default=0, ge=0)
    diagnostic_retrieval_calls: int = Field(default=0, ge=0)
    syntax_edit_checks: int = Field(default=0, ge=0)
    syntax_runtime_checks: int = Field(default=0, ge=0)
    syntax_denials: int = Field(default=0, ge=0)
    static_preflight_calls: int = Field(default=0, ge=0)
    abaqus_calls: int = Field(default=0, ge=0)
    solver_calls: int = Field(default=0, ge=0)
    wall_time_seconds: float = Field(default=0.0, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @model_validator(mode="after")
    def check_solver_call_count(self) -> "CodingRunUsage":
        if self.solver_calls > self.abaqus_calls:
            raise ValueError("solver_calls cannot exceed abaqus_calls")
        return self


class CodingRunStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class CodingRunOutcome(_StrictModel):
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    task_revision: int = Field(default=0, ge=0)
    amendment_sequence: int = Field(default=0, ge=0)
    status: CodingRunStatus
    summary: str = Field(min_length=1)
    usage: CodingRunUsage = Field(default_factory=CodingRunUsage)
    artifacts: tuple[ArtifactRef, ...] = ()
    clarifications: tuple[ClarificationEvent, ...] = ()
    verification_scope: VerificationScope | None = None
    completion_kind: Literal["deliverables", "response"] = Field(
        default="deliverables", exclude_if=lambda v: v == "deliverables"
    )
    failure_stage: str | None = None

    @model_validator(mode="after")
    def check_failure_stage(self) -> "CodingRunOutcome":
        if self.completion_kind == "response" and (
            self.status is not CodingRunStatus.COMPLETED
            or self.verification_scope is not None
            or self.artifacts
        ):
            raise ValueError(
                "a response completion has no artifacts or verification scope"
            )
        if self.status is CodingRunStatus.FAILED and not self.failure_stage:
            raise ValueError("failure_stage is required for a failed outcome")
        if self.status is CodingRunStatus.COMPLETED and self.failure_stage is not None:
            raise ValueError("a completed outcome cannot have failure_stage")
        return self
