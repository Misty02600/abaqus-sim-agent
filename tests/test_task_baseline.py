import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.cli import run_cli
from abaqus_sim_agent.coding.contracts import ClarificationRequest
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.model import create_deepseek_chat_model
from abaqus_sim_agent.coding.run_lock import CodingRunAlreadyLocked
from abaqus_sim_agent.evaluation.task_baseline import (
    REVIEW_ITEMS,
    TASK_IDS,
    THICKNESS_LINE,
    HumanReview,
    ReviewCheck,
    TaskTrialReview,
    ThicknessClarificationFixture,
    baseline_execution_config,
    compose_baseline_agent,
    planned_slots,
    prepare_task,
    task_candidates,
)
from abaqus_sim_agent.evaluation.task_baseline_runner import (
    EnvironmentUnavailable,
    OfflineBatch,
    ProviderFixtureTimeout,
    record_review,
    run_offline_batch,
    summarize_batch,
)
from abaqus_sim_agent.execution import Abaqus2024Runner
from tests.test_coding_amendments import _persist_base_state


def question(field="thickness", text="What is the plate thickness in mm?"):
    return ClarificationRequest(
        field_id=field,
        kind="missing_physical_input",
        question=text,
        impact="Required section input",
    )


def test_candidates_have_blocked_execution_and_paired_visible_facts():
    complete, missing = prepare_task(TASK_IDS[0]), prepare_task(TASK_IDS[2])
    assert (
        complete.workspace_files["requirements.md"]
        == missing.workspace_files["requirements.md"] + THICKNESS_LINE.encode()
    )
    assert complete.candidate.goal == missing.candidate.goal
    assert "plate_thickness" not in missing.candidate.goal
    assert THICKNESS_LINE not in missing.workspace_files["requirements.md"].decode()
    assert "human_review_checklist_and_reference_ready" in complete.pending_gates
    assert complete.reference_verifier is not None
    assert complete.candidate.verification_scope == "execution_evidence"
    assert not complete.clarification.enabled
    assert missing.clarification.enabled
    assert len(planned_slots()) == len(set(planned_slots())) == 12
    assert planned_slots()[:4] == tuple(f"{task}/r1" for task in TASK_IDS)
    candidates = task_candidates()
    assert sum(t.candidate_budget.max_model_requests for t in candidates) * 3 == 360
    assert sum(t.candidate_budget.max_abaqus_calls for t in candidates) * 3 == 114
    assert all(t.readiness == "offline_candidate_not_sealed" for t in candidates)
    for task in (TASK_IDS[1], TASK_IDS[3]):
        with pytest.raises(ValueError, match="verified source"):
            prepare_task(task)


def test_semantic_slot_answers_original_field_once_and_retains_gaps():
    fixture = ThicknessClarificationFixture(enabled=True)
    assert fixture.get_answer(question("thickness_mm")).field_id == "thickness_mm"
    assert fixture.get_answer(question("plate_thickness")) is None
    assert fixture.observations[-1].classification == "slot_consumed"
    fixture = ThicknessClarificationFixture(enabled=True)
    for q in (
        question("thickness", "What is Young's modulus?"),
        question("plate_t", "What is plate thickness?"),
        question("thickness", "Thickness and width in mm?"),
        question("thickness", "Thickness in inches?"),
    ):
        assert fixture.get_answer(q) is None
        assert fixture.observations[-1].classification == "adapter_gap"
    assert not fixture.consumed
    assert fixture.get_answer(question(text="请提供板厚，单位mm。")).value == "1 mm"


