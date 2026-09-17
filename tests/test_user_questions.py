import asyncio
import json

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from tests.test_task_baseline_service import FakeAbaqusProcess

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.amendments import append_task_revision
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.launch import LaunchManifest
from abaqus_sim_agent.coding.resume import (
    create_step_store,
    load_effective_run_for_resume,
    load_resume_state,
)
from abaqus_sim_agent.coding.service import (
    CodingRunInfrastructureError,
    CodingRunResult,
    CodingRunService,
)
from abaqus_sim_agent.coding.user_questions import (
    UserAnswerInput,
    WaitingForUser,
    cancel_question,
    inspect_question,
    load_questions,
    submit_answer,
)
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.evaluation.task_baseline import baseline_execution_config
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.skills import skill_library_path


def call(name, args, call_id="call"):
    return ModelResponse(parts=[ToolCallPart(name, args, tool_call_id=call_id)])


@pytest.mark.parametrize(
    "mode", ["full", "automatic_verified_only", "automatic_exact_signature"]
)
def test_cli_catalog_questions_resume_preserves_schema_binding_and_lookup_budget(
    tmp_path, monkeypatch, mode
):
    from pydantic import SecretStr

    from abaqus_sim_agent.api_catalog import ApiCatalog, ApiMethodCard
    from abaqus_sim_agent.coding.cli import run_cli

    catalog_dir = tmp_path / "catalog"
    catalog = ApiCatalog(
        [
            ApiMethodCard(
                card_id=f"api:2024:Part.{name}",
                entity_kind="method",
                canonical_name=f"Part.{name}",
                declaring_name=f"Part.{name}",
                class_name="Part",
                method_name=name,
            )
            for name in ("setElementType", "Set", "seedEdgeByNumber")
        ],
        runtime_release="2024",
    )
    catalog.write(catalog_dir)
    state, workspace = tmp_path / "state", tmp_path / "workspace"
    workspace.mkdir()
    steps = []
    if mode == "full":
        steps.append(("lookup_abaqus_api", {"name": "Part.setElementType"}))
    steps.append(("ask_user", {"question": "Thickness?"}))
    if mode == "full":
        steps += [
            ("lookup_abaqus_api", {"name": name})
            for name in ("Part.setElementType", "Part.Set", "Part.seedEdgeByNumber")
        ]
    steps += [
        ("write_file", {"path": "out.txt", "content": "done"}),
        (
            "final_result_ReadyForVerificationDecision",
            {
                "status": "ready_for_verification",
                "summary": "done",
                "deliverables": [{"relative_path": "out.txt"}],
            },
        ),
    ]
    calls = 0

    def reply(messages, info):
        nonlocal calls
        names = {tool.name for tool in info.function_tools}
        assert "ask_user" in names
        assert ("lookup_abaqus_api" in names) == (mode == "full")
        assert "final_result_NeedsInputDecision" not in {
            tool.name for tool in info.output_tools
        }
        if (state / "user_questions.json").exists():
            assert any(
                isinstance(part, ToolReturnPart)
                and part.tool_name == "ask_user"
                and part.content == "1 mm"
                for message in messages
                for part in message.parts
            )
        name, args = steps[calls]
        calls += 1
        return call(name, args, f"c{calls}")

    class Client:
        async def close(self):
            pass

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.create_deepseek_chat_model",
        lambda **kwargs: (FunctionModel(reply), Client()),
    )
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.take_gateway_api_key_from_environment",
        lambda *args: SecretStr("fixture"),
    )
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert (
        run_cli(
            [
                "--goal",
                "Write out.txt after asking",
                "--workspace",
                str(workspace),
                "--state-dir",
                str(state),
                "--api-catalog-dir",
                str(catalog_dir),
                "--api-catalog-mode",
                mode,
                "--max-api-lookup-calls",
                "2",
            ]
        )
        == 2
    )
    launch_bytes = (state / "launch.json").read_bytes()
    assert json.loads(launch_bytes)["question_protocol"] == "ask_user_v1"
    question = load_questions(state, load_resume_state(state).run_id).questions[0]
    answer = tmp_path / "answer.json"
    answer.write_text(
        json.dumps({"question_id": question.question_id, "answer": "1 mm"}),
        encoding="utf-8",
    )
    assert run_cli(["--resume", str(state), "--answer-file", str(answer)]) == 0
    assert (state / "launch.json").read_bytes() == launch_bytes
    resumed = load_resume_state(state)
    assert resumed.usage.requests == len(steps)
    assert resumed.dependencies.api_lookup_calls == (2 if mode == "full" else 0)
    if mode == "full":
        traces = [
            json.loads(line)
            for line in (state / "api_lookups.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert [trace["status"] for trace in traces] == [
            "found",
            "duplicate",
            "found",
            "unavailable",
        ]
    assert len(load_questions(state, resumed.run_id).questions) == 1

    # A mutated release must fail before another model/client is constructed.
    def forbidden(**kwargs):
        raise AssertionError("catalog drift must be rejected before model creation")

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.create_deepseek_chat_model", forbidden
    )
    cards = catalog_dir / "methods.jsonl"
    cards.write_bytes(cards.read_bytes() + b"\n")
    assert run_cli(["--resume", str(state)]) == 1
    assert (state / "launch.json").read_bytes() == launch_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("ask_first", [True, False])
