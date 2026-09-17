import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.coding.document_policy import DocumentPolicy
from abaqus_sim_agent.coding.document_policy_contracts import (
    QUERY_ADAPTER,
    Citation,
    DocumentAssessment,
    DocumentScope,
    FirstQuery,
    FollowUpQuery,
    HostNeed,
    JournalEntry,
    NeedAssessment,
    PolicyConfiguration,
    ProposedNeed,
)
from abaqus_sim_agent.coding.document_policy_journal import entry_digest
from abaqus_sim_agent.coding.document_policy_state import (
    DocumentPolicyDenied,
    DocumentPolicyIntegrityError,
)
from abaqus_sim_agent.coding.run_lock import CodingRunAlreadyLocked, CodingRunFileLock
from abaqus_sim_agent.retrieval.evidence_contracts import (
    Binding,
    Cost,
    Evidence,
    Found,
    NotFound,
    Rejected,
    Unavailable,
)

BINDING = Binding(
    snapshot_sha256="a" * 64,
    prepared_sha256="b" * 64,
    candidate_index_sha256="c" * 64,
    symbol_vector_sha256="d" * 64,
    ranker_acquisition_sha256="e" * 64,
)
SCOPE = DocumentScope(
    product="Abaqus/Standard",
    source_release="2026 FD02",
    constraints_origin="user_confirmed",
)
CONFIG = PolicyConfiguration(run_id="run-test", binding=BINDING, scope=SCOPE)


class FakePort:
    def __init__(self):
        self.calls = []
        self.kind = "found"
        self.failure = None
        self.on_search = None
        self.started = asyncio.Event()
        self.release = None

    async def search(self, request, *, search_id, deadline):
        self.calls.append((request, search_id, deadline))
        self.started.set()
        if self.on_search:
            self.on_search()
        if self.release:
            await self.release.wait()
        if self.failure:
            raise self.failure
        if self.kind == "unavailable":
            return Unavailable(search_id=search_id, reason_code="model_unavailable")
        if self.kind == "rejected":
            return Rejected(search_id=search_id, reason_code="query_too_long")
        if self.kind == "not_found":
            return NotFound(
                search_id=search_id, binding=BINDING, reason_code="no_candidates"
            )
        body = "Rotations are expressed in radians. Use consistent units."
        evidence = Evidence(
            id="doc-one",
            document_id="page",
            title="Units",
            section="Rotation",
            source="https://official.example/units.htm#rotation",
            source_sha256="f" * 64,
            release="2026 FD02",
            applicability={"origin": "unknown", "products": (), "evidence": "unknown"},
            body_scope="complete_stored_evidence_unit",
            body=body,
            body_sha256=hashlib.sha256(body.encode()).hexdigest(),
            member_xpaths=("/html/body/p",),
        )
        return Found(
            search_id=search_id,
            binding=BINDING,
            evidence=(evidence,),
            cost=Cost(stage="delivered", body_tokens=(12,), evidence_tokens=200),
        )


class FakeValidator:
    def __init__(self):
        self.calls = []
        self.broken = set()

    async def validate(self, request, result, *, binding, deadline):
        self.calls.append(result.search_id)
        if result.search_id in self.broken:
            raise ValueError("bound source changed")


@pytest.fixture
def scenario(tmp_path):
    state, workspace = tmp_path / "state", tmp_path / "workspace"
    usage = SimpleNamespace(concept_lookup_calls=0)
    port, validator = FakePort(), FakeValidator()
    with CodingRunFileLock(state) as lock:

        def factory():
            return DocumentPolicy(
                state_dir=state,
                workspace=workspace,
                lock=lock,
                port=port,
                validator=validator,
                usage=usage,
            )

        yield SimpleNamespace(
            state=state,
            workspace=workspace,
            lock=lock,
            port=port,
            validator=validator,
            usage=usage,
            factory=factory,
        )


def first(condition="rotation unit", *, critical=True):
    return FirstQuery(
        question="What unit is used for prescribed rotations?",
        proposed_needs=(
            ProposedNeed(
                condition=condition, critical=critical, affects_step="build_model"
            ),
        ),
    )


