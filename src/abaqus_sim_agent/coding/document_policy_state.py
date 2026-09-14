"""查证ledger的确定性状态转换；读回日志与在线提交使用同一套不变量。"""

from dataclasses import dataclass, field

from abaqus_sim_agent.retrieval.evidence_contracts import (
    EvidenceResult,
    QuestionQuery,
    SearchRequest,
)

from .document_policy_contracts import (
    AssessmentRecorded,
    DocumentAssessment,
    DocumentScope,
    EvidenceInvalidated,
    EvidenceNeed,
    EvidenceReceived,
    IndependentOperationDeclared,
    Initialized,
    InputProvided,
    NeedsRegistered,
    PolicyConfiguration,
    PolicyEvent,
    PolicyGate,
    PolicyQuery,
    QuotaAmended,
    RevisionStarted,
    SearchAbandoned,
    SearchReserved,
)


class DocumentPolicyDenied(ValueError):
    def __init__(
        self,
        message: str,
        *,
        reason_code: str = "policy_denied",
        next_action: str | None = None,
        lookup_remaining: int | None = None,
    ):
        super().__init__(message)
        self.reason_code = reason_code
        self.next_action = next_action
        self.lookup_remaining = lookup_remaining


class DocumentPolicyIntegrityError(RuntimeError):
    pass


def condition_key(condition: str) -> str:
    return " ".join(condition.split()).casefold()


@dataclass
class Chain:
    need_ids: tuple[str, ...]
    search_ids: list[str] = field(default_factory=list)
    status: str = "pending"


