"""PLAN-0003的独立宿主策略组件；尚未注册到真实Coder工具或CLI。"""

import math
import time
import traceback
import uuid
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ValidationError

from abaqus_sim_agent.retrieval.evidence_contracts import (
    RESULT_ADAPTER,
    Binding,
    EvidenceResult,
    SearchRequest,
    Unavailable,
)

from .document_policy_contracts import (
    QUERY_ADAPTER,
    AssessmentRecorded,
    DocumentAssessment,
    DocumentScope,
    EvidenceInvalidated,
    EvidenceNeed,
    EvidenceReceived,
    HostNeed,
    IndependentOperationDeclared,
    Initialized,
    InputProvided,
    NeedsRegistered,
    PolicyCheckpoint,
    PolicyConfiguration,
    PolicyGate,
    PolicyQuery,
    PolicySearchReply,
    QuotaAmended,
    RevisionStarted,
    SearchAbandoned,
    SearchReserved,
)
from .document_policy_journal import PolicyJournal
from .document_policy_state import (
    DocumentPolicyDenied,
    DocumentPolicyIntegrityError,
    PolicyState,
    condition_key,
)
from .document_search_port import DocumentSearchPort
from .run_lock import CodingRunFileLock


class DocumentLookupUsage(Protocol):
    concept_lookup_calls: int


class DocumentReceiptValidator(Protocol):
    async def validate(
        self,
        request: SearchRequest,
        result: EvidenceResult,
        *,
        binding: Binding,
        deadline: float,
    ) -> None:
        """重新核对实际依赖与原文；失败抛异常，不能仅比较目录名或缓存pointer。"""
        ...