def deadline():
    return time.monotonic() + 20


def assessment(reply, *, status="supported", next_action="continue", source=None):
    source = source or reply
    return DocumentAssessment(
        search_id=reply.result.search_id,
        scope=SCOPE,
        needs=tuple(
            NeedAssessment(
                need_id=n.id,
                status=status,
                citations=()
                if status == "missing"
                else (
                    Citation(
                        search_id=source.result.search_id,
                        evidence_id="doc-one",
                        quote="Rotations are expressed in radians.",
                        applicability="unknown",
                    ),
                ),
                explanation="This is a model assessment, not verified semantic support.",
            )
            for n in reply.needs
        ),
        next_action=next_action,
    )


def follow_up(reply):
    return FollowUpQuery(
        question="Clarify the documented rotation unit",
        follow_up_of=reply.root_search_id,
        unresolved_need_ids=tuple(n.id for n in reply.needs),
    )


@pytest.mark.asyncio
async def test_follow_up_denials_explain_state_without_consuming_lookup(scenario):
    policy = scenario.factory()
    policy.create(CONFIG, max_lookup_calls=3)
    one = await policy.search(first(), deadline=deadline())
    with pytest.raises(DocumentPolicyDenied, match="record an assessment") as caught:
        await policy.search(follow_up(one), deadline=deadline())
    assert caught.value.reason_code == "assessment_required"
    assert caught.value.lookup_remaining == 2
    assert "record_document_assessment" in caught.value.next_action
    assert scenario.usage.concept_lookup_calls == 1
    with pytest.raises(DocumentPolicyDenied, match="unknown host root") as caught:
        await policy.search(
            follow_up(one).model_copy(update={"follow_up_of": "unknown"}),
            deadline=deadline(),
        )
    assert caught.value.reason_code == "invalid_chain"
    await policy.record_assessment(
        assessment(one, status="partial", next_action="follow_up"), deadline=deadline()
    )
    two = await policy.search(follow_up(one), deadline=deadline())
    await policy.record_assessment(
        assessment(two, status="missing", next_action="stop"), deadline=deadline()
    )
    with pytest.raises(DocumentPolicyDenied, match="one follow-up") as caught:
        await policy.search(
            follow_up(one).model_copy(update={"question": "A third distinct query"}),
            deadline=deadline(),
        )
    assert caught.value.reason_code == "follow_up_exhausted"
    assert caught.value.lookup_remaining == 1
    assert len(scenario.port.calls) == 2


def events(scenario):
    return [
        JournalEntry.model_validate_json(line).event
        for line in (scenario.state / "document_policy.jsonl").read_bytes().splitlines()
    ]


@pytest.mark.asyncio
async def test_local_task_needs_no_query_and_support_returns_only_to_original_checks(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG)
    assert policy.execution_gate("abaqus").allowed
    assert (
        policy.execution_gate("ready_for_verification").reason
        == "original_checks_required"
    )
    assert scenario.port.calls == []
    result = await policy.search(first(), deadline=deadline())
    assert not policy.execution_gate("abaqus").allowed
    assert policy.execution_gate("abaqus").reason == "assessment_pending"
    assert policy.execution_gate("files").allowed
    await policy.record_assessment(assessment(result), deadline=deadline())
    assert policy.execution_gate("ready_for_verification").allowed
    assert (
        events(scenario)[-1].assessment.judgment_origin == "model_assessed_not_verified"
    )
    assert scenario.usage.concept_lookup_calls == 1


