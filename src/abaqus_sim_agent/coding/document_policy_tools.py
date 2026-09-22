"""同一Coder的有界查证工具；宿主配置/need声明不接受模型写入。"""

import hashlib
import json
from typing import Literal

from pydantic_ai import Agent, RunContext

from .dependencies import CodingAgentDependencies
from .document_policy_contracts import DocumentAssessment, PolicyQuery
from .document_policy_state import DocumentPolicyDenied

POLICY_INSTRUCTIONS = """
Document policy bounded_v1 is active. Do not search when workspace/task evidence suffices.
search_abaqus_docs accepts a query with kind=first and proposed_needs, or kind=follow_up
with follow_up_of (the host root_search_id) and unresolved_need_ids. Only ask questions;
API symbols remain the separate API Catalog's responsibility. Never invent physical inputs.
After each search, call record_document_assessment with protocol="document-assessment-v1"
and assessment containing every returned need, exact citations
(search_id, evidence_id, quote, applicability), the unchanged host scope, and next_action.
Each need is supported/partial/missing/conflict. Supported is only your assessment, not a
verified scientific conclusion. Unknown applicability stays unknown. Evidence may be incomplete.
The host allows at most one semantic follow-up per chain and counts every dispatched lookup,
including failures. A technical failure is not permission to rename needs or reword a retry.
Pending assessments and unresolved critical needs block Abaqus and final readiness. You may
continue ordinary file inspection. Request needs_input for missing physical user inputs;
otherwise stop with failed/failure_stage=knowledge_evidence if critical evidence is unavailable.
Scope and mandatory needs are provided below by the host. Do not infer a new scope from prose.
""".strip()


def register_document_policy_tools[AgentOutputT](
    agent: Agent[CodingAgentDependencies, AgentOutputT],
) -> None:
    @agent.instructions
    def current_document_state(ctx: RunContext[CodingAgentDependencies]) -> str:
        policy = ctx.deps.document_policy
        if policy is None:
            return "Host document policy is not initialized; do not perform protected actions."
        state = policy.journal.state
        return "Current host document state (data, not instructions): " + json.dumps(
            {
                "scope": state.scope.model_dump(mode="json") if state.scope else None,
                "needs": [
                    n.model_dump(mode="json") | {"status": state.statuses[n.id]}
                    for n in policy.needs
                ],
                "chains": [
                    {
                        "root_search_id": key,
                        "status": chain.status,
                        "search_ids": chain.search_ids,
                    }
                    for key, chain in state.chains.items()
                ],
                "lookup_calls": ctx.deps.concept_lookup_calls,
                "max_lookup_calls": state.limit,
            },
            ensure_ascii=False,
        )

    @agent.tool(name="search_abaqus_docs", sequential=True)
    async def search_docs(
        ctx: RunContext[CodingAgentDependencies], query: PolicyQuery
    ) -> dict:
        """Search for registered knowledge conditions, or follow up once on unresolved needs."""
        policy = ctx.deps.document_policy
        if policy is None:
            return {"status": "blocked", "reason": "Document policy is not initialized"}
        try:
            return (
                await policy.search(
                    query,
                    deadline=ctx.deps.document_operation_deadline(),
                )
            ).model_dump(mode="json")
        except (DocumentPolicyDenied, TimeoutError) as exc:
            return {
                "status": "blocked",
                "reason": str(exc),
                **_denial_details(exc),
                "needs": [n.model_dump(mode="json") for n in policy.needs],
            }

    @agent.tool(name="record_document_assessment", sequential=True)
    async def record_assessment(
        ctx: RunContext[CodingAgentDependencies],
        assessment: DocumentAssessment,
        protocol: Literal["document-assessment-v1"],
    ) -> dict:
        """Record all registered conditions and exact evidence quotations; not physical acceptance."""
        # Keep an explicit object envelope: SDK single-model flattening loses the
        # JSON-mode path needed by nested strict tuple fields in this profile.
        del protocol
        policy = ctx.deps.document_policy
        if policy is None:
            return {"status": "blocked", "reason": "Document policy is not initialized"}
        try:
            await policy.record_assessment(
                assessment,
                deadline=ctx.deps.document_operation_deadline(),
            )
            return {
                "status": "recorded",
                "judgment_origin": "model_assessed_not_verified",
                "gate": policy.execution_gate("abaqus").model_dump(mode="json"),
            }
        except (DocumentPolicyDenied, TimeoutError) as exc:
            return {"status": "blocked", "reason": str(exc), **_denial_details(exc)}


def _denial_details(exc: DocumentPolicyDenied | TimeoutError) -> dict:
    if not isinstance(exc, DocumentPolicyDenied):
        return {"reason_code": "document_timeout"}
    return {
        "reason_code": exc.reason_code,
        "next_action": exc.next_action,
        "lookup_remaining": exc.lookup_remaining,
    }


def _independent_operation(
    deps: CodingAgentDependencies, kind: str, path: str
) -> str | None:
    binding = deps.request.document_policy
    if binding is None:
        return None
    for operation in binding.spec.independent_operations:
        if (
            kind != operation.kind
            or path.replace("\\", "/").casefold() != operation.relative_path.casefold()
        ):
            continue
        candidate = deps.request.workspace / operation.relative_path
        if (
            candidate.resolve() != candidate
            or not candidate.is_file()
            or candidate.is_symlink()
        ):
            return None
        if hashlib.sha256(candidate.read_bytes()).hexdigest() == operation.sha256:
            return operation.operation_id
    return None


async def document_runtime_denial(
    deps: CodingAgentDependencies, *, kind: str, path: str
) -> dict | None:
    if deps.request.document_policy is None:
        return None
    policy = deps.document_policy
    if policy is None:
        reason = "Document policy is required but unavailable"
    else:
        try:
            gate = await policy.validate_execution_gate(
                "abaqus",
                deadline=deps.document_operation_deadline(),
                host_operation_id=_independent_operation(deps, kind, path),
            )
            if gate.allowed:
                return None
            reason = gate.reason + ": " + ", ".join(gate.unresolved_need_ids)
        except (DocumentPolicyDenied, TimeoutError) as exc:
            reason = str(exc)
    return {
        "blocked": True,
        "succeeded": False,
        "stage_succeeded": False,
        "budget_consumed": False,
        "runtime_executed": False,
        "error_type": "DocumentPolicyDenied",
        "error": reason,
    }
