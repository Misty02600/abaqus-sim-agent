from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import ModelMessagesTypeAdapter, RunUsage
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai_harness.step_persistence import (
    FileStepStore,
    StepStore,
    ToolEffectRecord,
    continue_run,
)

from abaqus_sim_agent.coding.amendments import (
    AMENDMENT_LEDGER_NAME,
    EffectiveCodingRun,
    load_amendment_ledger,
    resolve_effective_run,
)
from abaqus_sim_agent.coding.contracts import (
    API_DISCOVERY_JOURNAL_NAME,
    API_LOOKUP_JOURNAL_NAME,
    DIAGNOSTIC_RETRIEVAL_JOURNAL_NAME,
    ApiDiscoveryTrace,
    ApiLookupTrace,
    ClarificationEvent,
    CodingRunRequest,
    ConceptLookupTrace,
    ConceptTriggerOutcome,
    ConceptTriggerSource,
    DiagnosticRetrievalTrace,
)
from abaqus_sim_agent.coding.deferred_runtime import DEFERRED_TOOL_FRONTIER_NAME
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.document_policy_contracts import PolicyCheckpoint
from abaqus_sim_agent.coding.domain_knowledge import (
    DOMAIN_KNOWLEDGE_STATE_DIRECTORY,
)
from abaqus_sim_agent.coding.persistence import write_text_atomic
from abaqus_sim_agent.coding.static_preflight import StaticPreflightReport
from abaqus_sim_agent.coding.workspace import WorkspaceSnapshot
from abaqus_sim_agent.execution import AbaqusExecutionResult

RESUME_STATE_SCHEMA_VERSION = "coding-run-resume-v3"
STEP_PERSISTENCE_DIRECTORY = "step-persistence"
EXECUTION_JOURNAL_NAME = "execution_results.jsonl"
RUNTIME_TOOL_NAMES = frozenset(
    {"run_cae_script", "run_solver_job", "solver_job", "run_abaqus_python"}
)
_OWNED_RUN_STATE_FILES = (
    "edit_scope_state.json",
    AMENDMENT_LEDGER_NAME,
    "launch.json",
    "request.json",
    "initial_snapshot.json",
    "resume_state.json",
    DEFERRED_TOOL_FRONTIER_NAME,
    "user_questions.json",
    "document_policy.jsonl",
    "document_policy.head.json",
)
_OWNED_RUN_STATE_DIRECTORIES = (
    STEP_PERSISTENCE_DIRECTORY,
    DOMAIN_KNOWLEDGE_STATE_DIRECTORY,
    "document-evidence",
    "document-validation",
    "user-questions",
)