def test_artifact_acceptance_does_not_rewrite_terminal_status():
    review = TaskTrialReview(
        slot=planned_slots()[0],
        terminal="interrupted",
        cause="provider",
        artifact_acceptance="pass",
        execution_acceptance="pass",
        numeric_acceptance="pass",
        interaction_acceptance="pass",
        authority_acceptance="pass",
        human_review=HumanReview(
            source="fixture",
            reviewer="offline fixture",
            reviewed_at=datetime.now(UTC),
            checks={
                item: ReviewCheck(
                    state="pass",
                    reason="synthetic review",
                    evidence=("state/oracle.json",),
                )
                for item in REVIEW_ITEMS
            },
        ),
        evidence_references=("state/oracle.json",),
    )
    assert not review.full_delivery_passed
    assert review.artifact_acceptance == "pass"
    assert review.terminal == "interrupted"
    assert review.model_copy(
        update={"terminal": "completed", "cause": "none"}
    ).full_delivery_passed
    assert not review.model_copy(
        update={
            "terminal": "completed",
            "cause": "none",
            "authority_acceptance": "fail",
        }
    ).full_delivery_passed


@pytest.mark.usefixtures("mock_model_requests")
def test_actual_cli_and_baseline_http_configuration_parity_with_sdk_retry(
    tmp_path, monkeypatch
):
    bodies = []
    clients = []

    def response(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(
                503,
                headers={"retry-after-ms": "1"},
                json={"error": {"message": "offline transient"}},
            )
        names = [t["function"]["name"] for t in body["tools"]]
        # CLI writes an artifact, then reaches typed completion; evaluator stops after one request.
        if len(bodies) == 2:
            name, args = "write_file", {"path": "result.py", "content": "value = 1\n"}
        elif len(bodies) == 3:
            name = next(n for n in names if n.endswith("ReadyForVerificationDecision"))
            args = {
                "status": "ready_for_verification",
                "summary": "offline ready",
                "deliverables": [{"relative_path": "result.py"}],
            }
        else:
            name = next(n for n in names if n.endswith("FailedDecision"))
            args = {
                "status": "failed",
                "summary": "offline stop",
                "failure_stage": "offline",
            }
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": f"call-{len(bodies)}",
                                    "type": "function",
                                    "function": {
                                        "name": name,
                                        "arguments": json.dumps(args),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )

    def client_factory(**kwargs):
        client = AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.deepseek_gateway.AsyncOpenAI", client_factory
    )
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.take_gateway_api_key_from_environment",
        lambda _: SecretStr("offline"),
    )
    work = tmp_path / "cli-work"
    work.mkdir()
    state = tmp_path / "cli-state"
    catalog = tmp_path / "api-catalog"
    ApiCatalog([], runtime_release="2024").write(catalog)
    assert (
        run_cli(
            [
                "--goal",
                "Create result.py",
                "--workspace",
                str(work),
                "--state-dir",
                str(state),
                "--run-id",
                "parity",
                "--model-provider",
                "opencode-go",
            ]
        )
        == 0
    )
    outcome = json.loads((state / "outcome.json").read_text())
    assert outcome["usage"]["model_requests"] == 2
    assert len(bodies) == 3  # SDK 503 retry is not an extra logical request.

    task_root = tmp_path / "eval"
    task_root.mkdir()
    request = _persist_base_state(task_root)

    async def evaluate():
        config = baseline_execution_config()
        assert config.http_retries == 10
        assert config.http_timeout_seconds == 60.0
        model, client = create_deepseek_chat_model(
            gateway=config.model_provider,
            model_name=config.model_name,
            api_key=SecretStr("offline"),
            conversation_id=request.run_id,
            http_retries=config.http_retries,
            timeout_seconds=config.http_timeout_seconds,
            execution_config=config,
        )
        agent = compose_baseline_agent(
            model, request, configuration=config, question_protocol="ask_user_v1"
        )
        deps = CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(
                request.workspace,
                request.state_dir,
                launcher=request.runtime.launcher_path,
            ),
        )
        try:
            await agent.run(request.goal, deps=deps)
            agent.model_settings = {"extra_body": {"thinking": {"type": "disabled"}}}
            with pytest.raises(ValueError, match="thinking override"):
                await agent.run(request.goal, deps=deps)
        finally:
            await client.close()

    asyncio.run(evaluate())
    assert len(bodies) == 4
    assert bodies[0] == bodies[1]
    for body in bodies:
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "high"
        assert "max_tokens" not in body
        assert "max_completion_tokens" not in body
    # Ordinary product sessions expose the coding shell and background Solver
    # contract; the frozen evaluator retains its original runtime tool schema.
    product_tools = {t["function"]["name"]: t for t in bodies[1]["tools"]}
    evaluator_tools = {t["function"]["name"]: t for t in bodies[3]["tools"]}
    assert {
        "final_result_ResponseDecision",
        "run_command",
        "start_command",
        "check_command",
        "stop_command",
        "solver_job",
    } <= product_tools.keys()
    assert "run_solver_job" not in product_tools
    assert "run_solver_job" in evaluator_tools
    assert (
        not {
            "final_result_ResponseDecision",
            "run_command",
            "start_command",
            "check_command",
            "stop_command",
            "solver_job",
        }
        & evaluator_tools.keys()
    )
    assert "write_plan" not in evaluator_tools
    assert "write_plan" not in product_tools
    for name in product_tools.keys() & evaluator_tools.keys():
        assert product_tools[name] == evaluator_tools[name]
    assert [(c.max_retries, c.timeout) for c in clients] == [
        (4, 300.0),
        (10, 60.0),
    ]