async def test_question_mixed_with_catalog_lookup_has_no_lookup_effect(
    tmp_path, ask_first
):
    from abaqus_sim_agent.api_catalog import ApiCatalog, ApiMethodCard

    turns = 0

    def reply(messages, info):
        nonlocal turns
        turns += 1
        ask = ToolCallPart(
            "ask_user", {"question": "Thickness?"}, tool_call_id=f"q{turns}"
        )
        lookup = ToolCallPart(
            "lookup_abaqus_api", {"name": "Part.Set"}, tool_call_id="lookup"
        )
        return ModelResponse(
            parts=([ask, lookup] if ask_first else [lookup, ask])
            if turns == 1
            else [ask]
        )

    service = service_for(tmp_path, reply)
    catalog = ApiCatalog(
        [
            ApiMethodCard(
                card_id="api:2024:Part.Set",
                entity_kind="method",
                canonical_name="Part.Set",
                declaring_name="Part.Set",
                class_name="Part",
                method_name="Set",
            )
        ],
        runtime_release="2024",
    )
    service.dependencies.api_catalog = catalog
    service.agent = create_abaqus_coding_agent(
        FunctionModel(reply),
        service.request,
        step_store=service.step_store,
        question_protocol="ask_user_v1",
        api_catalog_mode="full",
    )
    result = await service.run_step()
    assert isinstance(result, WaitingForUser)
    assert result.question.question_id == "q2"
    assert service.dependencies.api_lookup_calls == 0
    assert not (service.request.state_dir / "api_lookups.jsonl").exists()


def service_for(tmp_path, reply, *, resume=False):
    state = tmp_path / "state"
    if resume:
        effective = load_effective_run_for_resume(state)
        request, initial = effective.request, effective.initial_snapshot
    else:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        state.mkdir()
        request = CodingRunRequest(
            run_id="questions-test",
            goal="Write a result after obtaining the missing input.",
            workspace=workspace.resolve(),
            state_dir=state.resolve(),
            runtime=RuntimeProfile(launcher_path=(state / "fixture.bat").resolve()),
        )
        initial = WorkspaceResolver().snapshot(workspace)
    config = baseline_execution_config()
    launch = LaunchManifest(
        schema_version="coding-run-launch-v4",
        question_protocol="ask_user_v1",
        skill_library=skill_library_path().resolve(),
        **config.model_dump(),
    )
    store = create_step_store(state)
    agent = create_abaqus_coding_agent(
        FunctionModel(reply),
        request,
        step_store=store,
        execution_config=config,
        question_protocol="ask_user_v1",
    )
    runner = Abaqus2024Runner(
        request.workspace,
        request.state_dir,
        process_strategy=FakeAbaqusProcess("plate_tension"),
    )
    return CodingRunService(
        agent=agent,
        request=request,
        dependencies=CodingAgentDependencies(
            request=request, runner=runner, step_store=store
        ),
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=ExecutionEvidenceVerifier(),
        initial_snapshot=initial,
        step_store=store,
        question_protocol="ask_user_v1",
        launch_manifest_json=None if resume else launch.model_dump_json(),
        resume=resume,
        expected_amendment_sequence=effective.amendment_sequence if resume else None,
    )