@pytest.mark.asyncio
async def test_actual_second_lookup_and_third_lookup_denied_even_with_large_run_quota(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG, max_lookup_calls=5)
    one = await policy.search(first(), deadline=deadline())
    await policy.record_assessment(
        assessment(one, status="partial", next_action="follow_up"), deadline=deadline()
    )
    assert policy.execution_gate("abaqus").reason == "knowledge_gap"
    two = await policy.search(follow_up(one), deadline=deadline())
    assert two.root_search_id == one.root_search_id
    assert two.result.search_id != one.result.search_id
    assert two.needs == one.needs
    with pytest.raises(DocumentPolicyDenied, match="follow-up is unavailable"):
        await policy.record_assessment(
            assessment(two, status="missing", next_action="follow_up"),
            deadline=deadline(),
        )
    await policy.record_assessment(
        assessment(two, status="missing", next_action="stop"), deadline=deadline()
    )
    with pytest.raises(DocumentPolicyDenied, match="one follow-up"):
        await policy.search(
            follow_up(one).model_copy(update={"question": "another rewording"}),
            deadline=deadline(),
        )
    assert len(scenario.port.calls) == 2
    assert not policy.execution_gate("ready_for_verification").allowed


@pytest.mark.asyncio
async def test_follow_up_can_carry_prior_support_but_must_assess_all_needs(scenario):
    policy = scenario.factory()
    policy.create(CONFIG)
    q = first().model_copy(
        update={
            "proposed_needs": (
                *first().proposed_needs,
                ProposedNeed(condition="unit convention", affects_step="build_model"),
            )
        }
    )
    one = await policy.search(q, deadline=deadline())
    first_assessment = assessment(one, status="partial", next_action="follow_up")
    first_assessment = first_assessment.model_copy(
        update={
            "needs": (
                first_assessment.needs[0].model_copy(update={"status": "supported"}),
                first_assessment.needs[1],
            )
        }
    )
    await policy.record_assessment(first_assessment, deadline=deadline())
    with pytest.raises(DocumentPolicyDenied, match="unresolved needs"):
        await policy.search(follow_up(one), deadline=deadline())
    two = await policy.search(
        follow_up(one).model_copy(update={"unresolved_need_ids": (one.needs[1].id,)}),
        deadline=deadline(),
    )
    complete = assessment(two, source=one)
    await policy.record_assessment(complete, deadline=deadline())
    assert policy.execution_gate("ready_for_verification").allowed
    assert one.result.search_id in scenario.validator.calls


@pytest.mark.asyncio
async def test_need_labels_not_in_retrieval_query_and_scope_cannot_be_model_overridden(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG)
    query = first("SECRET HOST ACCEPTANCE LABEL")
    reply = await policy.search(query, deadline=deadline())
    sent = scenario.port.calls[0][0]
    assert sent.query.question == query.question
    assert "SECRET HOST" not in sent.model_dump_json()
    assert sent.query.product == SCOPE.product
    assert sent.query.source_release == SCOPE.source_release
    with pytest.raises(ValidationError):
        QUERY_ADAPTER.validate_json(
            json.dumps({**query.model_dump(mode="json"), "product": "Abaqus/Explicit"})
        )
    changed = assessment(reply).model_copy(
        update={"scope": SCOPE.model_copy(update={"source_release": None})}
    )
    with pytest.raises(DocumentPolicyDenied, match="cannot change host scope"):
        await policy.record_assessment(changed, deadline=deadline())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        "evidence_id",
        "unknown_scope",
        "omit_need",
    ],
)
async def test_invalid_citations_or_incomplete_assessment_do_not_unblock(
    scenario, damage
):
    policy = scenario.factory()
    policy.create(CONFIG)
    reply = await policy.search(first(), deadline=deadline())
    value = assessment(reply)
    row, cite = value.needs[0], value.needs[0].citations[0]
    if damage in {"quote", "evidence_id", "search_id"}:
        cite = cite.model_copy(update={damage: "not-in-receipt"})
    elif damage == "unknown_scope":
        cite = cite.model_copy(update={"applicability": "as_documented"})
    row = row.model_copy(update={"citations": (cite,)})
    value = value.model_copy(
        update={
            "needs": ()
            if damage == "omit_need"
            else (row, row)
            if damage == "duplicate_need"
            else (row,)
        }
    )
    before = (scenario.state / "document_policy.jsonl").read_bytes()
    with pytest.raises((DocumentPolicyDenied, ValidationError)):
        await policy.record_assessment(value, deadline=deadline())
    assert not policy.execution_gate("abaqus").allowed
    assert (scenario.state / "document_policy.jsonl").read_bytes() == before