ResumeStatus = Literal[
    "waiting_for_user",
    "waiting_for_tool_execution",
    "running",
    "interrupted",
    "needs_reconcile",
    "completed",
    "failed",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RunUsageSnapshot(_StrictModel):
    requests: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    input_audio_tokens: int = Field(default=0, ge=0)
    cache_audio_read_tokens: int = Field(default=0, ge=0)
    output_audio_tokens: int = Field(default=0, ge=0)
    details: dict[str, int] = Field(default_factory=dict)
    cost: str | None = None

    @classmethod
    def capture(cls, usage: RunUsage) -> RunUsageSnapshot:
        return cls(
            requests=usage.requests,
            tool_calls=usage.tool_calls,
            input_tokens=usage.input_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            output_tokens=usage.output_tokens,
            input_audio_tokens=usage.input_audio_tokens,
            cache_audio_read_tokens=usage.cache_audio_read_tokens,
            output_audio_tokens=usage.output_audio_tokens,
            details=dict(usage.details),
            cost=str(usage.cost) if usage.cost is not None else None,
        )

    def restore(self) -> RunUsage:
        return RunUsage(
            requests=self.requests,
            tool_calls=self.tool_calls,
            input_tokens=self.input_tokens,
            cache_write_tokens=self.cache_write_tokens,
            cache_read_tokens=self.cache_read_tokens,
            output_tokens=self.output_tokens,
            input_audio_tokens=self.input_audio_tokens,
            cache_audio_read_tokens=self.cache_audio_read_tokens,
            output_audio_tokens=self.output_audio_tokens,
            details=dict(self.details),
            cost=Decimal(self.cost) if self.cost is not None else None,
        )


class ConceptLookupKeySnapshot(_StrictModel):
    query: str
    exact_terms: tuple[str, ...] = ()
    products: tuple[str, ...] = ()


class ApiParameterKeySnapshot(_StrictModel):
    card_id: str
    parameter_name: str


class StaticPreflightCacheSnapshot(_StrictModel):
    kind: str
    relative_path: str
    script_sha256: str
    report: StaticPreflightReport
    acknowledged: bool = False


class DependencyResumeState(_StrictModel):
    api_lookup_calls: int = Field(default=0, ge=0)
    concept_lookup_calls: int = Field(default=0, ge=0)
    diagnostic_retrieval_calls: int = Field(default=0, ge=0)
    api_lookup_queries: tuple[str, ...] = ()
    api_discovery_queries: tuple[str, ...] = ()
    concept_lookup_queries: tuple[ConceptLookupKeySnapshot, ...] = ()
    diagnostic_retrieval_keys: tuple[str, ...] = ()
    returned_api_card_ids: tuple[str, ...] = ()
    returned_api_parameter_keys: tuple[ApiParameterKeySnapshot, ...] = ()
    abaqus_calls: int = Field(default=0, ge=0)
    solver_calls: int = Field(default=0, ge=0)
    executions: tuple[AbaqusExecutionResult, ...] = ()
    static_preflight_calls: int = Field(default=0, ge=0)
    static_preflight_cache: tuple[StaticPreflightCacheSnapshot, ...] = ()
    syntax_checks: int = Field(default=0, ge=0)
    syntax_edit_checks: int = Field(default=0, ge=0)
    syntax_runtime_checks: int = Field(default=0, ge=0)
    syntax_denials: int = Field(default=0, ge=0)
    probe_protocol_active: bool = False
    concept_trigger_source: ConceptTriggerSource = "unknown"
    concept_trigger_outcome: ConceptTriggerOutcome = "unknown"
    concept_trigger_reference: str | None = None
    concept_trigger_evidence_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @classmethod
    def capture(cls, dependencies: CodingAgentDependencies) -> DependencyResumeState:
        acknowledged = dependencies.acknowledged_static_preflights
        cache = tuple(
            StaticPreflightCacheSnapshot(
                kind=key[0],
                relative_path=key[1],
                script_sha256=key[2],
                report=report,
                acknowledged=key in acknowledged,
            )
            for key, report in sorted(
                dependencies.static_preflight_reports.items(),
                key=lambda item: item[0],
            )
        )
        syntax_gate = dependencies.syntax_gate
        return cls(
            api_lookup_calls=dependencies.api_lookup_calls,
            concept_lookup_calls=dependencies.concept_lookup_calls,
            diagnostic_retrieval_calls=dependencies.diagnostic_retrieval_calls,
            api_lookup_queries=tuple(sorted(dependencies.api_lookup_queries)),
            api_discovery_queries=tuple(sorted(dependencies.api_discovery_queries)),
            concept_lookup_queries=tuple(
                ConceptLookupKeySnapshot(
                    query=query,
                    exact_terms=exact_terms,
                    products=products,
                )
                for query, exact_terms, products in sorted(
                    dependencies.concept_lookup_queries
                )
            ),
            diagnostic_retrieval_keys=tuple(
                sorted(dependencies.diagnostic_retrieval_keys)
            ),
            returned_api_card_ids=tuple(sorted(dependencies.returned_api_card_ids)),
            returned_api_parameter_keys=tuple(
                ApiParameterKeySnapshot(
                    card_id=card_id,
                    parameter_name=parameter_name,
                )
                for card_id, parameter_name in sorted(
                    dependencies.returned_api_parameter_keys
                )
            ),
            abaqus_calls=dependencies.abaqus_calls,
            solver_calls=dependencies.solver_calls,
            executions=tuple(dependencies.executions),
            static_preflight_calls=dependencies.static_preflight_calls,
            static_preflight_cache=cache,
            syntax_checks=syntax_gate.checks if syntax_gate is not None else 0,
            syntax_edit_checks=(
                syntax_gate.edit_checks if syntax_gate is not None else 0
            ),
            syntax_runtime_checks=(
                syntax_gate.runtime_checks if syntax_gate is not None else 0
            ),
            syntax_denials=syntax_gate.denials if syntax_gate is not None else 0,
            probe_protocol_active=dependencies.probe_protocol is not None,
            concept_trigger_source=dependencies.concept_trigger_source,
            concept_trigger_outcome=dependencies.concept_trigger_outcome,
            concept_trigger_reference=dependencies.concept_trigger_reference,
            concept_trigger_evidence_sha256=(
                dependencies.concept_trigger_evidence_sha256
            ),
        )

    def restore_into(
        self,
        dependencies: CodingAgentDependencies,
        *,
        journaled_executions: tuple[AbaqusExecutionResult, ...] = (),
        journaled_api_lookups: tuple[ApiLookupTrace, ...] = (),
        journaled_api_discoveries: tuple[ApiDiscoveryTrace, ...] = (),
        journaled_diagnostic_retrievals: tuple[DiagnosticRetrievalTrace, ...] = (),
        journaled_document_lookups: tuple[ConceptLookupTrace, ...] = (),
    ) -> None:
        """把已持久化的宿主计数和证据恢复到新建的 run dependencies。

        Args:
            dependencies: 当前进程刚完成 composition 的依赖对象。
            journaled_executions: 工具完成时立即追加的执行结果，用于补上宿主
                在下一节点快照前崩溃造成的间隙。
            journaled_api_lookups: 精确 API 查证的已落盘预算、去重和交付证据。
            journaled_api_discoveries: API 方法目录发现的已落盘预算与去重证据。
            journaled_diagnostic_retrievals: 自动诊断检索的已落盘计数与去重证据。
            journaled_document_lookups: 主动文档查询的已落盘计数与去重证据。

        Raises:
            CodingRunResumeError: 上一次运行启用了尚不能安全重建的 typed probe
                状态，或当前依赖对象已经被使用。
        """
        if self.probe_protocol_active:
            raise CodingRunResumeError(
                "typed probe protocol state cannot yet be resumed safely"
            )
        if _dependencies_have_runtime_state(dependencies):
            raise CodingRunResumeError(
                "resume requires a freshly composed CodingAgentDependencies object"
            )
        dependencies.api_lookup_calls = max(
            self.api_lookup_calls,
            sum(trace.counted for trace in journaled_api_lookups)
            + sum(trace.counted for trace in journaled_api_discoveries),
        )
        dependencies.concept_lookup_calls = max(
            self.concept_lookup_calls,
            sum(trace.counted for trace in journaled_document_lookups),
        )
        dependencies.diagnostic_retrieval_calls = max(
            self.diagnostic_retrieval_calls,
            sum(trace.counted for trace in journaled_diagnostic_retrievals),
        )
        dependencies.api_lookup_queries = {
            *self.api_lookup_queries,
            *(trace.query for trace in journaled_api_lookups if trace.query),
        }
        dependencies.api_discovery_queries = {
            *self.api_discovery_queries,
            *(
                _api_discovery_dedupe_key(trace)
                for trace in journaled_api_discoveries
                if trace.receiver is not None
            ),
        }
        dependencies.concept_lookup_queries = {
            (item.query, item.exact_terms, item.products)
            for item in self.concept_lookup_queries
        }
        dependencies.concept_lookup_queries.update(
            (trace.dedupe_key, (), ())
            for trace in journaled_document_lookups
            if trace.counted and trace.dedupe_key is not None
        )
        dependencies.diagnostic_retrieval_keys = {
            *self.diagnostic_retrieval_keys,
            *(
                f"{trace.task_revision}:{trace.diagnostic_sha256}"
                for trace in journaled_diagnostic_retrievals
                if trace.counted
            ),
        }
        dependencies.returned_api_card_ids = {
            *self.returned_api_card_ids,
            *(
                card_id
                for trace in journaled_api_lookups
                for card_id in trace.delivered_card_ids
            ),
        }
        dependencies.returned_api_parameter_keys = {
            (item.card_id, item.parameter_name)
            for item in self.returned_api_parameter_keys
        }
        dependencies.returned_api_parameter_keys.update(
            (item.card_id, item.parameter_name)
            for trace in journaled_api_lookups
            for item in trace.delivered_parameters
        )
        merged_executions = _merge_executions(
            self.executions,
            journaled_executions,
        )
        dependencies.executions = merged_executions
        dependencies.abaqus_calls = max(self.abaqus_calls, len(merged_executions))
        dependencies.solver_calls = max(
            self.solver_calls,
            sum(
                result.invocation.value == "solver_job" for result in merged_executions
            ),
        )
        dependencies.concept_trigger_source = self.concept_trigger_source
        dependencies.concept_trigger_outcome = self.concept_trigger_outcome
        dependencies.concept_trigger_reference = self.concept_trigger_reference
        dependencies.concept_trigger_evidence_sha256 = (
            self.concept_trigger_evidence_sha256
        )
        if len(merged_executions) > len(self.executions):
            dependencies.restore_runtime_execution_trigger(
                merged_executions[-1],
                execution_index=len(merged_executions) - 1,
            )
        dependencies.static_preflight_calls = self.static_preflight_calls
        dependencies.static_preflight_reports = {
            (item.kind, item.relative_path, item.script_sha256): item.report
            for item in self.static_preflight_cache
        }
        dependencies.acknowledged_static_preflights = {
            (item.kind, item.relative_path, item.script_sha256)
            for item in self.static_preflight_cache
            if item.acknowledged
        }
        syntax_gate = dependencies.syntax_gate
        if syntax_gate is not None:
            syntax_gate.checks = self.syntax_checks
            syntax_gate.edit_checks = self.syntax_edit_checks
            syntax_gate.runtime_checks = self.syntax_runtime_checks
            syntax_gate.denials = self.syntax_denials


class CodingRunResumeState(_StrictModel):
    schema_version: Literal[
        "coding-run-resume-v3", "coding-run-resume-v4", "coding-run-resume-v5"
    ] = RESUME_STATE_SCHEMA_VERSION
    question_protocol: Literal["ask_user_v1"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    pending_question_id: str | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    document_policy_checkpoint: PolicyCheckpoint | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    run_id: str
    status: ResumeStatus
    turn: int = Field(ge=0)
    amendment_sequence: int = Field(default=0, ge=0)
    task_revision: int = Field(default=0, ge=0)
    continuation_id: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    revision_workspace_snapshot: WorkspaceSnapshot
    revision_execution_start_index: int = Field(default=0, ge=0)
    active_wall_time_seconds: float = Field(ge=0)
    workspace_snapshot: WorkspaceSnapshot
    usage: RunUsageSnapshot
    clarifications: tuple[ClarificationEvent, ...] = ()
    dependencies: DependencyResumeState
    last_error_type: str | None = None
    last_error_message: str | None = None
    updated_at: datetime

    @model_validator(mode="after")
    def document_checkpoint_version(self):
        if self.schema_version == "coding-run-resume-v5":
            if (
                self.question_protocol != "ask_user_v1"
                or self.document_policy_checkpoint is not None
            ):
                raise ValueError("resume v5 requires the supported ask_user profile")
            if self.status == "waiting_for_user" and self.pending_question_id is None:
                raise ValueError("waiting state requires its question id")
            return self
        if (
            self.question_protocol is not None
            or self.pending_question_id is not None
            or self.status == "waiting_for_user"
        ):
            raise ValueError("legacy resume cannot carry question state")
        if (self.schema_version == "coding-run-resume-v4") != (
            self.document_policy_checkpoint is not None
        ):
            raise ValueError(
                "resume v4 requires a document checkpoint; v3 must not carry one"
            )
        return self


class ResumeHistory(_StrictModel):
    messages: tuple[ModelMessage, ...] = ()
    prompt: str
    source: Literal[
        "step_complete",
        "step_interrupted",
        "history_fallback",
        "original_goal",
    ]
    prior_step_run_id: str | None = None
    unresolved_non_runtime_tools: tuple[str, ...] = ()


class CodingRunResumeError(RuntimeError):
    pass


class CodingRunWorkspaceChanged(CodingRunResumeError):
    pass


class CodingRunReconciliationRequired(CodingRunResumeError):
    def __init__(self, effects: tuple[ToolEffectRecord, ...]) -> None:
        self.effects = effects
        detail = ", ".join(
            f"{effect.tool_name}:{effect.tool_call_id}" for effect in effects
        )
        super().__init__(
            "Abaqus runtime state is unknown after interruption; reconcile before "
            f"resuming ({detail})"
        )


def create_step_store(state_dir: Path) -> FileStepStore:
    return FileStepStore(state_dir / STEP_PERSISTENCE_DIRECTORY)


def ensure_new_run_state_available(
    state_dir: Path, *, prepared_domain_knowledge: bool = False
) -> None:
    occupied = [name for name in _OWNED_RUN_STATE_FILES if (state_dir / name).exists()]
    occupied.extend(
        name
        for name in _OWNED_RUN_STATE_DIRECTORIES
        if (state_dir / name).exists()
        and not (prepared_domain_knowledge and name == DOMAIN_KNOWLEDGE_STATE_DIRECTORY)
    )
    if occupied:
        raise CodingRunResumeError(
            "state directory already contains a CodingRun; use resume instead of "
            "overwriting: " + ", ".join(occupied)
        )


def load_original_request_for_resume(state_dir: Path) -> CodingRunRequest:
    resolved = state_dir.resolve(strict=True)
    if not resolved.is_dir():
        raise CodingRunResumeError("resume state path must be a directory")
    path = resolved / "request.json"
    if not path.is_file():
        raise CodingRunResumeError("resume state has no request.json")
    request = CodingRunRequest.model_validate_json(path.read_text(encoding="utf-8"))
    if request.state_dir != resolved:
        raise CodingRunResumeError(
            "request.json state_dir does not match the selected resume directory"
        )
    return request


def load_effective_run_for_resume(state_dir: Path) -> EffectiveCodingRun:
    request = load_original_request_for_resume(state_dir)
    initial_snapshot = load_initial_snapshot(state_dir)
    ledger = load_amendment_ledger(state_dir, run_id=request.run_id)
    return resolve_effective_run(request, initial_snapshot, ledger)


def load_request_for_resume(state_dir: Path) -> CodingRunRequest:
    return load_effective_run_for_resume(state_dir).request


def load_initial_snapshot(state_dir: Path) -> WorkspaceSnapshot:
    path = state_dir / "initial_snapshot.json"
    if not path.is_file():
        raise CodingRunResumeError(
            "run predates resumable CodingRun state: initial_snapshot.json is missing"
        )
    return WorkspaceSnapshot.model_validate_json(path.read_text(encoding="utf-8"))


def load_resume_state(state_dir: Path) -> CodingRunResumeState:
    path = state_dir / "resume_state.json"
    if not path.is_file():
        raise CodingRunResumeError(
            "run predates resumable CodingRun state: resume_state.json is missing"
        )
    return CodingRunResumeState.model_validate_json(path.read_text(encoding="utf-8"))


def write_resume_state(state_dir: Path, state: CodingRunResumeState) -> None:
    write_text_atomic(state_dir / "resume_state.json", state.model_dump_json(indent=2))


def append_execution_result(
    state_dir: Path,
    result: AbaqusExecutionResult,
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / EXECUTION_JOURNAL_NAME).open(
        "a",
        encoding="utf-8",
        newline="\n",
    ) as stream:
        stream.write(result.model_dump_json())
        stream.write("\n")


def load_execution_results(state_dir: Path) -> tuple[AbaqusExecutionResult, ...]:
    path = state_dir / EXECUTION_JOURNAL_NAME
    if not path.is_file():
        return ()
    results: list[AbaqusExecutionResult] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            results.append(AbaqusExecutionResult.model_validate_json(line))
        except ValueError as exc:
            raise CodingRunResumeError(
                f"invalid execution journal record at line {line_number}: {exc}"
            ) from exc
    return tuple(results)


def load_api_lookup_traces(state_dir: Path) -> tuple[ApiLookupTrace, ...]:
    path = state_dir / API_LOOKUP_JOURNAL_NAME
    if not path.is_file():
        return ()
    traces: list[ApiLookupTrace] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            traces.append(ApiLookupTrace.model_validate_json(line))
        except ValueError as exc:
            raise CodingRunResumeError(
                f"invalid API lookup journal record at line {line_number}: {exc}"
            ) from exc
    return tuple(traces)


def load_api_discovery_traces(state_dir: Path) -> tuple[ApiDiscoveryTrace, ...]:
    path = state_dir / API_DISCOVERY_JOURNAL_NAME
    if not path.is_file():
        return ()
    traces: list[ApiDiscoveryTrace] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            traces.append(ApiDiscoveryTrace.model_validate_json(line))
        except ValueError as exc:
            raise CodingRunResumeError(
                f"invalid API discovery journal record at line {line_number}: {exc}"
            ) from exc
    return tuple(traces)


def load_diagnostic_retrieval_traces(
    state_dir: Path,
) -> tuple[DiagnosticRetrievalTrace, ...]:
    path = state_dir / DIAGNOSTIC_RETRIEVAL_JOURNAL_NAME
    if not path.is_file():
        return ()
    traces: list[DiagnosticRetrievalTrace] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            traces.append(DiagnosticRetrievalTrace.model_validate_json(line))
        except ValueError as exc:
            raise CodingRunResumeError(
                "invalid diagnostic retrieval journal record at line "
                f"{line_number}: {exc}"
            ) from exc
    return tuple(traces)


def load_document_lookup_traces(state_dir: Path) -> tuple[ConceptLookupTrace, ...]:
    path = state_dir / "concept_lookups.jsonl"
    if not path.is_file():
        return ()
    traces: list[ConceptLookupTrace] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            traces.append(ConceptLookupTrace.model_validate_json(line))
        except ValueError as exc:
            raise CodingRunResumeError(
                f"invalid document lookup journal record at line {line_number}: {exc}"
            ) from exc
    return tuple(traces)


def load_history_fallback(state_dir: Path) -> tuple[ModelMessage, ...]:
    path = state_dir / "history.json"
    if not path.is_file():
        return ()
    try:
        return tuple(ModelMessagesTypeAdapter.validate_json(path.read_bytes()))
    except ValueError as exc:
        raise CodingRunResumeError(f"invalid history.json: {exc}") from exc


async def select_resume_history(
    store: StepStore,
    request: CodingRunRequest,
    *,
    deferred_tool_effect: tuple[str, str] | None = None,
) -> ResumeHistory:
    runs = await store.list_runs(conversation_id=request.run_id)
    fallback = load_history_fallback(request.state_dir)
    if not runs:
        return _fallback_resume_history(request, fallback)

    latest_run = runs[-1]
    try:
        unresolved = tuple(
            await store.list_unresolved_tool_effects(run_id=latest_run.run_id)
        )
    except ValueError as exc:
        raise CodingRunResumeError(
            "invalid tool-effect ledger; runtime state cannot be proven safe"
        ) from exc

    def is_deferred_effect(effect: ToolEffectRecord) -> bool:
        return deferred_tool_effect == (effect.tool_name, effect.tool_call_id)

    unresolved_for_resume = tuple(
        effect for effect in unresolved if not is_deferred_effect(effect)
    )
    runtime_effects = tuple(
        effect
        for effect in unresolved_for_resume
        if effect.tool_name in RUNTIME_TOOL_NAMES
    )
    if runtime_effects:
        raise CodingRunReconciliationRequired(runtime_effects)
    unresolved_names = tuple(
        sorted({effect.tool_name for effect in unresolved_for_resume})
    )

    state_path = request.state_dir / "resume_state.json"
    if (
        fallback
        and state_path.is_file()
        and load_resume_state(request.state_dir).last_error_type == "UserStoppedError"
    ):
        # Reconcile effects first. Keep the native interrupted response for context;
        # Pydantic AI closes out its unexecuted tool calls when the next prompt arrives.
        if isinstance(fallback[-1], ModelResponse):
            fallback = (*fallback[:-1], replace(fallback[-1], state="interrupted"))
        return ResumeHistory(
            messages=fallback,
            prompt="The user explicitly resumed after stopping a model response. Preserve the partial reply as context; do not assume any unexecuted tool call completed.",
            source="history_fallback",
            prior_step_run_id=latest_run.run_id,
            unresolved_non_runtime_tools=unresolved_names,
        )

    for include_interrupted in (False, True):
        try:
            messages = tuple(
                await continue_run(
                    store,
                    run_id=latest_run.run_id,
                    include_interrupted=include_interrupted,
                )
            )
        except (LookupError, ValueError):
            continue
        if messages:
            return ResumeHistory(
                messages=messages,
                prompt=_continuation_prompt(unresolved_names),
                source=("step_interrupted" if include_interrupted else "step_complete"),
                prior_step_run_id=latest_run.run_id,
                unresolved_non_runtime_tools=unresolved_names,
            )
    return _fallback_resume_history(
        request,
        fallback,
        unresolved_tools=unresolved_names,
    )


def new_resume_state(
    request: CodingRunRequest,
    *,
    status: ResumeStatus,
    turn: int,
    amendment_sequence: int,
    task_revision: int,
    continuation_id: str | None,
    revision_workspace_snapshot: WorkspaceSnapshot,
    revision_execution_start_index: int,
    active_wall_time_seconds: float,
    workspace_snapshot: WorkspaceSnapshot,
    usage: RunUsage,
    clarifications: list[ClarificationEvent],
    dependencies: CodingAgentDependencies,
    error: BaseException | None = None,
    question_protocol: Literal["ask_user_v1"] | None = None,
    pending_question_id: str | None = None,
) -> CodingRunResumeState:
    return CodingRunResumeState(
        schema_version="coding-run-resume-v5"
        if question_protocol
        else RESUME_STATE_SCHEMA_VERSION,
        question_protocol=question_protocol,
        pending_question_id=pending_question_id,
        run_id=request.run_id,
        status=status,
        turn=turn,
        amendment_sequence=amendment_sequence,
        task_revision=task_revision,
        continuation_id=continuation_id,
        revision_workspace_snapshot=revision_workspace_snapshot,
        revision_execution_start_index=revision_execution_start_index,
        active_wall_time_seconds=active_wall_time_seconds,
        workspace_snapshot=workspace_snapshot,
        usage=RunUsageSnapshot.capture(usage),
        clarifications=tuple(clarifications),
        dependencies=DependencyResumeState.capture(dependencies),
        last_error_type=type(error).__name__ if error is not None else None,
        last_error_message=str(error) if error is not None else None,
        updated_at=datetime.now(UTC),
    )


def _fallback_resume_history(
    request: CodingRunRequest,
    history: tuple[ModelMessage, ...],
    *,
    unresolved_tools: tuple[str, ...] = (),
) -> ResumeHistory:
    history = _drop_unresolved_response(history)
    if history and any(isinstance(message, ModelResponse) for message in history):
        return ResumeHistory(
            messages=history,
            prompt=_continuation_prompt(unresolved_tools),
            source="history_fallback",
            unresolved_non_runtime_tools=unresolved_tools,
        )
    return ResumeHistory(
        prompt=request.goal,
        source="original_goal",
        unresolved_non_runtime_tools=unresolved_tools,
    )


def _drop_unresolved_response(
    history: tuple[ModelMessage, ...],
) -> tuple[ModelMessage, ...]:
    if not history or not isinstance(history[-1], ModelResponse):
        return history
    if not any(isinstance(part, ToolCallPart) for part in history[-1].parts):
        return history
    return history[:-1]


def _continuation_prompt(unresolved_tools: tuple[str, ...]) -> str:
    prompt = (
        "Continue the same coding run after an infrastructure interruption. "
        "Preserve completed work, inspect the current workspace, and perform only "
        "the remaining necessary steps."
    )
    if unresolved_tools:
        prompt += (
            " The following non-runtime tool calls may have been interrupted: "
            f"{', '.join(unresolved_tools)}. Inspect their workspace effects before "
            "deciding whether to repeat them."
        )
    return prompt


def _dependencies_have_runtime_state(
    dependencies: CodingAgentDependencies,
) -> bool:
    return any(
        (
            dependencies.api_lookup_calls,
            dependencies.concept_lookup_calls,
            dependencies.diagnostic_retrieval_calls,
            dependencies.api_lookup_queries,
            dependencies.api_discovery_queries,
            dependencies.concept_lookup_queries,
            dependencies.diagnostic_retrieval_keys,
            dependencies.returned_api_card_ids,
            dependencies.returned_api_parameter_keys,
            dependencies.abaqus_calls,
            dependencies.solver_calls,
            dependencies.executions,
            dependencies.static_preflight_calls,
            dependencies.static_preflight_reports,
            dependencies.acknowledged_static_preflights,
            dependencies.probe_protocol is not None,
        )
    )


def _api_discovery_dedupe_key(trace: ApiDiscoveryTrace) -> str:
    assert trace.receiver is not None
    return "\0".join(
        (
            trace.receiver.casefold(),
            (trace.query or "").casefold(),
            str(trace.limit),
        )
    )


def _merge_executions(
    persisted: tuple[AbaqusExecutionResult, ...],
    journaled: tuple[AbaqusExecutionResult, ...],
) -> list[AbaqusExecutionResult]:
    merged: list[AbaqusExecutionResult] = []
    seen: set[tuple[str, str]] = set()
    for result in (*persisted, *journaled):
        key = (str(result.stdout_raw_path), str(result.stderr_raw_path))
        if key in seen:
            continue
        seen.add(key)
        merged.append(result)
    return merged