@pytest.mark.asyncio
async def test_wait_answer_resume_without_reasking_or_spending_wait_as_active_wall(
    tmp_path,
):
    service = service_for(
        tmp_path,
        lambda messages, info: call("ask_user", {"question": "Plate thickness?"}, "q1"),
    )
    wait = await service.run_step()
    assert isinstance(wait, WaitingForUser)
    state = service.request.state_dir
    assert load_resume_state(state).status == "waiting_for_user"
    assert not (state / "outcome.json").exists()
    assert (await inspect_question(state)).question_id == "q1"

    def forbidden(messages, info):
        raise AssertionError("unanswered resume must not contact model")

    again = await service_for(tmp_path, forbidden, resume=True).run_step()
    assert isinstance(again, WaitingForUser)
    assert again.question.question_id == "q1"
    answer = UserAnswerInput(question_id="q1", answer="1 mm")
    await submit_answer(state, answer)
    before = (state / "user_questions.json").read_bytes()
    await submit_answer(state, answer)
    assert (state / "user_questions.json").read_bytes() == before
    steps = 0

    def finish(messages, info):
        nonlocal steps
        assert any(
            isinstance(p, ToolReturnPart)
            and p.tool_call_id == "q1"
            and p.content == "1 mm"
            for m in messages
            for p in m.parts
        )
        steps += 1
        return (
            call("write_file", {"path": "out.txt", "content": "result"}, "w")
            if steps == 1
            else call(
                "final_result_ReadyForVerificationDecision",
                {
                    "status": "ready_for_verification",
                    "summary": "done",
                    "deliverables": [{"relative_path": "out.txt"}],
                },
                "final",
            )
        )

    result = await service_for(tmp_path, finish, resume=True).run_step()
    assert isinstance(result, CodingRunResult)
    assert result.outcome.status == "completed"
    assert len(load_questions(state, service.request.run_id).questions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["ask_first", "write_first", "with_final"])
async def test_mixed_question_blocks_all_tool_effects_in_real_harness(tmp_path, order):
    turns = 0

    def reply(messages, info):
        nonlocal turns
        turns += 1
        ask = ToolCallPart(
            "ask_user", {"question": "Thickness?"}, tool_call_id=f"q{turns}"
        )
        write = ToolCallPart(
            "write_file", {"path": "bad.txt", "content": "bad"}, tool_call_id="w"
        )
        final = ToolCallPart(
            "final_result_FailedDecision",
            {"status": "failed", "summary": "stop", "failure_stage": "modeling"},
            tool_call_id="final",
        )
        return ModelResponse(
            parts={
                "ask_first": [ask, write],
                "write_first": [write, ask],
                "with_final": [ask, final],
            }[order]
            if turns == 1
            else [ask]
        )

    service = service_for(tmp_path, reply)
    result = await service.run_step()
    assert isinstance(result, WaitingForUser)
    assert not (service.request.workspace / "bad.txt").exists()
    assert result.question.question_id == "q2"