@pytest.mark.asyncio
async def test_exact_quote_can_still_be_semantically_wrong_and_is_not_host_verified(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG)
    reply = await policy.search(first(), deadline=deadline())
    value = assessment(reply)
    cite = (
        value.needs[0]
        .citations[0]
        .model_copy(update={"quote": "Use consistent units."})
    )
    row = value.needs[0].model_copy(
        update={
            "citations": (cite,),
            "explanation": "Incorrect inference: this establishes that rotations are in degrees.",
        }
    )
    await policy.record_assessment(
        value.model_copy(update={"needs": (row,)}), deadline=deadline()
    )
    assert policy.execution_gate("abaqus").reason == "original_checks_required"
    assert (
        events(scenario)[-1].assessment.judgment_origin == "model_assessed_not_verified"
    )


@pytest.mark.asyncio
async def test_physical_input_precedes_lookup_and_cannot_be_downgraded_by_model(
    scenario,
):
    policy = scenario.factory()
    policy.create(
        CONFIG,
        host_needs=(
            HostNeed(
                condition="plate thickness",
                critical=True,
                affects_step="all Abaqus",
                origin="task_contract",
                kind="physical_input",
            ),
        ),
    )
    with pytest.raises(DocumentPolicyDenied, match="clarify physical"):
        await policy.search(
            first("plate thickness", critical=False), deadline=deadline()
        )
    assert scenario.port.calls == []
    assert policy.needs[0].origin == "task_contract"
    assert policy.needs[0].critical
    assert policy.execution_gate("abaqus").reason == "needs_input"
    policy.host_provide_input(
        policy.needs[0].id, "user clarification message 1: thickness supplied"
    )
    assert policy.execution_gate("abaqus").allowed


@pytest.mark.parametrize(
    "raw",
    [
        {
            "condition": "x",
            "critical": False,
            "affects_step": "y",
            "origin": "task_contract",
        },
        {"condition": "x", "affects_step": "y", "kind": "physical_input"},
    ],
)
def test_model_need_schema_cannot_forge_host_fields(raw):
    with pytest.raises(ValidationError):
        ProposedNeed.model_validate(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["not_found", "exception"])
async def test_failed_search_consumes_reservation_and_cannot_forge_support(
    scenario, kind
):
    policy = scenario.factory()
    policy.create(CONFIG)
    scenario.port.kind = kind
    if kind == "exception":
        scenario.port.failure = RuntimeError("fake infrastructure failure")
    checked = []
    scenario.port.on_search = lambda: checked.append(events(scenario)[-1].kind)
    reply = await policy.search(first(), deadline=deadline())
    assert checked == ["search_reserved"]
    assert scenario.usage.concept_lookup_calls == 1
    assert not reply.result.evidence
    with pytest.raises(DocumentPolicyDenied, match="found evidence"):
        await policy.record_assessment(assessment(reply), deadline=deadline())
    await policy.record_assessment(
        assessment(reply, status="missing", next_action="stop"), deadline=deadline()
    )
    cached = await policy.search(first(), deadline=deadline())
    assert cached.cache_hit
    assert len(scenario.port.calls) == 1


@pytest.mark.asyncio
async def test_cached_receipt_is_revalidated_and_drift_blocks_previously_supported_need(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG)
    reply = await policy.search(first(), deadline=deadline())
    await policy.record_assessment(assessment(reply), deadline=deadline())
    cached = await policy.search(first(), deadline=deadline())
    assert cached.cache_hit
    assert scenario.validator.calls.count(reply.result.search_id) == 3
    scenario.validator.broken.add(reply.result.search_id)
    with pytest.raises(DocumentPolicyDenied, match="invalidated"):
        await policy.search(first(), deadline=deadline())
    assert policy.execution_gate("abaqus").reason == "knowledge_gap"
    restored = scenario.factory()
    restored.restore(CONFIG)
    assert not restored.execution_gate("ready_for_verification").allowed
    assert len(scenario.port.calls) == 1