def _fixture_result(context, **changes):
    path = context.state_dir / "evidence.json"
    path.write_text(
        json.dumps({"fixture": True, "slot": context.slot}), encoding="utf-8"
    )
    (context.workspace / "output.txt").write_text(context.slot, encoding="utf-8")
    values = {
        "slot": context.slot,
        "terminal": "completed",
        "cause": "none",
        "artifact_acceptance": "pass",
        "execution_acceptance": "pass",
        "numeric_acceptance": "pass",
        "interaction_acceptance": "pass",
        "authority_acceptance": "pass",
        "evidence_references": (f"trials/{context.slot}/state/evidence.json",),
        "logical_requests": 0,
        "http_attempts": 0,
        "runtime_calls": 0,
    }
    return TaskTrialReview(**(values | changes))


def _fixture_review(slot, **changes):
    check = ReviewCheck(
        state="pass",
        reason="Offline test fixture, not human review",
        evidence=(f"trials/{slot}/state/evidence.json",),
    )
    return HumanReview(
        source="fixture",
        reviewer="pytest fixture",
        reviewed_at=datetime.now(UTC),
        checks={name: changes.get(name, check) for name in REVIEW_ITEMS},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 2])
async def test_offline_batch_order_isolation_and_incremental_persistence(
    tmp_path, concurrency, monkeypatch
):
    root = tmp_path / "batch"
    active = peak = 0
    starts, ends = [], []

    def no_provider(**kwargs):
        raise AssertionError("offline batch must not create a provider client")

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.model.create_deepseek_chat_model", no_provider
    )

    async def fake(context):
        nonlocal active, peak
        repeat = int(context.slot[-1])
        if repeat > 1:
            assert sum(s.endswith(f"r{repeat - 1}") for s in ends) == 4
        starts.append(context.slot)
        active += 1
        peak = max(peak, active)
        # Completion order differs from declared order under concurrency 2.
        await asyncio.sleep(0.025 if context.slot.startswith("requirements") else 0.001)
        if ends:
            saved = json.loads((root / "batch.json").read_text(encoding="utf-8"))
            saved_by_slot = {r["slot"]: r for r in saved["records"]}
            assert saved_by_slot[ends[0]]["terminal"] == "completed"
        active -= 1
        ends.append(context.slot)
        return _fixture_result(context)

    summary = await run_offline_batch(root, fake, max_concurrency=concurrency)
    assert starts == list(planned_slots())
    assert peak == concurrency
    assert summary["counts"] == {"pass": 0, "fail": 0, "unknown": 12}
    assert summary["started"] == 12
    assert summary["purpose"] == "fixture_only_not_agent_benchmark"
    for slot in planned_slots():
        trial = root / "trials" / slot
        assert (trial / "workspace/output.txt").read_text() == slot
        assert (
            json.loads((trial / "state/trial-result.json").read_text())["slot"] == slot
        )
    if concurrency == 2:
        assert ends != starts