@pytest.mark.asyncio
async def test_late_answer_creates_one_revision_and_old_artifacts_are_stale(tmp_path):
    steps = iter(
        [
            call(
                "write_file", {"path": "build_model.py", "content": "# fixture\n"}, "w"
            ),
            call("run_cae_script", {"script_path": "build_model.py"}, "cae"),
            call("ask_user", {"question": "What new load should be used?"}, "q"),
        ]
    )
    service = service_for(tmp_path, lambda messages, info: next(steps))
    wait = await service.run_step()
    assert isinstance(wait, WaitingForUser)
    assert wait.question.revision_had_runtime
    state = service.request.state_dir
    original = (state / "request.json").read_bytes()
    with pytest.raises(ValueError, match="pending question"):
        append_task_revision(state, goal="new goal", reason="test")
    answer = UserAnswerInput(question_id="q", answer="200 N")
    await submit_answer(state, answer)
    await submit_answer(state, answer)
    effective = load_effective_run_for_resume(state)
    assert effective.task_revision == 1
    assert effective.revision_execution_start_index == 1
    assert len(effective.ledger.amendments) == 1
    assert (state / "request.json").read_bytes() == original
    turns = 0

    def finish(messages, info):
        nonlocal turns
        turns += 1
        assert any(
            "200 N" in str(getattr(p, "content", "")) for m in messages for p in m.parts
        )
        return (
            call(
                "final_result_ReadyForVerificationDecision",
                {
                    "status": "ready_for_verification",
                    "summary": "old file",
                    "deliverables": [{"relative_path": "plate_tension.inp"}],
                },
                "f",
            )
            if turns == 1
            else call(
                "final_result_FailedDecision",
                {
                    "status": "failed",
                    "failure_stage": "verification",
                    "summary": "old file cannot prove new requirements",
                },
                "fail",
            )
        )

    result = await service_for(tmp_path, finish, resume=True).run_step()
    assert isinstance(result, CodingRunResult)
    assert result.outcome.task_revision == 1
    assert result.outcome.status == "failed"
    assert turns == 2
    events = [
        json.loads(line)
        for line in (state / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    verification = next(e["data"] for e in events if e["event"] == "verification")
    assert not verification["passed"]
    assert "not fresh" in verification["feedback"]


@pytest.mark.asyncio
async def test_cancel_then_new_goal_is_not_a_fabricated_answer(tmp_path):
    service = service_for(
        tmp_path,
        lambda messages, info: call("ask_user", {"question": "Thickness?"}, "q"),
    )
    await service.run_step()
    state = service.request.state_dir
    (service.request.workspace / "user_note.txt").write_text("explicit revision input")
    await cancel_question(state, "q")
    append_task_revision(
        state,
        goal="Instead explain the script; do not execute it.",
        reason="test replacement",
    )
    assert await inspect_question(state) is None

    def finish(messages, info):
        text = str([getattr(p, "content", None) for m in messages for p in m.parts])
        assert "cancelled" in text
        assert "Instead explain" in text
        return call(
            "final_result_FailedDecision",
            {
                "status": "failed",
                "failure_stage": "fixture",
                "summary": "fixture stopped",
            },
            "end",
        )

    await service_for(tmp_path, finish, resume=True).run_step()
    question = load_questions(state, service.request.run_id).questions[0]
    assert question.cancelled
    assert question.answer is None


@pytest.mark.asyncio
async def test_answer_survives_provider_failure_after_tool_return(tmp_path):
    service = service_for(
        tmp_path,
        lambda messages, info: call("ask_user", {"question": "Thickness?"}, "q"),
    )
    await service.run_step()
    await submit_answer(
        service.request.state_dir, UserAnswerInput(question_id="q", answer="2 mm")
    )

    def broken(messages, info):
        raise ConnectionError("fixture provider interrupted")

    with pytest.raises(CodingRunInfrastructureError):
        await service_for(tmp_path, broken, resume=True).run_step()

    def finish(messages, info):
        answers = [
            p
            for m in messages
            for p in m.parts
            if isinstance(p, ToolReturnPart) and p.tool_call_id == "q"
        ]
        assert len(answers) == 1
        assert answers[0].content == "2 mm"
        return call(
            "final_result_FailedDecision",
            {
                "status": "failed",
                "failure_stage": "fixture",
                "summary": "fixture stopped",
            },
            "end",
        )

    await service_for(tmp_path, finish, resume=True).run_step()


@pytest.mark.asyncio
async def test_wrong_answer_id_conflict_and_workspace_drift_are_rejected(tmp_path):
    service = service_for(
        tmp_path,
        lambda messages, info: call("ask_user", {"question": "Thickness?"}, "q"),
    )
    await service.run_step()
    state = service.request.state_dir
    before = (state / "user_questions.json").read_bytes()
    with pytest.raises(ValueError, match="question id"):
        await submit_answer(state, UserAnswerInput(question_id="other", answer="1 mm"))
    assert (state / "user_questions.json").read_bytes() == before
    await submit_answer(state, UserAnswerInput(question_id="q", answer="1 mm"))
    with pytest.raises(ValueError, match="overwrite"):
        await submit_answer(state, UserAnswerInput(question_id="q", answer="2 mm"))
    (service.request.workspace / "unexpected.txt").write_text("user edit")
    with pytest.raises(ValueError, match="workspace changed"):
        await inspect_question(state)


def test_cli_default_asks_outside_event_loop_and_run_lock(tmp_path, monkeypatch):
    from pydantic import SecretStr

    from abaqus_sim_agent.coding.cli import run_cli
    from abaqus_sim_agent.coding.run_lock import CodingRunFileLock

    state, workspace = tmp_path / "state", tmp_path / "workspace"
    workspace.mkdir()
    calls = 0

    def reply(messages, info):
        nonlocal calls
        calls += 1
        assert "ask_user" in {t.name for t in info.function_tools}
        assert "final_result_NeedsInputDecision" not in {
            t.name for t in info.output_tools
        }
        if calls == 1:
            return call("ask_user", {"question": "Thickness?"}, "q")
        if calls == 2:
            return call("write_file", {"path": "out.txt", "content": "done"}, "w")
        return call(
            "final_result_ReadyForVerificationDecision",
            {
                "status": "ready_for_verification",
                "summary": "done",
                "deliverables": [{"relative_path": "out.txt"}],
            },
            "end",
        )

    class Client:
        async def close(self):
            pass

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.create_deepseek_chat_model",
        lambda **kwargs: (FunctionModel(reply), Client()),
    )
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.take_gateway_api_key_from_environment",
        lambda *args: SecretStr("fixture"),
    )

    def user_input(prompt):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        with CodingRunFileLock(state):
            assert load_resume_state(state).status == "waiting_for_user"
        return "1 mm"

    monkeypatch.setattr("builtins.input", user_input)
    assert (
        run_cli(
            [
                "--goal",
                "Write out.txt after asking",
                "--workspace",
                str(workspace),
                "--state-dir",
                str(state),
            ]
        )
        == 0
    )
    assert (
        json.loads((state / "launch.json").read_bytes())["question_protocol"]
        == "ask_user_v1"
    )