@pytest.mark.asyncio
async def test_active_chain_prevents_new_root_and_budget_reservation_is_cumulative(
    scenario,
):
    scenario.usage.concept_lookup_calls = 1
    policy = scenario.factory()
    policy.create(CONFIG, max_lookup_calls=2)
    reply = await policy.search(first(), deadline=deadline())
    with pytest.raises(DocumentPolicyDenied, match="active chain"):
        await policy.search(first("renamed need"), deadline=deadline())
    with pytest.raises(DocumentPolicyDenied, match="follow-up is unavailable"):
        await policy.record_assessment(
            assessment(reply, status="missing", next_action="follow_up"),
            deadline=deadline(),
        )
    await policy.record_assessment(
        assessment(reply, status="missing", next_action="stop"), deadline=deadline()
    )
    with pytest.raises(DocumentPolicyDenied, match="budget"):
        await policy.search(first("different need"), deadline=deadline())
    assert scenario.usage.concept_lookup_calls == 2
    assert len(scenario.port.calls) == 1


@pytest.mark.asyncio
async def test_optional_missing_need_does_not_claim_task_acceptance_and_independence_is_host_owned(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG)
    reply = await policy.search(first(), deadline=deadline())
    await policy.record_assessment(
        assessment(reply, status="missing", next_action="stop"), deadline=deadline()
    )
    assert not policy.execution_gate("abaqus", host_operation_id="unrelated").allowed
    policy.host_declare_independent(
        "unrelated",
        (reply.needs[0].id,),
        "task contract declares this step independent",
    )
    assert policy.execution_gate("abaqus", host_operation_id="unrelated").allowed
    assert not policy.execution_gate(
        "ready_for_verification", host_operation_id="unrelated"
    ).allowed


@pytest.mark.asyncio
async def test_resume_replays_receipt_after_stale_checkpoint_without_requery(scenario):
    policy = scenario.factory()
    policy.create(CONFIG)
    old_checkpoint = policy.checkpoint()
    reply = await policy.search(first(), deadline=deadline())
    scenario.usage.concept_lookup_calls = 0
    restored = scenario.factory()
    restored.restore(CONFIG, checkpoint=old_checkpoint)
    assert scenario.usage.concept_lookup_calls == 1
    assert restored.execution_gate("abaqus").reason == "assessment_pending"
    again = await restored.search(first(), deadline=deadline())
    assert again.result == reply.result
    assert again.cache_hit
    await restored.record_assessment(assessment(again), deadline=deadline())
    assert restored.execution_gate("ready_for_verification").allowed
    assert len(scenario.port.calls) == 1


@pytest.mark.asyncio
async def test_cancellation_abandons_reservation_and_restoration_never_restarts_it(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG)
    scenario.port.release = asyncio.Event()
    task = asyncio.create_task(policy.search(first(), deadline=deadline()))
    await scenario.port.started.wait()
    assert (await policy.validate_execution_gate("files", deadline=deadline())).allowed
    with pytest.raises(DocumentPolicyDenied, match="in flight"):
        await policy.search(first("new need"), deadline=deadline())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events(scenario)[-1].kind == "search_abandoned"
    restored = scenario.factory()
    restored.restore(CONFIG)
    with pytest.raises(DocumentPolicyDenied, match="interrupted"):
        await restored.search(first(), deadline=deadline())
    assert scenario.usage.concept_lookup_calls == 1


@pytest.mark.asyncio
async def test_resume_after_reservation_only_marks_abandoned(scenario, monkeypatch):
    policy = scenario.factory()
    policy.create(CONFIG)
    # Simulate a process disappearing after the reservation was fsynced but before dispatch.
    original = policy.journal.append

    def stop_after_reservation(event):
        original(event)
        if event.kind == "search_reserved":
            raise KeyboardInterrupt()

    monkeypatch.setattr(policy.journal, "append", stop_after_reservation)
    with pytest.raises(KeyboardInterrupt):
        await policy.search(first(), deadline=deadline())
    assert scenario.port.calls == []
    restored = scenario.factory()
    restored.restore(CONFIG)
    assert scenario.usage.concept_lookup_calls == 1
    assert events(scenario)[-1].kind == "search_abandoned"
    assert not restored.execution_gate("abaqus").allowed