@pytest.mark.asyncio
async def test_exception_counts_do_not_disappear_and_no_retry(tmp_path):
    calls = []

    async def fake(context):
        calls.append(context.slot)
        if context.slot.startswith("missing-input"):
            raise ProviderFixtureTimeout("offline provider timeout")
        return _fixture_result(context)

    root = tmp_path / "batch"
    summary = await run_offline_batch(root, fake)
    assert calls == list(planned_slots())
    assert summary["counts"] == {"pass": 0, "fail": 3, "unknown": 9}
    assert sum(r["cause"] == "provider" for r in summary["rows"]) == 3
    assert summary["costs"]["http_attempts"]["unknown_trials"] == 3
    assert (
        root / "trials" / planned_slots()[2] / "state/framework-error.txt"
    ).is_file()


@pytest.mark.asyncio
async def test_environment_failure_stops_new_slots_without_replacing_them(tmp_path):
    calls = []

    async def fake(context):
        calls.append(context.slot)
        if context.slot == planned_slots()[1]:
            raise EnvironmentUnavailable("offline environment unavailable")
        return _fixture_result(context)

    root = tmp_path / "batch"
    summary = await run_offline_batch(root, fake)
    assert calls == list(planned_slots()[:2])
    assert summary["status"] == "stopped"
    assert summary["started"] == 2
    assert summary["counts"] == {"pass": 0, "fail": 1, "unknown": 11}
    assert all(r["terminal"] == "not_started" for r in summary["rows"][2:])


@pytest.mark.asyncio
async def test_batch_cancellation_keeps_completed_active_and_unstarted_slots(tmp_path):
    running = asyncio.Event()

    async def fake(context):
        if context.slot == planned_slots()[1]:
            running.set()
            await asyncio.Event().wait()
        return _fixture_result(context)

    root = tmp_path / "batch"
    job = asyncio.create_task(run_offline_batch(root, fake))
    await asyncio.wait_for(running.wait(), 5)
    with pytest.raises(CodingRunAlreadyLocked):
        summarize_batch(root)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    summary = summarize_batch(root)
    assert summary["status"] == "interrupted"
    assert [r["terminal"] for r in summary["rows"][:3]] == [
        "completed",
        "interrupted",
        "not_started",
    ]
    assert summary["planned"] == 12
    assert summary["started"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["raise", "self_cancel", "external_child_cancel"])
async def test_child_cancellation_propagates_as_batch_cancellation(tmp_path, mode):
    started = asyncio.Event()
    child = None

    async def fake(context):
        nonlocal child
        if context.slot == planned_slots()[1]:
            child = asyncio.current_task()
            started.set()
            if mode == "raise":
                raise asyncio.CancelledError
            if mode == "self_cancel":
                child.cancel()
            await asyncio.Event().wait()
        return _fixture_result(context)

    root = tmp_path / "batch"
    job = asyncio.create_task(run_offline_batch(root, fake))
    await asyncio.wait_for(started.wait(), 5)
    if mode == "external_child_cancel":
        child.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job, 5)
    summary = summarize_batch(root)
    assert summary["status"] == "interrupted"
    assert summary["started"] == 2
    assert [r["terminal"] for r in summary["rows"][:3]] == [
        "completed",
        "interrupted",
        "not_started",
    ]


@pytest.mark.asyncio
async def test_child_cancellation_stops_parallel_peer_and_keeps_unstarted_slots(
    tmp_path,
):
    peer_started = asyncio.Event()
    peer_cancelled = asyncio.Event()

    async def fake(context):
        if context.slot == planned_slots()[1]:
            await peer_started.wait()
            raise asyncio.CancelledError
        if context.slot == planned_slots()[2]:
            peer_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                peer_cancelled.set()
        return _fixture_result(context)

    root = tmp_path / "batch"
    job = asyncio.create_task(run_offline_batch(root, fake, max_concurrency=2))
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job, 5)
    assert peer_cancelled.is_set()
    summary = summarize_batch(root)
    assert summary["status"] == "interrupted"
    assert summary["started"] == 3
    assert [r["terminal"] for r in summary["rows"][:4]] == [
        "completed",
        "interrupted",
        "interrupted",
        "not_started",
    ]


