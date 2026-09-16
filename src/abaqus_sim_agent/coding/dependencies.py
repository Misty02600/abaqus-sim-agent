from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic_ai_harness.step_persistence import StepStore

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.aci import ProductionPythonSyntaxGate
from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    ConceptLookupContext,
    ConceptTriggerOutcome,
    ConceptTriggerSource,
)
from abaqus_sim_agent.coding.document_policy import (
    DocumentPolicy,
    DocumentReceiptValidator,
)
from abaqus_sim_agent.coding.document_search_port import DocumentSearchPort
from abaqus_sim_agent.coding.edit_scope import ReferenceEditGate
from abaqus_sim_agent.coding.probe import ProbeProtocolState
from abaqus_sim_agent.coding.progress import ProgressObserver
from abaqus_sim_agent.coding.static_preflight import (
    AbqpyBasedPyrightPreflight,
    StaticPreflightReport,
)
from abaqus_sim_agent.execution.abaqus2024 import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
    AbaqusInvocation,
)
from abaqus_sim_agent.retrieval import AbaqusDocumentIndex

__all__ = ["CodingAgentDependencies", "RuntimeBudgetExceeded"]


class RuntimeBudgetExceeded(RuntimeError):
    pass


@dataclass(slots=True)
class CodingAgentDependencies:
    request: CodingRunRequest
    runner: Abaqus2024Runner
    step_store: StepStore | None = None
    observer: ProgressObserver | None = None
    api_catalog: ApiCatalog | None = None
    concept_index: AbaqusDocumentIndex | None = None
    document_port: DocumentSearchPort | None = None
    document_validator: DocumentReceiptValidator | None = None
    document_policy: DocumentPolicy | None = None
    document_deadline: float = 0.0
    api_lookup_calls: int = 0
    concept_lookup_calls: int = 0
    diagnostic_retrieval_calls: int = 0
    api_lookup_queries: set[str] = field(default_factory=set)
    api_discovery_queries: set[str] = field(default_factory=set)
    concept_lookup_queries: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = field(
        default_factory=set
    )
    diagnostic_retrieval_keys: set[str] = field(default_factory=set)
    returned_api_card_ids: set[str] = field(default_factory=set)
    returned_api_parameter_keys: set[tuple[str, str]] = field(default_factory=set)
    abaqus_calls: int = 0
    solver_calls: int = 0
    executions: list[AbaqusExecutionResult] = field(default_factory=list)
    probe_protocol: ProbeProtocolState | None = None
    probe_runtime_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    static_preflight: AbqpyBasedPyrightPreflight | None = None
    static_preflight_calls: int = 0
    static_preflight_reports: dict[tuple[str, str, str], StaticPreflightReport] = field(
        default_factory=dict
    )
    acknowledged_static_preflights: set[tuple[str, str, str]] = field(
        default_factory=set
    )
    syntax_gate: ProductionPythonSyntaxGate | None = None
    edit_scope_gate: ReferenceEditGate | None = field(default=None, init=False)
    current_turn: int = 0
    current_amendment_sequence: int = 0
    current_task_revision: int = 0
    concept_trigger_source: ConceptTriggerSource = "task_start"
    concept_trigger_outcome: ConceptTriggerOutcome = "none"
    concept_trigger_reference: str | None = None
    concept_trigger_evidence_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.request.edit_scope is not None:
            self.edit_scope_gate = ReferenceEditGate(
                self.request.workspace, self.request.state_dir, self.request.edit_scope
            )
        binding = self.request.static_preflight
        if binding is None and self.static_preflight is None:
            if self.syntax_gate is not None:
                raise ValueError("syntax gate requires enforced static preflight")
            return
        if binding is None or self.static_preflight is None:
            raise ValueError(
                "static preflight request binding and runtime must be configured together"
            )
        if self.static_preflight.binding != binding:
            raise ValueError("static preflight runtime does not match request binding")
        if self.static_preflight.state_dir != self.request.state_dir:
            raise ValueError("static preflight state directory must match the request")
        if binding.mode == "enforced_verified":
            if self.syntax_gate is None:
                self.syntax_gate = ProductionPythonSyntaxGate(
                    workspace=self.request.workspace,
                    state_dir=self.request.state_dir,
                    runtime_script_policy=self.request.runtime_script_policy,
                )
        elif self.syntax_gate is not None:
            raise ValueError("hard syntax gate requires enforced_verified mode")

    def reserve_runtime_call(self, *, solver: bool) -> str | None:
        if self.request.edit_scope is not None:
            return "Pure edit scope prohibits all Abaqus execution"
        budget = self.request.budget
        if self.abaqus_calls >= budget.max_abaqus_calls:
            return "Abaqus invocation budget is exhausted"
        if solver and self.solver_calls >= budget.max_solver_calls:
            return "Abaqus solver invocation budget is exhausted"
        self.abaqus_calls += 1
        if solver:
            self.solver_calls += 1
        return None

    def runtime_script_policy_denial(
        self,
        *,
        kind: Literal["cae", "abaqus_python", "solver_input"],
        path: str,
    ) -> dict[str, object] | None:
        policy = self.request.runtime_script_policy
        allowed = {
            "cae": policy.allowed_cae_scripts,
            "abaqus_python": policy.allowed_abaqus_python_scripts,
            "solver_input": policy.allowed_solver_inputs,
        }[kind]
        if allowed is None:
            return None
        normalized = path.replace("\\", "/")
        if normalized.casefold() in {value.casefold() for value in allowed}:
            return None
        labels = {
            "cae": "CAE script",
            "abaqus_python": "Abaqus Python script",
            "solver_input": "Solver input",
        }
        return {
            "succeeded": False,
            "stage_succeeded": False,
            "blocked": True,
            "budget_consumed": False,
            "error_type": "RuntimeScriptPolicyDenied",
            "error": (
                f"{labels[kind]} {normalized!r} is outside the host allowlist; "
                "apply the evidence-backed change to an allowed production file"
            ),
            "requested_path": normalized,
            "allowed_paths": list(allowed),
        }

    def reserve_api_lookup(self) -> str | None:
        if self.api_catalog is None:
            return "Abaqus API catalog is not configured"
        if self.api_lookup_calls >= self.request.budget.max_api_lookup_calls:
            return "Abaqus API lookup budget is exhausted"
        self.api_lookup_calls += 1
        return None

    def reserve_static_preflight(self) -> str | None:
        if self.static_preflight is None:
            return "Static preflight is not configured"
        if (
            self.static_preflight_calls
            >= self.request.budget.max_static_preflight_calls
        ):
            return "Static preflight budget is exhausted"
        self.static_preflight_calls += 1
        return None

    def register_api_lookup_query(self, query: str) -> bool:
        if query in self.api_lookup_queries:
            return False
        self.api_lookup_queries.add(query)
        return True

    def register_api_discovery_query(self, query: str) -> bool:
        if query in self.api_discovery_queries:
            return False
        self.api_discovery_queries.add(query)
        return True

    def all_api_cards_returned(self, card_ids: tuple[str, ...]) -> bool:
        return bool(card_ids) and self.returned_api_card_ids.issuperset(card_ids)

    def register_returned_api_cards(self, card_ids: tuple[str, ...]) -> None:
        self.returned_api_card_ids.update(card_ids)

    def api_parameter_returned(self, card_id: str, parameter_name: str) -> bool:
        return (card_id, parameter_name) in self.returned_api_parameter_keys

    def register_returned_api_parameter(
        self,
        card_id: str,
        parameter_name: str,
    ) -> None:
        self.returned_api_parameter_keys.add((card_id, parameter_name))

    def reserve_concept_lookup(self) -> str | None:
        if self.concept_index is None:
            return "Abaqus concept document index is not configured"
        if self.concept_lookup_calls >= self.request.budget.max_concept_lookup_calls:
            return "Abaqus concept document lookup budget is exhausted"
        self.concept_lookup_calls += 1
        return None

    def reserve_diagnostic_retrieval(self) -> str | None:
        if self.concept_index is None:
            return "Abaqus diagnostic document index is not configured"
        if (
            self.diagnostic_retrieval_calls
            >= self.request.budget.max_diagnostic_retrieval_calls
        ):
            return "Abaqus diagnostic retrieval budget is exhausted"
        self.diagnostic_retrieval_calls += 1
        return None

    def register_concept_lookup_query(
        self,
        key: tuple[str, tuple[str, ...], tuple[str, ...]],
    ) -> bool:
        if key in self.concept_lookup_queries:
            return False
        self.concept_lookup_queries.add(key)
        return True

    def concept_lookup_context(
        self,
        *,
        tool_call_id: str | None,
    ) -> ConceptLookupContext:
        return ConceptLookupContext(
            recorded_at=datetime.now(UTC),
            turn=self.current_turn or None,
            amendment_sequence=self.current_amendment_sequence,
            task_revision=self.current_task_revision,
            tool_call_id=tool_call_id,
            trigger_source=self.concept_trigger_source,
            trigger_outcome=self.concept_trigger_outcome,
            trigger_reference=self.concept_trigger_reference,
            trigger_evidence_sha256=self.concept_trigger_evidence_sha256,
            execution_count_before_lookup=len(self.executions),
        )

    def set_concept_lookup_trigger(
        self,
        source: ConceptTriggerSource,
        outcome: ConceptTriggerOutcome,
        *,
        reference: str | None = None,
        evidence: str | None = None,
    ) -> None:
        self.concept_trigger_source = source
        self.concept_trigger_outcome = outcome
        self.concept_trigger_reference = reference
        self.concept_trigger_evidence_sha256 = (
            hashlib.sha256(evidence.encode("utf-8")).hexdigest()
            if evidence is not None
            else None
        )

    def record_runtime_execution(self, result: AbaqusExecutionResult) -> None:
        self.executions.append(result)
        self.restore_runtime_execution_trigger(
            result,
            execution_index=len(self.executions) - 1,
        )

    def restore_runtime_execution_trigger(
        self,
        result: AbaqusExecutionResult,
        *,
        execution_index: int,
    ) -> None:
        source: ConceptTriggerSource
        if result.invocation is AbaqusInvocation.CAE_NOGUI:
            source = "cae"
        elif result.invocation is AbaqusInvocation.SOLVER_JOB:
            source = "solver"
        else:
            source = "abaqus_python"
        self.set_concept_lookup_trigger(
            source,
            (
                "interrupted"
                if result.timed_out
                else "feedback"
                if result.stage_succeeded and result.diagnostics
                else "success"
                if result.stage_succeeded
                else "failure"
            ),
            reference=f"execution:{execution_index}",
            evidence=result.model_dump_json(),
        )

    def ensure_probe_protocol(
        self,
        allowed_target_scripts: tuple[str, ...] | None = None,
    ) -> ProbeProtocolState:
        allowed = (
            frozenset(path.replace("\\", "/") for path in allowed_target_scripts)
            if allowed_target_scripts is not None
            else None
        )
        if self.probe_protocol is None:
            self.probe_protocol = ProbeProtocolState(
                self.request,
                allowed_target_scripts=allowed,
            )
        elif (
            allowed is not None
            and self.probe_protocol.allowed_target_scripts != allowed
        ):
            raise RuntimeError(
                "probe target configuration changed within one CodingRun"
            )
        return self.probe_protocol

    def unresolved_probe_feedback(self) -> str | None:
        if self.probe_protocol is None:
            return None
        return self.probe_protocol.unresolved_feedback()