@pytest.mark.asyncio
async def test_revision_invalidates_prior_receipts_but_preserves_usage_and_host_quota(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG)
    reply = await policy.search(first(), deadline=deadline())
    await policy.record_assessment(assessment(reply), deadline=deadline())
    policy.host_amend_lookup_limit(3)
    policy.host_start_revision(())
    assert policy.execution_gate("abaqus").allowed
    with pytest.raises(DocumentPolicyDenied, match="current usable"):
        await policy.record_assessment(assessment(reply), deadline=deadline())
    current = await policy.search(first(), deadline=deadline())
    assert not current.cache_hit
    assert current.result.search_id != reply.result.search_id
    assert scenario.usage.concept_lookup_calls == 2
    restored = scenario.factory()
    restored.restore(CONFIG, checkpoint=policy.checkpoint())
    assert restored.checkpoint().task_revision == 1
    assert restored.journal.state.limit == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    ["partial", "wrong_hash", "delete_head"],
)
async def test_corrupt_journal_or_checkpoint_fails_closed(scenario, damage):
    policy = scenario.factory()
    policy.create(CONFIG)
    await policy.search(first(), deadline=deadline())
    path, head = (
        scenario.state / "document_policy.jsonl",
        scenario.state / "document_policy.head.json",
    )
    rows = path.read_bytes().splitlines(keepends=True)
    if damage == "partial":
        path.write_bytes(b"".join(rows)[:-2])
    elif damage == "missing_line":
        path.write_bytes(b"".join(rows[:-1]))
    elif damage == "wrong_hash":
        raw = json.loads(rows[-1])
        raw["sha256"] = "0" * 64
        path.write_bytes(b"".join(rows[:-1]) + json.dumps(raw).encode() + b"\n")
    elif damage == "head":
        head.write_text("{}")
    elif damage == "delete_head":
        head.unlink()
    else:
        path.write_bytes(b"".join(rows) + rows[-1])
    with pytest.raises(DocumentPolicyIntegrityError):
        scenario.factory().restore(CONFIG)


@pytest.mark.asyncio
async def test_well_formed_rehashed_illegal_transition_still_fails(scenario):
    policy = scenario.factory()
    policy.create(CONFIG)
    await policy.search(first(), deadline=deadline())
    path = scenario.state / "document_policy.jsonl"
    rows = [JournalEntry.model_validate_json(x) for x in path.read_bytes().splitlines()]
    entry = rows[-1]
    event = entry.event.model_copy(update={"search_id": "not-reserved"})
    entry = entry.model_copy(update={"event": event})
    rows[-1] = entry.model_copy(update={"sha256": entry_digest(entry)})
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows))
    with pytest.raises(DocumentPolicyIntegrityError):
        scenario.factory().restore(CONFIG)


def test_same_state_lock_and_disabled_mode_boundary(scenario):
    with pytest.raises(CodingRunAlreadyLocked):
        with CodingRunFileLock(scenario.state):
            pass
    with pytest.raises(ValidationError):
        PolicyConfiguration.model_validate({**CONFIG.model_dump(), "mode": "off"})
    with pytest.raises(ValidationError):
        PolicyConfiguration.model_validate(
            {**CONFIG.model_dump(), "concept_retrieval_mode": "off"}
        )
    lock = CodingRunFileLock(scenario.state)
    with pytest.raises(RuntimeError, match="must hold"):
        DocumentPolicy(
            state_dir=scenario.state,
            workspace=scenario.workspace,
            lock=lock,
            port=scenario.port,
            validator=scenario.validator,
            usage=scenario.usage,
        )


@pytest.mark.asyncio
async def test_resume_rejects_changed_run_or_backend_and_does_not_reset_counter(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG)
    await policy.search(first(), deadline=deadline())
    with pytest.raises(DocumentPolicyIntegrityError, match="configuration changed"):
        scenario.factory().restore(
            CONFIG.model_copy(update={"run_id": "different-run"})
        )
    with pytest.raises(DocumentPolicyIntegrityError, match="configuration changed"):
        scenario.factory().restore(
            CONFIG.model_copy(
                update={
                    "binding": BINDING.model_copy(update={"snapshot_sha256": "0" * 64})
                }
            )
        )
    assert scenario.usage.concept_lookup_calls == 1


