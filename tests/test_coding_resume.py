import asyncio
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic_ai import RunUsage
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_harness.step_persistence import RunRecord

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.resume import (
    CodingRunResumeError,
    DependencyResumeState,
    RunUsageSnapshot,
    _drop_unresolved_response,
    append_execution_result,
    create_step_store,
    ensure_new_run_state_available,
    load_execution_results,
    select_resume_history,
)
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
    AbaqusInvocation,
    ProcessOutput,
)


class _SuccessfulProcess:
    async def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> ProcessOutput:
        del command, cwd, timeout_seconds
        return ProcessOutput(exit_code=0, stdout=b"CAE complete\n", stderr=b"")


@pytest.mark.parametrize(
    "marker",
    [
        "amendments.json",
        "launch.json",
        "request.json",
        "initial_snapshot.json",
        "resume_state.json",
        "step-persistence",
    ],
)
def test_new_run_never_overwrites_owned_state_marker(
    tmp_path: Path,
    marker: str,
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    target = state_dir / marker
    if marker == "step-persistence":
        target.mkdir()
        (target / "sentinel").write_bytes(b"preserve")
    else:
        target.write_bytes(b"preserve")

    with pytest.raises(CodingRunResumeError, match=marker):
        ensure_new_run_state_available(state_dir)

    if target.is_dir():
        assert (target / "sentinel").read_bytes() == b"preserve"
    else:
        assert target.read_bytes() == b"preserve"


def _request(tmp_path: Path) -> CodingRunRequest:
    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    state_dir.mkdir()
    return CodingRunRequest(
        run_id="resume-state-test",
        goal="Create an Abaqus model.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(
            launcher_path=Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
        ),
    )


def _execution(request: CodingRunRequest) -> AbaqusExecutionResult:
    stdout_path = request.state_dir / "stdout.bin"
    stderr_path = request.state_dir / "stderr.bin"
    stdout_path.write_bytes(b"ok")
    stderr_path.write_bytes(b"")
    return AbaqusExecutionResult(
        invocation=AbaqusInvocation.SOLVER_JOB,
        command=("abq2024.bat", "job=plate", "input=plate.inp", "interactive"),
        workspace=request.workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout="ok",
        stderr="",
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=stdout_path,
        stderr_raw_path=stderr_path,
    )


def test_usage_snapshot_round_trips_all_budget_fields() -> None:
    usage = RunUsage(
        requests=3,
        tool_calls=4,
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=2,
        cost=Decimal("0.12"),
        details={"provider_retry": 1},
    )

    restored = RunUsageSnapshot.capture(usage).restore()

    assert restored == usage


def test_history_fallback_drops_only_dangling_tool_call_response() -> None:
    completed = ModelResponse(parts=[TextPart("completed response")])
    dangling = ModelResponse(
        parts=[
            ToolCallPart(
                "write_file",
                {"path": "result.py", "content": "answer = 42"},
                tool_call_id="dangling-write",
            )
        ]
    )

    assert _drop_unresolved_response((completed, dangling)) == (completed,)
    assert _drop_unresolved_response((completed,)) == (completed,)


def test_corrupt_tool_effect_ledger_fails_closed(tmp_path: Path) -> None:
    request = _request(tmp_path)
    store = create_step_store(request.state_dir)
    run_id = f"{request.run_id}-turn-1"
    asyncio.run(
        store.register_run(RunRecord(run_id=run_id, conversation_id=request.run_id))
    )
    effects_path = (
        request.state_dir / "step-persistence" / run_id / "tool_effects.jsonl"
    )
    effects_path.write_text("ll}\n", encoding="utf-8")

    with pytest.raises(CodingRunResumeError, match="cannot be proven safe"):
        asyncio.run(select_resume_history(store, request))


def test_execution_journal_restores_provenance_and_budget(tmp_path: Path) -> None:
    request = _request(tmp_path)
    execution = _execution(request)
    append_execution_result(request.state_dir, execution)

    journaled = load_execution_results(request.state_dir)
    assert journaled == (execution,)

    restored = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
    )
    DependencyResumeState().restore_into(
        restored,
        journaled_executions=journaled,
    )

    assert restored.executions == [execution]
    assert restored.abaqus_calls == 1
    assert restored.solver_calls == 1
    assert restored.concept_trigger_source == "solver"
    assert restored.concept_trigger_outcome == "success"
    assert restored.concept_trigger_reference == "execution:0"
    assert restored.concept_trigger_evidence_sha256 is not None


def test_concept_lookup_trigger_context_survives_resume(tmp_path: Path) -> None:
    request = _request(tmp_path)
    failed_execution = _execution(request).model_copy(update={"exit_code": 1})
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
    )
    dependencies.current_turn = 2
    dependencies.record_runtime_execution(failed_execution)

    context = dependencies.concept_lookup_context(tool_call_id="lookup-after-solver")
    assert context.turn == 2
    assert context.trigger_source == "solver"
    assert context.trigger_outcome == "failure"
    assert context.trigger_reference == "execution:0"
    assert context.trigger_evidence_sha256 is not None
    assert context.execution_count_before_lookup == 1

    snapshot = DependencyResumeState.capture(dependencies)
    restored = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
    )
    snapshot.restore_into(restored)

    restored_context = restored.concept_lookup_context(tool_call_id="resumed-lookup")
    assert restored_context.trigger_source == "solver"
    assert restored_context.trigger_outcome == "failure"
    assert restored_context.trigger_reference == "execution:0"
    assert restored_context.trigger_evidence_sha256 == context.trigger_evidence_sha256
    assert restored_context.execution_count_before_lookup == 1


def test_timed_out_runtime_is_not_classified_as_domain_failure(tmp_path: Path) -> None:
    request = _request(tmp_path)
    timed_out = _execution(request).model_copy(
        update={"exit_code": None, "timed_out": True}
    )
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
    )

    dependencies.record_runtime_execution(timed_out)

    context = dependencies.concept_lookup_context(tool_call_id="after-timeout")
    assert context.trigger_source == "solver"
    assert context.trigger_outcome == "interrupted"


def test_runtime_tool_records_effect_identity_before_external_execution(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    (request.workspace / "build.py").write_text(
        "print('build')\n",
        encoding="utf-8",
    )
    store = create_step_store(request.state_dir)
    call_count = 0

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_cae_script",
                        {"script_path": "build.py"},
                        tool_call_id="cae-effect",
                    )
                ]
            )
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Stop after the runtime probe.",
                    },
                )
            ]
        )

    runner = Abaqus2024Runner(
        request.workspace,
        request.state_dir,
        process_strategy=_SuccessfulProcess(),
    )
    dependencies = CodingAgentDependencies(
        request=request,
        runner=runner,
        step_store=store,
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        step_store=store,
    )

    asyncio.run(
        agent.run(
            request.goal,
            deps=dependencies,
            conversation_id=request.run_id,
            run_id=f"{request.run_id}-turn-1",
        )
    )

    [run_record] = asyncio.run(store.list_runs(conversation_id=request.run_id))
    effect = asyncio.run(
        store.get_tool_effect(
            run_id=run_record.run_id,
            tool_call_id="cae-effect",
        )
    )
    assert effect is not None
    assert effect.status == "completed"
    assert effect.idempotency_key is not None
    assert "cae_nogui::-::build.py::" in effect.idempotency_key
    assert effect.effect_summary == "cae_nogui path=build.py"
    assert len(load_execution_results(request.state_dir)) == 1