@pytest.mark.asyncio
async def test_answer_receipt_recovers_interrupted_revision_commit(
    tmp_path, monkeypatch
):
    from abaqus_sim_agent.coding import user_questions as questions

    steps = iter(
        [
            call(
                "write_file", {"path": "build_model.py", "content": "# fixture\n"}, "w"
            ),
            call("run_cae_script", {"script_path": "build_model.py"}, "cae"),
            call("ask_user", {"question": "New load?"}, "q"),
        ]
    )
    service = service_for(tmp_path, lambda messages, info: next(steps))
    await service.run_step()
    original = questions.ensure_answer_revision

    def crash(*args):
        raise OSError("fixture crash after durable answer")

    monkeypatch.setattr(questions, "ensure_answer_revision", crash)
    with pytest.raises(OSError, match="fixture crash"):
        await submit_answer(
            service.request.state_dir, UserAnswerInput(question_id="q", answer="200 N")
        )
    assert (
        load_questions(service.request.state_dir, service.request.run_id)
        .questions[-1]
        .answer
        == "200 N"
    )
    monkeypatch.setattr(questions, "ensure_answer_revision", original)
    assert await inspect_question(service.request.state_dir) is None
    assert load_effective_run_for_resume(service.request.state_dir).task_revision == 1
    assert await inspect_question(service.request.state_dir) is None
    assert (
        len(load_effective_run_for_resume(service.request.state_dir).ledger.amendments)
        == 1
    )