@pytest.mark.asyncio
async def test_execution_boundary_revalidates_receipts_before_fake_runtime_budget(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG)
    reply = await policy.search(first(), deadline=deadline())
    await policy.record_assessment(assessment(reply), deadline=deadline())
    runtime_calls = 0
    assert (await policy.validate_execution_gate("abaqus", deadline=deadline())).allowed
    scenario.validator.broken.add(reply.result.search_id)
    gate = await policy.validate_execution_gate("abaqus", deadline=deadline())
    if gate.allowed:
        runtime_calls += 1
    assert runtime_calls == 0
    assert gate.reason == "knowledge_gap"


@pytest.mark.asyncio
async def test_timeout_result_no_partial_evidence_and_no_query_after_expired_deadline(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG)
    with pytest.raises(TimeoutError):
        await policy.search(first(), deadline=time.monotonic() - 1)
    assert scenario.port.calls == []
    scenario.port.failure = TimeoutError("fake worker expired")
    result = await policy.search(first(), deadline=deadline())
    assert result.result.reason_code == "worker_timeout"
    assert not result.result.evidence
    assert scenario.usage.concept_lookup_calls == 1


@pytest.mark.asyncio
async def test_quota_failure_retains_registered_critical_need_and_host_amendment_works(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG, max_lookup_calls=0)
    with pytest.raises(DocumentPolicyDenied, match="budget"):
        await policy.search(first(), deadline=deadline())
    assert len(policy.needs) == 1
    assert policy.execution_gate("ready_for_verification").reason == "knowledge_gap"
    policy.host_amend_lookup_limit(2)
    reply = await policy.search(first(), deadline=deadline())
    assert len(reply.needs) == 1
    assert scenario.usage.concept_lookup_calls == 1


@pytest.mark.asyncio
async def test_new_host_revision_can_change_scope_but_old_receipts_cannot_follow(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG)
    reply = await policy.search(first(), deadline=deadline())
    policy.host_start_revision(
        (), scope=SCOPE.model_copy(update={"source_release": "2024"})
    )
    result = await policy.search(first(), deadline=deadline())
    assert result.result.status == "unavailable"
    assert result.result.reason_code == "integrity_mismatch"
    assert scenario.port.calls[-1][0].query.source_release == "2024"
    with pytest.raises(DocumentPolicyDenied, match="current usable"):
        await policy.record_assessment(assessment(reply), deadline=deadline())


@pytest.mark.asyncio
async def test_incomplete_initialization_and_stale_live_head_never_allow_execution(
    scenario,
):
    policy = scenario.factory()
    with pytest.raises((DocumentPolicyIntegrityError, FileNotFoundError)):
        policy.execution_gate("abaqus")
    policy.create(CONFIG)
    head = scenario.state / "document_policy.head.json"
    head.write_text("{}")
    with pytest.raises(DocumentPolicyIntegrityError, match="head changed"):
        await policy.search(first(), deadline=deadline())
    assert scenario.port.calls == []


@pytest.mark.asyncio
async def test_fsynced_receipt_can_lead_head_without_losing_call_on_restore(
    scenario, monkeypatch
):
    from abaqus_sim_agent.coding import document_policy_journal as journal_module

    policy = scenario.factory()
    policy.create(CONFIG)
    original = journal_module.write_text_atomic

    def broken_head(path, value):
        if policy.journal.entries[-1].event.kind == "evidence_received":
            raise OSError("head write interruption")
        original(path, value)

    monkeypatch.setattr(journal_module, "write_text_atomic", broken_head)
    with pytest.raises(OSError, match="head write"):
        await policy.search(first(), deadline=deadline())
    monkeypatch.setattr(journal_module, "write_text_atomic", original)
    restored = scenario.factory()
    restored.restore(CONFIG)
    result = await restored.search(first(), deadline=deadline())
    assert result.cache_hit
    assert scenario.usage.concept_lookup_calls == 1
    assert len(scenario.port.calls) == 1