@pytest.mark.asyncio
async def test_review_and_rescore_preserve_machine_results_and_do_not_rerun(tmp_path):
    calls = 0

    async def fake(context):
        nonlocal calls
        calls += 1
        return _fixture_result(context)

    root = tmp_path / "batch"
    await run_offline_batch(root, fake)
    slot = planned_slots()[0]
    original_path = root / "trials" / slot / "state/trial-result.json"
    original = original_path.read_bytes()
    summary = record_review(root, slot, _fixture_review(slot))
    assert summary["counts"] == {"pass": 1, "fail": 0, "unknown": 11}
    assert summary["confirmed_pass_per_planned"] == 1 / 12
    reviewed = (root / "batch.json").read_bytes()
    assert summarize_batch(root)["counts"]["pass"] == 1
    assert (root / "batch.json").read_bytes() == reviewed
    assert original_path.read_bytes() == original
    failed_check = ReviewCheck(
        state="fail",
        reason="Fixture: wrong load direction",
        evidence=(f"trials/{slot}/state/evidence.json",),
    )
    summary = record_review(
        root, slot, _fixture_review(slot, loads_constraints=failed_check)
    )
    assert summary["counts"] == {"pass": 0, "fail": 1, "unknown": 11}
    assert len(list((root / "review-history").glob("*.json"))) == 2
    assert original_path.read_bytes() == original
    assert calls == 12


@pytest.mark.asyncio
async def test_artifact_pass_after_interruption_is_not_delivery_pass(tmp_path):
    async def fake(context):
        return _fixture_result(context, terminal="interrupted", cause="provider")

    root = tmp_path / "batch"
    await run_offline_batch(root, fake)
    summary = record_review(
        root, planned_slots()[0], _fixture_review(planned_slots()[0])
    )
    assert summary["counts"] == {"pass": 0, "fail": 12, "unknown": 0}
    original = json.loads(
        (root / "trials" / planned_slots()[0] / "state/trial-result.json").read_text()
    )
    assert original["artifact_acceptance"] == "pass"
    assert original["terminal"] == "interrupted"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["missing", "changed", "facts"])
async def test_missing_or_changed_evidence_cannot_retain_pass(tmp_path, mutation):
    async def fake(context):
        return _fixture_result(context)

    root = tmp_path / "batch"
    await run_offline_batch(root, fake)
    slot = planned_slots()[0]
    record_review(root, slot, _fixture_review(slot))
    path = root / "trials" / slot / "state/evidence.json"
    if mutation == "missing":
        path.unlink()
    elif mutation == "changed":
        path.write_text("changed offline fixture")
    else:
        data = json.loads((root / "batch.json").read_text())
        data["records"][0]["logical_requests"] = 999
        (root / "batch.json").write_text(json.dumps(data), encoding="utf-8")
    summary = summarize_batch(root)
    assert summary["counts"]["pass"] == 0
    assert summary["rows"][0]["integrity_error"]
    if mutation == "facts":
        assert summary["costs"]["logical_requests"]["known_sum"] == 0
        assert summary["costs"]["logical_requests"]["unknown_trials"] == 1


@pytest.mark.asyncio
async def test_rejects_self_review_cross_slot_evidence_and_reusing_batch(tmp_path):
    async def fake(context):
        return _fixture_result(context, human_review=_fixture_review(context.slot))

    root = tmp_path / "batch"
    summary = await run_offline_batch(root, fake)
    assert summary["counts"]["fail"] == 12
    before = (root / "batch.json").read_bytes()
    with pytest.raises(FileExistsError):
        await run_offline_batch(root, fake)
    assert (root / "batch.json").read_bytes() == before
    with pytest.raises(ValueError, match="another slot"):
        record_review(root, planned_slots()[0], _fixture_review(planned_slots()[1]))