@pytest.mark.asyncio
async def test_wait_time_not_charged_to_active_wall(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from abaqus_sim_agent.coding import user_questions as questions

    service = service_for(
        tmp_path,
        lambda messages, info: call("ask_user", {"question": "Thickness?"}, "q"),
    )
    await service.run_step()
    before = load_resume_state(service.request.state_dir).active_wall_time_seconds

    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(hours=1)

    monkeypatch.setattr(questions, "datetime", FutureClock)
    await submit_answer(
        service.request.state_dir, UserAnswerInput(question_id="q", answer="1 mm")
    )
    assert (
        load_questions(service.request.state_dir, service.request.run_id).wait_seconds
        >= 3600
    )
    assert (
        load_resume_state(service.request.state_dir).active_wall_time_seconds == before
    )


@pytest.mark.asyncio
async def test_corrupt_question_ledger_is_not_overwritten(tmp_path):
    service = service_for(
        tmp_path,
        lambda messages, info: call("ask_user", {"question": "Thickness?"}, "q"),
    )
    await service.run_step()
    ledger = service.request.state_dir / "user_questions.json"
    ledger.write_bytes(b"broken")
    with pytest.raises(ValueError, match="Invalid JSON"):
        await inspect_question(service.request.state_dir)
    assert ledger.read_bytes() == b"broken"


@pytest.mark.asyncio
async def test_reconcile_precedes_answer_and_cancellation(tmp_path):
    from datetime import UTC, datetime

    from pydantic_ai_harness.step_persistence import ToolEffectRecord

    from abaqus_sim_agent.coding.resume import CodingRunReconciliationRequired

    service = service_for(
        tmp_path,
        lambda messages, info: call("ask_user", {"question": "Thickness?"}, "q"),
    )
    await service.run_step()
    store = service.step_store
    runs = await store.list_runs(conversation_id=service.request.run_id)
    await store.record_tool_effect(
        ToolEffectRecord(
            run_id=runs[-1].run_id,
            tool_call_id="unsettled",
            tool_name="run_solver_job",
            status="started",
            started_at=datetime.now(UTC),
        )
    )
    before = (service.request.state_dir / "user_questions.json").read_bytes()
    with pytest.raises(CodingRunReconciliationRequired):
        await submit_answer(
            service.request.state_dir, UserAnswerInput(question_id="q", answer="1 mm")
        )
    assert load_resume_state(service.request.state_dir).status == "needs_reconcile"
    with pytest.raises(ValueError, match="reconciliation required"):
        await cancel_question(service.request.state_dir, "q")
    assert (service.request.state_dir / "user_questions.json").read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["empty", "eof", "cancel"])