@pytest.mark.asyncio
async def test_eight_need_limit_and_noncritical_gap(scenario):
    policy = scenario.factory()
    host_needs = tuple(
        HostNeed(
            condition=f"condition-{i}",
            critical=False,
            affects_step="notes",
            origin="user_declared",
        )
        for i in range(8)
    )
    policy.create(CONFIG, host_needs=host_needs)
    with pytest.raises(DocumentPolicyDenied, match="eight"):
        await policy.search(first("ninth"), deadline=deadline())
    assert len(policy.needs) == 8
    reply = await policy.search(
        FirstQuery(question="Summarize these conditions"), deadline=deadline()
    )
    await policy.record_assessment(
        assessment(reply, status="missing", next_action="continue"), deadline=deadline()
    )
    assert policy.execution_gate("ready_for_verification").allowed
    assert len(policy.journal.state.unresolved(tuple(n.id for n in policy.needs))) == 8


@pytest.mark.asyncio
async def test_coding_dependencies_and_resume_snapshot_share_the_same_counter(scenario):
    from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
    from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
    from abaqus_sim_agent.coding.resume import DependencyResumeState

    request = CodingRunRequest(
        run_id="run-test",
        goal="Fake document boundary",
        workspace=scenario.workspace,
        state_dir=scenario.state,
        runtime=RuntimeProfile(launcher_path=scenario.state / "fake-launcher.bat"),
    )
    deps = CodingAgentDependencies(request=request, runner=SimpleNamespace())
    policy = DocumentPolicy(
        state_dir=scenario.state,
        workspace=scenario.workspace,
        lock=scenario.lock,
        port=scenario.port,
        validator=scenario.validator,
        usage=deps,
    )
    policy.create(CONFIG)
    await policy.search(first(), deadline=deadline())
    assert deps.concept_lookup_calls == 1
    assert deps.abaqus_calls == 0
    restored_deps = CodingAgentDependencies(request=request, runner=SimpleNamespace())
    # The product snapshot can be stale; the policy reservation remains authoritative.
    DependencyResumeState().restore_into(restored_deps)
    restored = DocumentPolicy(
        state_dir=scenario.state,
        workspace=scenario.workspace,
        lock=scenario.lock,
        port=scenario.port,
        validator=scenario.validator,
        usage=restored_deps,
    )
    restored.restore(CONFIG)
    assert restored_deps.concept_lookup_calls == 1
    assert restored_deps.abaqus_calls == 0


def test_invalid_creation_does_not_leave_a_half_initialized_policy(scenario):
    policy = scenario.factory()
    duplicate = HostNeed(condition="same", affects_step="build", origin="task_contract")
    with pytest.raises(DocumentPolicyDenied, match="duplicate"):
        policy.create(CONFIG, host_needs=(duplicate, duplicate))
    assert not (scenario.state / "document_policy.jsonl").exists()
    policy.create(CONFIG)
    assert policy.execution_gate("ready_for_verification").allowed


@pytest.mark.asyncio
async def test_technical_failure_cannot_be_retried_by_rewording_and_renaming_needs(
    scenario,
):
    policy = scenario.factory()
    policy.create(CONFIG, max_lookup_calls=8)
    scenario.port.kind = "unavailable"
    reply = await policy.search(first(), deadline=deadline())
    await policy.record_assessment(
        assessment(reply, status="missing", next_action="stop"), deadline=deadline()
    )
    rewritten = first("renamed condition").model_copy(
        update={"question": "Same question phrased differently"}
    )
    with pytest.raises(DocumentPolicyDenied, match="technical lookup failure"):
        await policy.search(rewritten, deadline=deadline())
    restored = scenario.factory()
    restored.restore(CONFIG)
    with pytest.raises(DocumentPolicyDenied, match="technical lookup failure"):
        await restored.search(rewritten, deadline=deadline())
    assert len(scenario.port.calls) == 1
    assert scenario.usage.concept_lookup_calls == 1