class DocumentPolicy:
    def __init__(
        self,
        *,
        state_dir: Path,
        workspace: Path,
        lock: CodingRunFileLock,
        port: DocumentSearchPort,
        validator: DocumentReceiptValidator,
        usage: DocumentLookupUsage,
    ):
        """绑定宿主拥有的日志与预算计数，不获取第二把锁或创建独立tool loop。

        Args:
            state_dir: 工作区之外的宿主state目录。
            workspace: 模型普通文件工作区，用于排除state重叠。
            lock: 调用方已持有且持续覆盖此实例使用期的CodingRun锁。
            port: 单次证据搜索端口；本阶段仅在测试中注入fake。
            validator: 收据复用/回评前的独立依赖和来源校验；不可缺省。
            usage: 共享的concept_lookup_calls计数，恢复取日志与宿主快照的较大值。

        Note:
            使用create/restore后才能工作。所有host_*方法只能由宿主组合层调用，
            不属于模型输入schema。这里未改变真实工具、CLI或resume_state格式。
        """
        state_dir, workspace = state_dir.resolve(), workspace.resolve()
        if state_dir.is_relative_to(workspace) or workspace.is_relative_to(state_dir):
            raise ValueError("document policy state must be disjoint from workspace")
        if (
            not isinstance(usage.concept_lookup_calls, int)
            or usage.concept_lookup_calls < 0
        ):
            raise ValueError("invalid host lookup usage")
        self.journal = PolicyJournal(state_dir, lock)
        self.port, self.validator, self.usage = port, validator, usage
        self._busy = False

    def create(
        self,
        configuration: PolicyConfiguration,
        *,
        max_lookup_calls: int = 2,
        host_needs: tuple[HostNeed, ...] = (),
    ) -> None:
        configuration = PolicyConfiguration.model_validate_json(
            configuration.model_dump_json()
        )
        initialized = Initialized(
            configuration=configuration,
            max_lookup_calls=max_lookup_calls,
            initial_lookup_calls=self.usage.concept_lookup_calls,
        )
        revision = RevisionStarted(
            task_revision=0,
            host_needs=self._host_declarations(host_needs),
            scope=configuration.scope,
        )
        checked = PolicyState()
        checked.apply(initialized)
        checked.apply(revision)
        self.journal.create()
        self.journal.append(initialized)
        self.journal.append(revision)

    def restore(
        self,
        configuration: PolicyConfiguration,
        *,
        checkpoint: PolicyCheckpoint | None = None,
    ) -> None:
        self.journal.restore(checkpoint)
        if self.journal.state.configuration != configuration:
            self.journal._poisoned = True
            raise DocumentPolicyIntegrityError(
                "run policy, scope or backend configuration changed"
            )
        self.usage.concept_lookup_calls = max(
            self.usage.concept_lookup_calls, self.journal.state.used
        )
        for search_id in sorted(self.journal.state.pending):
            self.journal.append(SearchAbandoned(search_id=search_id))

    def _idle(self):
        self.journal.assert_current()
        if self.journal.state.revision < 0:
            raise DocumentPolicyIntegrityError(
                "document policy has no initialized revision"
            )
        if self._busy:
            raise DocumentPolicyDenied("another document operation is in flight")

    def host_start_revision(
        self, needs: tuple[HostNeed, ...], *, scope: DocumentScope | None = None
    ) -> None:
        self.journal.assert_current()
        if self._busy:
            raise DocumentPolicyDenied("another document operation is in flight")
        declarations = self._host_declarations(needs)
        scope = scope or self.journal.state.scope
        if scope is None:
            raise DocumentPolicyDenied("revision requires host scope")
        self.journal.append(
            RevisionStarted(
                task_revision=self.journal.state.revision + 1,
                host_needs=declarations,
                scope=scope,
            )
        )

    def host_amend_lookup_limit(self, limit: int) -> None:
        self._idle()
        self.journal.append(QuotaAmended(max_lookup_calls=limit))

    @staticmethod
    def _host_declarations(needs: tuple[HostNeed, ...]) -> tuple[EvidenceNeed, ...]:
        parsed = tuple(HostNeed.model_validate_json(n.model_dump_json()) for n in needs)
        return tuple(
            EvidenceNeed(id="need-" + uuid.uuid4().hex, **n.model_dump())
            for n in parsed
        )

    def host_provide_input(self, need_id: str, reference: str) -> None:
        self._idle()
        self.journal.append(InputProvided(need_id=need_id, host_reference=reference))

    def host_declare_independent(
        self, operation_id: str, need_ids: tuple[str, ...], reference: str
    ) -> None:
        self._idle()
        self.journal.append(
            IndependentOperationDeclared(
                operation_id=operation_id,
                independent_of_need_ids=need_ids,
                host_reference=reference,
            )
        )

    def checkpoint(self) -> PolicyCheckpoint:
        return self.journal.checkpoint()

    @property
    def needs(self) -> tuple[EvidenceNeed, ...]:
        self.journal.assert_current()
        return tuple(self.journal.state.needs.values())

    def execution_gate(
        self,
        action: Literal["files", "abaqus", "ready_for_verification"],
        *,
        host_operation_id: str | None = None,
    ) -> PolicyGate:
        """只读状态投影；真正执行前应调用validate_execution_gate重新核验所依赖的收据。"""
        self.journal.assert_current()
        if self.journal.state.revision < 0:
            raise DocumentPolicyIntegrityError(
                "document policy has no initialized revision"
            )
        if action not in {"files", "abaqus", "ready_for_verification"}:
            raise DocumentPolicyDenied("unknown guarded action")
        return self.journal.state.gate(action, host_operation_id)

    async def validate_execution_gate(
        self,
        action: Literal["files", "abaqus", "ready_for_verification"],
        *,
        deadline: float,
        host_operation_id: str | None = None,
    ) -> PolicyGate:
        """校验关键依赖后返回门禁结果，不执行Abaqus，也不消耗Abaqus预算。

        Note:
            只有宿主明确声明的独立步骤可排除相关need；文件检查不依赖RAG。
            allowed仍要求原权限/预算/runtime/verifier，不能用作物理通过信号。
        """
        self._deadline(deadline)
        if action == "files":
            return self.execution_gate(action)
        self._idle()
        gate = self.execution_gate(action, host_operation_id=host_operation_id)
        if not gate.allowed or action == "files":
            return gate
        state = self.journal.state
        excluded = (
            state.independent.get(host_operation_id, set())
            if action == "abaqus" and host_operation_id
            else set()
        )
        searches = {
            sid
            for key, need in state.needs.items()
            if need.critical and key not in excluded
            for sid in state.supporting_searches.get(key, set())
        }
        self._busy = True
        try:
            for sid in sorted(searches):
                try:
                    await self._validate_stored(sid, deadline)
                except DocumentPolicyDenied:
                    return self.execution_gate(
                        action, host_operation_id=host_operation_id
                    )
            return self.execution_gate(action, host_operation_id=host_operation_id)
        finally:
            self._busy = False

    def _reply(self, search_id: str, *, cache_hit: bool) -> PolicySearchReply:
        state = self.journal.state
        reservation = state.reservations[search_id]
        chain = state.chains[reservation.root_search_id]
        return PolicySearchReply(
            root_search_id=reservation.root_search_id,
            result=state.results[search_id],
            needs=tuple(state.needs[k] for k in chain.need_ids),
            cache_hit=cache_hit,
            cumulative_lookup_calls=max(state.used, self.usage.concept_lookup_calls),
            requires_assessment=chain.status == "awaiting_assessment",
        )

    async def _validate_stored(self, search_id: str, deadline: float) -> None:
        state = self.journal.state
        if search_id in state.invalid or search_id not in state.results:
            raise DocumentPolicyDenied("receipt is absent or invalidated")
        if state.configuration is None:
            raise DocumentPolicyIntegrityError("missing configuration")
        try:
            self._deadline(deadline)
            await self.validator.validate(
                state.reservations[search_id].request,
                state.results[search_id],
                binding=state.configuration.binding,
                deadline=deadline,
            )
            self._deadline(deadline)
        except Exception as exc:
            self.journal.append(EvidenceInvalidated(search_id=search_id))
            raise DocumentPolicyDenied(
                "receipt revalidation failed; evidence invalidated, no retry"
            ) from exc

    @staticmethod
    def _deadline(deadline: float):
        if not math.isfinite(deadline) or time.monotonic() >= deadline:
            raise TimeoutError("document operation deadline expired or invalid")

    async def search(self, query: PolicyQuery, *, deadline: float) -> PolicySearchReply:
        self._idle()
        query = QUERY_ADAPTER.validate_json(query.model_dump_json())
        self._deadline(deadline)
        state = self.journal.state
        request = state.request_for(query)
        if any(
            n.kind == "physical_input" and state.statuses[k] != "provided"
            for k, n in state.needs.items()
        ):
            raise DocumentPolicyDenied("clarify physical input before document lookup")
        self._busy = True
        try:
            for sid, previous in state.reservations.items():
                if previous.query == query:
                    if sid in state.abandoned or sid in state.pending:
                        raise DocumentPolicyDenied(
                            "query was interrupted or remains pending; no automatic replay"
                        )
                    await self._validate_stored(sid, deadline)
                    return self._reply(sid, cache_hit=True)
            if state.lookup_faulted():
                raise DocumentPolicyDenied(
                    "technical lookup failure blocks reworded/new-root retries in this revision"
                )
            if query.kind == "first":
                if any(c.status != "closed" for c in state.chains.values()):
                    raise DocumentPolicyDenied(
                        "active chain must be assessed/handled before a new root"
                    )
                existing = {condition_key(n.condition) for n in state.needs.values()}
                proposed = []
                for n in query.proposed_needs:
                    key = condition_key(n.condition)
                    if key not in existing:
                        proposed.append(
                            EvidenceNeed(
                                id="need-" + uuid.uuid4().hex,
                                origin="model_proposed",
                                **n.model_dump(),
                            )
                        )
                        existing.add(key)
                if proposed:
                    self.journal.append(NeedsRegistered(needs=tuple(proposed)))
                state = self.journal.state
                need_ids = tuple(
                    k
                    for k, n in state.needs.items()
                    if n.kind == "knowledge" and k not in state.owners
                )
                root = search_id = "search-" + uuid.uuid4().hex
            else:
                root = query.follow_up_of
                chain = state.chains.get(root)
                if chain is None:
                    raise DocumentPolicyDenied(
                        "unknown host root",
                        reason_code="invalid_chain",
                        next_action="Use a root_search_id actually returned by the host.",
                        lookup_remaining=max(0, state.limit - state.used),
                    )
                search_id = "search-" + uuid.uuid4().hex
                need_ids = chain.need_ids
            if not need_ids:
                raise DocumentPolicyDenied(
                    "first query needs unassigned registered knowledge conditions"
                )
            reservation = SearchReserved(
                search_id=search_id,
                root_search_id=root,
                query=query,
                request=request,
                need_ids=need_ids,
                cumulative_lookup_calls=max(state.used, self.usage.concept_lookup_calls)
                + 1,
            )
            self._deadline(deadline)
            self.journal.append(reservation)
            self.usage.concept_lookup_calls = reservation.cumulative_lookup_calls
            try:
                result = await self.port.search(
                    request, search_id=search_id, deadline=deadline
                )
                result = RESULT_ADAPTER.validate_json(result.model_dump_json())
                self.journal.state.check_result(reservation, result)
                self._deadline(deadline)
                configuration = self.journal.state.configuration
                if configuration is None:
                    raise DocumentPolicyIntegrityError("missing configuration")
                await self.validator.validate(
                    request, result, binding=configuration.binding, deadline=deadline
                )
                self._deadline(deadline)
            except Exception as exc:
                error_path = self.journal.state_dir / (search_id + ".error.txt")
                error_path.write_text(traceback.format_exc(), encoding="utf-8")
                result = Unavailable(
                    search_id=search_id,
                    reason_code=(
                        "worker_timeout"
                        if isinstance(exc, TimeoutError)
                        else "integrity_mismatch"
                        if isinstance(exc, (ValidationError, DocumentPolicyDenied))
                        else "worker_failed"
                    ),
                    message="Document lookup/validation failed; the reservation is consumed and no retry was attempted.",
                )
            except BaseException:
                self.journal.append(SearchAbandoned(search_id=search_id))
                raise
            self.journal.append(EvidenceReceived(search_id=search_id, result=result))
            return self._reply(search_id, cache_hit=False)
        finally:
            self._busy = False

    async def record_assessment(
        self, assessment: DocumentAssessment, *, deadline: float
    ) -> None:
        self._idle()
        assessment = DocumentAssessment.model_validate_json(
            assessment.model_dump_json()
        )
        self.journal.state.check_assessment(assessment)
        self._busy = True
        try:
            ids = {
                assessment.search_id,
                *(c.search_id for n in assessment.needs for c in n.citations),
            }
            for sid in sorted(ids):
                await self._validate_stored(sid, deadline)
            self.journal.append(AssessmentRecorded(assessment=assessment))
        finally:
            self._busy = False