async def test_cli_resume_without_answer_stays_pending_without_a_model(
    tmp_path, monkeypatch, mode
):
    from abaqus_sim_agent.coding.cli import run_cli

    service = service_for(
        tmp_path,
        lambda messages, info: call("ask_user", {"question": "Thickness?"}, "q"),
    )
    await service.run_step()
    # run_cli is synchronous; execute it after this fixture's event loop is done.
    state = service.request.state_dir

    def forbidden(*args, **kwargs):
        raise AssertionError("must not construct a model for a pending question")

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.create_deepseek_chat_model", forbidden
    )

    def unanswered(prompt):
        if mode == "eof":
            raise EOFError
        if mode == "cancel":
            raise KeyboardInterrupt
        return ""

    monkeypatch.setattr("builtins.input", unanswered)
    before = load_resume_state(state).active_wall_time_seconds
    assert await asyncio.to_thread(run_cli, ["--resume", str(state)]) == 2
    assert load_resume_state(state).status == "waiting_for_user"
    assert load_resume_state(state).active_wall_time_seconds == before


@pytest.mark.asyncio
async def test_cli_answer_file_continues_without_console(tmp_path, monkeypatch):
    from pydantic import SecretStr

    from abaqus_sim_agent.coding.cli import run_cli

    service = service_for(
        tmp_path,
        lambda messages, info: call("ask_user", {"question": "Thickness?"}, "q"),
    )
    await service.run_step()
    answer_file = tmp_path / "answer.json"
    answer_file.write_text(json.dumps({"question_id": "q", "answer": "1 mm"}))

    class Client:
        async def close(self):
            pass

    def reply(messages, info):
        assert any(
            isinstance(p, ToolReturnPart) and p.content == "1 mm"
            for m in messages
            for p in m.parts
        )
        return call(
            "final_result_FailedDecision",
            {
                "status": "failed",
                "summary": "fixture stopped after answer",
                "failure_stage": "fixture",
            },
            "end",
        )

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.create_deepseek_chat_model",
        lambda **kwargs: (FunctionModel(reply), Client()),
    )
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.take_gateway_api_key_from_environment",
        lambda *args: SecretStr("fixture"),
    )
    monkeypatch.setattr(
        "builtins.input", lambda prompt: pytest.fail("must use answer file")
    )
    assert (
        await asyncio.to_thread(
            run_cli,
            [
                "--resume",
                str(service.request.state_dir),
                "--answer-file",
                str(answer_file),
            ],
        )
        == 1
    )
    assert (
        load_questions(service.request.state_dir, service.request.run_id)
        .questions[-1]
        .source
        == "file"
    )


@pytest.mark.asyncio
async def test_pending_question_is_answered_and_resumed_in_a_fresh_python_process(
    tmp_path,
):
    import sys

    service = service_for(
        tmp_path,
        lambda messages, info: call("ask_user", {"question": "Thickness?"}, "q"),
    )
    await service.run_step()
    script = r"""
import asyncio, sys
from pathlib import Path
from tests.test_user_questions import service_for, call
from abaqus_sim_agent.coding.user_questions import inspect_question, submit_answer, UserAnswerInput
from pydantic_ai.messages import ToolReturnPart

async def main():
    root = Path(sys.argv[1])
    question = await inspect_question(root / "state")
    assert question.question_id == "q"
    await submit_answer(root / "state", UserAnswerInput(question_id="q", answer="1 mm"))
    requests = 0
    def reply(messages, info):
        nonlocal requests
        requests += 1
        assert any(isinstance(p, ToolReturnPart) and p.tool_name == "ask_user" and p.content == "1 mm" for m in messages for p in m.parts)
        if requests == 1:
            return call("write_file", {"path": "resumed.txt", "content": "fresh-process result"}, "w")
        return call("final_result_ReadyForVerificationDecision", {"status": "ready_for_verification", "summary": "done", "deliverables": [{"relative_path": "resumed.txt"}]}, "final")
    result = await service_for(root, reply, resume=True).run_step()
    assert result.outcome.status == "completed"
    print("fresh-process-resume-ok")
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=0x08000000 if sys.platform == "win32" else 0,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, stderr.decode(errors="replace")
    assert b"fresh-process-resume-ok" in stdout
    assert load_resume_state(service.request.state_dir).status == "completed"