@dataclass
class PolicyState:
    configuration: PolicyConfiguration | None = None
    scope: DocumentScope | None = None
    revision: int = -1
    limit: int = 0
    used: int = 0
    needs: dict[str, EvidenceNeed] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    owners: dict[str, str] = field(default_factory=dict)
    chains: dict[str, Chain] = field(default_factory=dict)
    reservations: dict[str, SearchReserved] = field(default_factory=dict)
    results: dict[str, EvidenceResult] = field(default_factory=dict)
    pending: set[str] = field(default_factory=set)
    invalid: set[str] = field(default_factory=set)
    abandoned: set[str] = field(default_factory=set)
    known_ids: set[str] = field(default_factory=set)
    independent: dict[str, set[str]] = field(default_factory=dict)
    supporting_searches: dict[str, set[str]] = field(default_factory=dict)

    def request_for(self, query: PolicyQuery) -> SearchRequest:
        if self.scope is None:
            raise DocumentPolicyDenied("task revision is not initialized")
        return SearchRequest(
            query=QuestionQuery(
                question=query.question,
                observed_context=query.observed_context,
                product=self.scope.product,
                source_release=self.scope.source_release,
            ),
            constraints_origin=self.scope.constraints_origin,
            allowed_sources=self.scope.allowed_sources,
        )

    def unresolved(self, ids: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            k for k in ids if self.statuses[k] not in {"supported", "provided"}
        )

    def lookup_faulted(self) -> bool:
        return bool(
            self.invalid
            or self.abandoned
            or any(r.status == "unavailable" for r in self.results.values())
        )

    def check_result(self, reservation: SearchReserved, result: EvidenceResult) -> None:
        if self.configuration is None or result.search_id != reservation.search_id:
            raise DocumentPolicyDenied("receipt identity mismatch")
        if result.binding is not None and result.binding != self.configuration.binding:
            raise DocumentPolicyDenied("receipt backend binding mismatch")
        scope = self.scope
        if scope is None:
            raise DocumentPolicyDenied("receipt has no revision scope")
        for item in result.evidence:
            if (
                scope.source_release is not None
                and item.release != scope.source_release
            ):
                raise DocumentPolicyDenied("receipt violates hard document release")
            if (
                scope.allowed_sources
                and item.source.split("#", 1)[0] not in scope.allowed_sources
            ):
                raise DocumentPolicyDenied("receipt violates allowed sources")
            if (
                scope.product
                and item.applicability.origin != "unknown"
                and scope.product not in item.applicability.products
            ):
                raise DocumentPolicyDenied("receipt violates product scope")

    def check_assessment(self, assessment: DocumentAssessment) -> Chain:
        reservation = self.reservations.get(assessment.search_id)
        if (
            reservation is None
            or assessment.search_id not in self.results
            or assessment.search_id in self.invalid
        ):
            raise DocumentPolicyDenied("assessment requires a current usable receipt")
        chain = self.chains[reservation.root_search_id]
        if (
            chain.search_ids[-1] != assessment.search_id
            or chain.status != "awaiting_assessment"
        ):
            raise DocumentPolicyDenied(
                "assessment is not for the pending latest search"
            )
        if assessment.scope != self.scope:
            raise DocumentPolicyDenied("assessment cannot change host scope")
        ids = [row.need_id for row in assessment.needs]
        if len(set(ids)) != len(ids) or set(ids) != set(chain.need_ids):
            raise DocumentPolicyDenied(
                "assessment must cover every registered chain need exactly once"
            )
        current = self.results[assessment.search_id]
        for row in assessment.needs:
            # Earlier found evidence may still support carried-over conditions. A failed
            # current receipt itself can never be cited; source identity is explicit below.
            for citation in row.citations:
                if (
                    citation.search_id not in chain.search_ids
                    or citation.search_id in self.invalid
                ):
                    raise DocumentPolicyDenied(
                        "citation is outside the current chain/revision"
                    )
                source = self.results.get(citation.search_id)
                if source is None or source.status != "found":
                    raise DocumentPolicyDenied(
                        "citation does not refer to found evidence"
                    )
                item = next(
                    (e for e in source.evidence if e.id == citation.evidence_id), None
                )
                if item is None or citation.quote not in item.body:
                    raise DocumentPolicyDenied(
                        "citation ID or exact quote is not in the receipt"
                    )
                if (
                    item.applicability.origin == "unknown"
                    and citation.applicability != "unknown"
                ):
                    raise DocumentPolicyDenied(
                        "unknown applicability cannot be promoted"
                    )
        missing = [r.need_id for r in assessment.needs if r.status != "supported"]
        if assessment.next_action == "continue" and any(
            self.needs[k].critical for k in missing
        ):
            raise DocumentPolicyDenied("critical conditions remain unresolved")
        if assessment.next_action == "follow_up" and (
            not missing
            or len(chain.search_ids) >= 2
            or self.used >= self.limit
            or current.status in {"unavailable", "rejected"}
        ):
            raise DocumentPolicyDenied(
                "follow-up is unavailable; retain the gap and stop this chain"
            )
        return chain

    def gate(self, action: str, operation_id: str | None = None) -> PolicyGate:
        if action == "files":
            return PolicyGate(allowed=True, reason="original_checks_required")
        physical = tuple(
            k
            for k, n in self.needs.items()
            if n.kind == "physical_input" and self.statuses[k] != "provided"
        )
        if physical:
            return PolicyGate(
                allowed=False, reason="needs_input", unresolved_need_ids=physical
            )
        if self.pending or any(
            c.status == "awaiting_assessment" for c in self.chains.values()
        ):
            return PolicyGate(allowed=False, reason="assessment_pending")
        missing = tuple(
            k
            for k, n in self.needs.items()
            if n.critical and self.statuses[k] not in {"supported", "provided"}
        )
        if action == "abaqus" and operation_id is not None:
            exempt = self.independent.get(operation_id, set())
            missing = tuple(k for k in missing if k not in exempt)
        if missing:
            return PolicyGate(
                allowed=False, reason="knowledge_gap", unresolved_need_ids=missing
            )
        return PolicyGate(allowed=True, reason="original_checks_required")

    def _add_needs(self, needs: tuple[EvidenceNeed, ...]) -> None:
        if len(self.needs) + len(needs) > 8:
            raise DocumentPolicyDenied("at most eight needs per task revision")
        keys = {condition_key(n.condition) for n in self.needs.values()}
        for need in needs:
            key = condition_key(need.condition)
            if need.id in self.known_ids or key in keys:
                raise DocumentPolicyDenied("duplicate need ID or condition")
            self.known_ids.add(need.id)
            keys.add(key)
            self.needs[need.id] = need
            self.statuses[need.id] = "missing"

    def apply(self, event: PolicyEvent) -> None:
        if isinstance(event, Initialized):
            if self.configuration is not None:
                raise DocumentPolicyDenied("duplicate policy initialization")
            self.configuration, self.scope = (
                event.configuration,
                event.configuration.scope,
            )
            self.limit, self.used = event.max_lookup_calls, event.initial_lookup_calls
            return
        if self.configuration is None:
            raise DocumentPolicyDenied("journal has no initialization")
        if isinstance(event, RevisionStarted):
            if self.pending or event.task_revision != self.revision + 1:
                raise DocumentPolicyDenied(
                    "revision must advance exactly once without pending search"
                )
            if any(n.origin == "model_proposed" for n in event.host_needs):
                raise DocumentPolicyDenied("revision declarations must be host-owned")
            self.revision, self.scope = event.task_revision, event.scope
            self.needs, self.statuses, self.owners, self.chains = {}, {}, {}, {}
            self.reservations, self.results, self.independent = {}, {}, {}
            self.supporting_searches = {}
            self.invalid, self.abandoned = set(), set()
            self._add_needs(event.host_needs)
            return
        if self.revision < 0:
            raise DocumentPolicyDenied("journal has no task revision")
        if isinstance(event, NeedsRegistered):
            if any(
                n.origin != "model_proposed" or n.kind != "knowledge"
                for n in event.needs
            ):
                raise DocumentPolicyDenied("model cannot register host/input identity")
            self._add_needs(event.needs)
        elif isinstance(event, SearchReserved):
            self._reserve(event)
        elif isinstance(event, EvidenceReceived):
            if event.search_id not in self.pending:
                raise DocumentPolicyDenied("receipt has no pending reservation")
            self.check_result(self.reservations[event.search_id], event.result)
            self.results[event.search_id] = event.result
            self.pending.remove(event.search_id)
            self.chains[
                self.reservations[event.search_id].root_search_id
            ].status = "awaiting_assessment"
        elif isinstance(event, AssessmentRecorded):
            chain = self.check_assessment(event.assessment)
            for row in event.assessment.needs:
                self.statuses[row.need_id] = row.status
                self.supporting_searches[row.need_id] = (
                    {c.search_id for c in row.citations}
                    if row.status == "supported"
                    else set()
                )
            chain.status = (
                "follow_up" if event.assessment.next_action == "follow_up" else "closed"
            )
        elif isinstance(event, SearchAbandoned):
            if event.search_id not in self.pending:
                raise DocumentPolicyDenied("only pending searches can be abandoned")
            self.pending.remove(event.search_id)
            self.abandoned.add(event.search_id)
            self.chains[
                self.reservations[event.search_id].root_search_id
            ].status = "closed"
        elif isinstance(event, EvidenceInvalidated):
            if event.search_id not in self.results or event.search_id in self.invalid:
                raise DocumentPolicyDenied(
                    "cannot invalidate an absent/already invalid receipt"
                )
            self.invalid.add(event.search_id)
            chain = self.chains[self.reservations[event.search_id].root_search_id]
            chain.status = "closed"
            for key in chain.need_ids:
                self.statuses[key] = "missing"
                self.supporting_searches.pop(key, None)
        elif isinstance(event, QuotaAmended):
            self.limit = event.max_lookup_calls
        elif isinstance(event, InputProvided):
            need = self.needs.get(event.need_id)
            if (
                need is None
                or need.origin == "model_proposed"
                or need.kind != "physical_input"
            ):
                raise DocumentPolicyDenied(
                    "only a host-declared physical input can be provided"
                )
            self.statuses[event.need_id] = "provided"
        elif isinstance(event, IndependentOperationDeclared):
            ids = event.independent_of_need_ids
            if len(set(ids)) != len(ids) or not set(ids) <= self.needs.keys():
                raise DocumentPolicyDenied("unknown or duplicate operation dependency")
            self.independent[event.operation_id] = set(ids)

    def _reserve(self, event: SearchReserved) -> None:
        if self.lookup_faulted():
            raise DocumentPolicyDenied(
                "technical lookup failure blocks reworded/new-root retries in this revision"
            )
        if event.search_id in self.known_ids or self.pending:
            raise DocumentPolicyDenied("search ID reused or another query is pending")
        if not self.used < event.cumulative_lookup_calls <= self.limit:
            raise DocumentPolicyDenied(
                "lookup budget exhausted or counter rewound",
                reason_code="lookup_budget_exhausted"
                if event.cumulative_lookup_calls > self.limit
                else "lookup_counter_rewound",
                next_action="Do not retry this query; inspect the host lookup budget and report unresolved evidence.",
                lookup_remaining=max(0, self.limit - self.used),
            )
        if any(
            n.kind == "physical_input" and self.statuses[k] != "provided"
            for k, n in self.needs.items()
        ):
            raise DocumentPolicyDenied("clarify physical input before document lookup")
        if event.request != self.request_for(event.query):
            raise DocumentPolicyDenied(
                "query scope changed or need labels leaked into retrieval input"
            )
        if event.query.kind == "first":
            if any(c.status != "closed" for c in self.chains.values()):
                raise DocumentPolicyDenied(
                    "finish assessment/follow-up of the active chain first"
                )
            expected = tuple(
                k
                for k, n in self.needs.items()
                if n.kind == "knowledge" and k not in self.owners
            )
            if (
                not expected
                or event.need_ids != expected
                or event.root_search_id != event.search_id
            ):
                raise DocumentPolicyDenied(
                    "first query must own the unassigned needs and a fresh host root"
                )
            chain = Chain(event.need_ids)
            self.chains[event.root_search_id] = chain
            self.owners.update(dict.fromkeys(event.need_ids, event.root_search_id))
        else:
            chain = self.chains.get(event.root_search_id)
            remaining = max(0, self.limit - self.used)
            if chain is None or not chain.search_ids:
                raise DocumentPolicyDenied(
                    "follow-up references an invalid host chain",
                    reason_code="invalid_chain",
                    next_action="Use a root_search_id actually returned by the host.",
                    lookup_remaining=remaining,
                )
            if len(chain.search_ids) != 1:
                raise DocumentPolicyDenied(
                    "one follow-up per host-owned chain has already been used; no new root bypass",
                    reason_code="follow_up_exhausted",
                    next_action="Stop or report the remaining evidence gap; do not reopen this chain.",
                    lookup_remaining=remaining,
                )
            if chain.status == "awaiting_assessment":
                raise DocumentPolicyDenied(
                    "record an assessment of the first receipt before requesting follow-up; no lookup was consumed by this denial",
                    reason_code="assessment_required",
                    next_action="Call record_document_assessment for every returned need; choose follow_up if a relevant gap remains.",
                    lookup_remaining=remaining,
                )
            if chain.status != "follow_up":
                raise DocumentPolicyDenied(
                    "the current chain state does not permit follow-up",
                    reason_code="chain_not_follow_up",
                    next_action="Inspect the host chain state; this denial does not mean the run lookup budget is exhausted.",
                    lookup_remaining=remaining,
                )
            selected = event.query.unresolved_need_ids
            if (
                event.query.follow_up_of != event.root_search_id
                or event.need_ids != chain.need_ids
                or len(set(selected)) != len(selected)
                or not set(selected) <= set(self.unresolved(chain.need_ids))
            ):
                raise DocumentPolicyDenied(
                    "follow-up must name unresolved needs in this chain"
                )
        chain.search_ids.append(event.search_id)
        chain.status = "pending"
        self.known_ids.add(event.search_id)
        self.reservations[event.search_id] = event
        self.pending.add(event.search_id)
        self.used = event.cumulative_lookup_calls