def test_review_schema_and_duplicate_slots_fail_closed():
    with pytest.raises(ValueError, match="reason and evidence"):
        ReviewCheck(state="pass")
    with pytest.raises(ValueError, match="six review items"):
        HumanReview(
            source="fixture",
            reviewer="fixture",
            reviewed_at=datetime.now(UTC),
            checks={},
        )
    with pytest.raises(ValueError, match="twelve unique slots"):
        OfflineBatch(
            batch_id="fixture",
            max_concurrency=1,
            slots=(planned_slots()[0],) * 12,
            records=(),
        )


@pytest.mark.asyncio
async def test_parallel_fatal_error_finishes_active_peer_but_starts_no_new_task(
    tmp_path,
):
    first_started = asyncio.Event()
    calls = []

    async def fake(context):
        calls.append(context.slot)
        if context.slot == planned_slots()[0]:
            first_started.set()
            await asyncio.sleep(0.02)
            return _fixture_result(context)
        await first_started.wait()
        raise EnvironmentUnavailable("fixture: stop queue")

    summary = await run_offline_batch(tmp_path / "batch", fake, max_concurrency=2)
    assert calls == list(planned_slots()[:2])
    assert summary["status"] == "stopped"
    assert summary["rows"][0]["terminal"] == "completed"
    assert summary["rows"][1]["terminal"] == "failed"
    assert all(r["terminal"] == "not_started" for r in summary["rows"][2:])


@pytest.mark.asyncio
async def test_numeric_failure_is_not_overridden_by_fixture_review(tmp_path):
    async def fake(context):
        return _fixture_result(context, numeric_acceptance="fail")

    root = tmp_path / "batch"
    await run_offline_batch(root, fake)
    summary = record_review(
        root, planned_slots()[0], _fixture_review(planned_slots()[0])
    )
    assert summary["counts"] == {"pass": 0, "fail": 12, "unknown": 0}


@pytest.mark.asyncio
async def test_offline_batch_rejects_real_human_claim_and_unknown_review(tmp_path):
    async def fake(context):
        return _fixture_result(context)

    root = tmp_path / "batch"
    await run_offline_batch(root, fake)
    slot = planned_slots()[0]
    with pytest.raises(ValueError, match="settled offline"):
        record_review(
            root, slot, _fixture_review(slot).model_copy(update={"source": "human"})
        )
    with pytest.raises(ValueError, match="settled offline"):
        record_review(root, "unknown-slot", _fixture_review(slot))
    assert summarize_batch(root)["reviewed"] == 0


@pytest.mark.asyncio
async def test_missing_task_evidence_is_recorded_as_error_without_dropping_slot(
    tmp_path,
):
    async def fake(context):
        return _fixture_result(
            context, evidence_references=(f"trials/{context.slot}/state/missing.txt",)
        )

    summary = await run_offline_batch(tmp_path / "batch", fake)
    assert summary["started"] == 12
    assert summary["counts"] == {"pass": 0, "fail": 12, "unknown": 0}


@pytest.mark.asyncio
async def test_bad_report_evaluator_is_not_silently_ignored(tmp_path, monkeypatch):
    from pydantic_evals.reporting.analyses import ScalarResult

    def fail_analysis(**kwargs):
        raise ValueError("offline injected scorer error")

    monkeypatch.setattr(
        "abaqus_sim_agent.evaluation.task_baseline_runner.ScalarResult", fail_analysis
    )

    async def fake(context):
        return _fixture_result(context)

    root = tmp_path / "batch"
    with pytest.raises(RuntimeError, match="report evaluator failed"):
        await run_offline_batch(root, fake)
    monkeypatch.setattr(
        "abaqus_sim_agent.evaluation.task_baseline_runner.ScalarResult", ScalarResult
    )
    summary = summarize_batch(root)
    assert summary["status"] == "interrupted"
    assert summary["started"] == 4
    assert all(r["terminal"] == "not_started" for r in summary["rows"][4:])
