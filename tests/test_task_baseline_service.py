import asyncio
import hashlib
import json
from dataclasses import replace

import pytest
from pydantic import BaseModel, ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from tests.test_beam_section_change_evaluation import _make_source_run as beam_source
from tests.test_plate_change_evaluation import _make_source_run as plate_source
from tests.test_task_baseline import _fixture_review

from abaqus_sim_agent.coding.contracts import CodingRunOutcome
from abaqus_sim_agent.coding.resume import CodingRunResumeError, load_resume_state
from abaqus_sim_agent.evaluation.task_baseline import (
    TASK_IDS,
    baseline_execution_config,
    planned_slots,
    prepare_task,
)
from abaqus_sim_agent.evaluation.task_baseline_runner import (
    TrialContext,
    record_review,
    run_offline_batch,
)
from abaqus_sim_agent.evaluation.task_baseline_service import (
    _tool_argument_failure,
    run_service_fixture,
)
from abaqus_sim_agent.execution import ProcessOutput
from abaqus_sim_agent.execution.abaqus2024 import WindowsProcessStrategy


class FakeAbaqusProcess:
    def __init__(self, stem):
        self.stem = stem
        self.calls = []

    async def run(self, command, *, cwd, timeout_seconds):
        self.calls.append((tuple(command), cwd, timeout_seconds))
        marker = f"fixture-{len(self.calls)}"
        if "cae" in command:
            (cwd / f"{self.stem}.cae").write_text(marker)
            (cwd / f"{self.stem}.inp").write_text(marker)
        elif "python" in command:
            (cwd / "results.json").write_text(
                json.dumps({"fixture": True, "stage": marker})
            )
        else:
            (cwd / f"{self.stem}.odb").write_text(marker)
            (cwd / f"{self.stem}.sta").write_text(
                "THE ANALYSIS HAS COMPLETED SUCCESSFULLY"
            )
            if "'RF3'" in (cwd / "build_model.py").read_text():
                (cwd / f"{self.stem}.dat").write_text(
                    "***WARNING: OUTPUT REQUEST RF3 IS NOT VALID SINCE IT REQUESTS OUTPUT AT A DOF THAT IS NOT ACTIVE\n"
                )
                return ProcessOutput(
                    exit_code=0,
                    stdout=b"***WARNING: OUTPUT REQUEST RF3 IS NOT VALID SINCE IT REQUESTS OUTPUT AT A DOF THAT IS NOT ACTIVE\n",
                    stderr=b"",
                )
            (cwd / f"{self.stem}.dat").write_text("")
        return ProcessOutput(
            exit_code=0, stdout=b"offline simulated process", stderr=b""
        )


def prepared_case(tmp_path, task_id):
    if task_id == TASK_IDS[1]:
        return prepare_task(
            task_id, source_run_dir=beam_source(tmp_path / "source-beam")
        )
    if task_id == TASK_IDS[3]:
        source = plate_source(tmp_path / "source-plate")
        build = b"left_set = object()\nmodel.FieldOutputRequest(name='F', createStepName='T', variables=('U', 'S', 'RF'))\n"
        (source / "workspace/build_model.py").write_bytes(build)
        outcome_file = source / "state/outcome.json"
        outcome = CodingRunOutcome.model_validate_json(outcome_file.read_bytes())
        outcome = outcome.model_copy(
            update={
                "artifacts": tuple(
                    a.model_copy(
                        update={
                            "size_bytes": len(build),
                            "sha256": hashlib.sha256(build).hexdigest(),
                        }
                    )
                    if a.relative_path == "build_model.py"
                    else a
                    for a in outcome.artifacts
                )
            }
        )
        outcome_file.write_text(outcome.model_dump_json(), encoding="utf-8")
        return prepare_task(task_id, source_run_dir=source)
    return prepare_task(task_id)


def context_for(tmp_path, task_id, repetition=1):
    slot = f"{task_id}/r{repetition}"
    root = tmp_path / "trials" / slot
    workspace, state = root / "workspace", root / "state"
    workspace.mkdir(parents=True)
    state.mkdir()
    return TrialContext(slot, workspace, state)


def tool(name, args):
    return ModelResponse(parts=[ToolCallPart(name, json.dumps(args))])


def scripted_model(
    prepared, *, missing_manifest=False, omit_runtime=False, crash_after=None
):
    candidate = prepared.candidate
    steps = []
    if candidate.seed_kind == "none":
        steps.append(("read_file", {"path": "requirements.md"}))
    if candidate.task_id == TASK_IDS[2]:
        steps.append(
            (
                "final_result_NeedsInputDecision",
                {
                    "status": "needs_input",
                    "summary": "Need physical input",
                    "clarification": {
                        "field_id": "thickness_mm",
                        "kind": "missing_physical_input",
                        "question": "板厚是多少，单位mm？",
                        "impact": "Required section property",
                    },
                },
            )
        )
    if candidate.task_id == TASK_IDS[3] and not omit_runtime:
        steps += [
            ("run_cae_script", {"script_path": "build_model.py"}),
            (
                "run_solver_job",
                {
                    "job_name": candidate.artifact_stem,
                    "input_file": candidate.artifact_stem + ".inp",
                },
            ),
        ]
    steps.append(
        (
            "write_file",
            {
                "path": "build_model.py",
                "content": "profile = (20, 10)\n# Offline fixture, not an Abaqus model\n",
            },
        )
    )
    if candidate.seed_kind == "none":
        steps.append(
            (
                "write_file",
                {
                    "path": "extract_results.py",
                    "content": "# Offline extractor fixture\nvalue = 1\n",
                },
            )
        )
    if omit_runtime:
        steps += [
            (
                "write_file",
                {"path": f"{candidate.artifact_stem}.{suffix}", "content": "fixture"},
            )
            for suffix in ("cae", "inp", "odb")
        ]
        steps.append(("write_file", {"path": "results.json", "content": "{}"}))
    else:
        steps += [
            ("run_cae_script", {"script_path": "build_model.py"}),
            (
                "run_solver_job",
                {
                    "job_name": candidate.artifact_stem,
                    "input_file": candidate.artifact_stem + ".inp",
                },
            ),
            ("run_abaqus_python", {"script_path": "extract_results.py"}),
        ]
    steps.append(("read_file", {"path": "results.json"}))
    declared = ("results.json",) if missing_manifest else candidate.new_deliverables
    steps.append(
        (
            "final_result_ReadyForVerificationDecision",
            {
                "status": "ready_for_verification",
                "summary": "Offline fixture artifacts ready",
                "deliverables": [{"relative_path": p} for p in declared],
            },
        )
    )
    index = 0

    def reply(messages, info):
        nonlocal index
        assert info.model_settings["extra_body"]["thinking"] == {"type": "enabled"}
        assert info.model_settings["openai_reasoning_effort"] == "high"
        names = {t.name for t in info.function_tools}
        assert "write_plan" in names
        assert "run_command" not in names
        if crash_after is not None and index == crash_after:
            raise ConnectionError("offline injected provider interruption")
        if index >= len(steps):
            return tool(
                "final_result_FailedDecision",
                {
                    "status": "failed",
                    "summary": "fixture exhausted",
                    "failure_stage": "fixture",
                },
            )
        name, args = steps[index]
        index += 1
        if candidate.task_id == TASK_IDS[2] and name == "write_file":
            assert "The user supplied thickness_mm: 1 mm" in str(messages)
        return tool(name, args)

    return FunctionModel(reply)


@pytest.mark.asyncio
@pytest.mark.parametrize("task_id", TASK_IDS)
async def test_real_service_all_four_cards_with_fake_model_and_process(
    tmp_path, task_id, monkeypatch
):
    prepared = prepared_case(tmp_path, task_id)
    context = context_for(tmp_path, task_id)
    process = FakeAbaqusProcess(prepared.candidate.artifact_stem)

    async def no_oracle(*args, **kwargs):
        raise AssertionError("reference oracle must not run inside service")

    monkeypatch.setattr(prepared.reference_verifier, "verify", no_oracle)
    row = await run_service_fixture(
        context, prepared, model=scripted_model(prepared), process_strategy=process
    )
    assert row.terminal == "completed"
    assert row.observation_kind == "service_fixture"
    assert row.service_verification_scope == "execution_evidence"
    assert row.artifact_acceptance == "pass"
    assert row.execution_acceptance == "pass"
    assert row.numeric_acceptance == "unknown"
    assert row.authority_acceptance == "unknown"
    assert row.verdict == "unknown"
    assert row.runtime_calls == (5 if task_id == TASK_IDS[3] else 3)
    assert row.solver_calls == (2 if task_id == TASK_IDS[3] else 1)
    assert row.http_attempts == 0
    assert row.human_review is None
    state = context.state_dir / "coding-run"
    launch = json.loads((state / "launch.json").read_text())
    assert launch["reasoning_policy"] == baseline_execution_config().reasoning_policy
    assert launch["http_retries"] == 10
    assert launch["http_timeout_seconds"] == 60.0
    assert launch["planning_enabled"] is True
    request = json.loads((state / "request.json").read_text(encoding="utf-8"))
    assert request["limits"]["model_requests"] == {
        "mode": "limited",
        "value": prepared.candidate.candidate_budget.max_model_requests,
    }
    assert "4200" not in request["goal"]
    for path in prepared.candidate.preserved_inputs:
        assert (context.workspace / path).read_bytes() == prepared.workspace_files[path]
    if task_id == TASK_IDS[2]:
        assert "板厚为 1 mm" not in (context.workspace / "requirements.md").read_text(
            encoding="utf-8"
        )
        resume = load_resume_state(state)
        assert resume.clarifications[0].field_id == "thickness_mm"
        assert resume.clarifications[0].abaqus_calls_before_answer == 0
    for journal in state.rglob("*.jsonl"):
        for line in journal.read_bytes().splitlines():
            json.loads(line)


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["missing_manifest", "omit_runtime"])
async def test_product_completion_does_not_imply_complete_task(tmp_path, variant):
    prepared = prepare_task(TASK_IDS[0])
    context = context_for(tmp_path, TASK_IDS[0])
    row = await run_service_fixture(
        context,
        prepared,
        model=scripted_model(prepared, **{variant: True}),
        process_strategy=FakeAbaqusProcess("plate_tension"),
    )
    assert row.terminal == "completed"
    assert row.execution_acceptance == "fail"
    assert row.verdict == "fail"
    outcome = json.loads((context.state_dir / "coding-run/outcome.json").read_text())
    assert outcome["status"] == "completed"
    assert outcome["verification_scope"] == "execution_evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_after", [2, 6])
async def test_provider_failure_preserves_same_run_state_and_usage(
    tmp_path, crash_after
):
    prepared = prepare_task(TASK_IDS[0])
    context = context_for(tmp_path, TASK_IDS[0])
    row = await run_service_fixture(
        context,
        prepared,
        model=scripted_model(prepared, crash_after=crash_after),
        process_strategy=FakeAbaqusProcess("plate_tension"),
    )
    assert row.terminal == "interrupted"
    assert row.cause == "provider"
    resume = load_resume_state(context.state_dir / "coding-run")
    assert row.logical_requests == resume.usage.requests
    assert row.logical_requests >= 2
    assert row.artifact_acceptance == ("pass" if crash_after == 6 else "fail")
    assert row.verdict == "fail"
    assert row.numeric_acceptance == "unknown"
    assert (context.workspace / "build_model.py").is_file()
    assert not (context.state_dir / "coding-run/outcome.json").exists()
    before = (context.state_dir / "coding-run/request.json").read_bytes()
    with pytest.raises(CodingRunResumeError, match="contains a CodingRun"):
        await run_service_fixture(
            context,
            prepared,
            model=scripted_model(prepared),
            process_strategy=FakeAbaqusProcess("plate_tension"),
        )
    assert (context.state_dir / "coding-run/request.json").read_bytes() == before


@pytest.mark.asyncio
async def test_budget_exhaustion_and_failed_decision_are_not_recovered_by_oracle(
    tmp_path,
):
    prepared = prepare_task(TASK_IDS[0])
    budget = prepared.candidate.candidate_budget.model_copy(
        update={"max_model_requests": 1}
    )
    prepared = replace(
        prepared,
        candidate=prepared.candidate.model_copy(update={"candidate_budget": budget}),
    )
    context = context_for(tmp_path, TASK_IDS[0])
    row = await run_service_fixture(
        context,
        prepared,
        model=scripted_model(prepared),
        process_strategy=FakeAbaqusProcess("plate_tension"),
    )
    assert row.terminal == "failed"
    assert row.cause == "budget"
    assert row.host_oracle_calls == 0
    assert row.numeric_acceptance == "unknown"


@pytest.mark.asyncio
async def test_unknown_clarification_alias_is_adapter_gap_not_fake_answer(tmp_path):
    prepared = prepare_task(TASK_IDS[2])
    context = context_for(tmp_path, TASK_IDS[2])
    model = FunctionModel(
        lambda messages, info: tool(
            "final_result_NeedsInputDecision",
            {
                "status": "needs_input",
                "summary": "missing thickness",
                "clarification": {
                    "field_id": "plate_t",
                    "kind": "missing_physical_input",
                    "question": "板厚是多少？",
                    "impact": "section stiffness",
                },
            },
        )
    )
    row = await run_service_fixture(
        context,
        prepared,
        model=model,
        process_strategy=FakeAbaqusProcess("plate_tension"),
    )
    assert row.terminal == "failed"
    assert row.cause == "adapter_gap"
    assert row.runtime_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", [WindowsProcessStrategy(), None, False])
async def test_service_fixture_rejects_actual_windows_process_strategy(
    tmp_path, strategy
):
    prepared = prepare_task(TASK_IDS[0])
    context = context_for(tmp_path, TASK_IDS[0])
    with pytest.raises(ValueError, match="non-Windows fake"):
        await run_service_fixture(
            context,
            prepared,
            model=scripted_model(prepared),
            process_strategy=strategy,
        )
    assert not (context.state_dir / "coding-run").exists()


@pytest.mark.asyncio
async def test_service_adapter_runs_in_existing_parallel_batch_and_review_is_posthoc(
    tmp_path,
):
    prepared = {task_id: prepared_case(tmp_path, task_id) for task_id in TASK_IDS}
    calls = []

    async def task(context):
        item = prepared[context.slot.split("/")[0]]
        calls.append(context.slot)
        return await run_service_fixture(
            context,
            item,
            model=scripted_model(item),
            process_strategy=FakeAbaqusProcess(item.candidate.artifact_stem),
        )

    root = tmp_path / "batch"
    summary = await run_offline_batch(root, task, max_concurrency=2)
    assert summary["counts"] == {"pass": 0, "fail": 0, "unknown": 12}
    assert len(calls) == 12
    slot = planned_slots()[0]
    evidence = f"trials/{slot}/state/service-checks.json"
    review = _fixture_review(slot)
    review = review.model_copy(
        update={
            "checks": {
                name: check.model_copy(update={"evidence": (evidence,)})
                for name, check in review.checks.items()
            }
        }
    )
    original_paths = [
        p
        for p in (root / "trials" / slot / "state/coding-run").rglob("*")
        if p.is_file()
    ]
    before = {p: p.read_bytes() for p in original_paths}
    summary = record_review(root, slot, review)
    assert summary["counts"] == {"pass": 1, "fail": 0, "unknown": 11}
    assert {p: p.read_bytes() for p in original_paths} == before
    assert len(calls) == 12
    # Cached task definitions must not share consumed clarification state across repeats.
    for repetition in range(1, 4):
        resume = load_resume_state(
            root / "trials" / TASK_IDS[2] / f"r{repetition}" / "state/coding-run"
        )
        assert len(resume.clarifications) == 1


@pytest.mark.asyncio
async def test_cancelled_service_receipt_preserves_usage_in_batch(tmp_path):
    signal = asyncio.Event()
    prepared = prepare_task(TASK_IDS[0])

    async def response(messages, info):
        signal.set()
        await asyncio.Event().wait()

    async def task(context):
        return await run_service_fixture(
            context,
            prepared,
            model=FunctionModel(response),
            process_strategy=FakeAbaqusProcess("plate_tension"),
        )

    root = tmp_path / "batch"
    job = asyncio.create_task(run_offline_batch(root, task))
    await asyncio.wait_for(signal.wait(), 10)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    batch = json.loads((root / "batch.json").read_text())
    row = batch["records"][0]
    assert row["terminal"] == "interrupted"
    assert row["observation_kind"] == "service_fixture"
    assert row["logical_requests"] is not None
    assert row["evidence_references"]


@pytest.mark.asyncio
async def test_preserved_input_file_tool_guard_is_still_active(tmp_path):
    prepared = prepared_case(tmp_path, TASK_IDS[1])
    context = context_for(tmp_path, TASK_IDS[1])
    base = scripted_model(prepared)
    first = True

    def reply(messages, info):
        nonlocal first
        if first:
            first = False
            return tool(
                "write_file",
                {"path": "extract_results.py", "content": "changed = True\n"},
            )
        return base.function(messages, info)

    row = await run_service_fixture(
        context,
        prepared,
        model=FunctionModel(reply),
        process_strategy=FakeAbaqusProcess("beam_tension"),
    )
    assert row.terminal == "completed"
    assert (
        context.workspace / "extract_results.py"
    ).read_bytes() == prepared.workspace_files["extract_results.py"]
    history = (context.state_dir / "coding-run/history.json").read_text(
        encoding="utf-8"
    )
    assert "immutable for this task" in history
    assert row.runtime_calls == 3


@pytest.mark.asyncio
async def test_runtime_mutation_of_preserved_input_fails_postcheck_not_product_outcome(
    tmp_path,
):
    class MutatingProcess(FakeAbaqusProcess):
        async def run(self, command, *, cwd, timeout_seconds):
            result = await super().run(
                command, cwd=cwd, timeout_seconds=timeout_seconds
            )
            if "cae" in command:
                (cwd / "extract_results.py").write_text("changed = True\n")
            return result

    prepared = prepared_case(tmp_path, TASK_IDS[1])
    context = context_for(tmp_path, TASK_IDS[1])
    row = await run_service_fixture(
        context,
        prepared,
        model=scripted_model(prepared),
        process_strategy=MutatingProcess("beam_tension"),
    )
    assert row.terminal == "completed"
    assert row.artifact_acceptance == "fail"
    assert row.verdict == "fail"
    assert (
        json.loads((context.state_dir / "coding-run/outcome.json").read_text())[
            "status"
        ]
        == "completed"
    )


@pytest.mark.asyncio
async def test_explicit_failed_decision_is_a_task_failure_not_provider_error(tmp_path):
    prepared = prepare_task(TASK_IDS[0])
    context = context_for(tmp_path, TASK_IDS[0])
    model = FunctionModel(
        lambda messages, info: tool(
            "final_result_FailedDecision",
            {
                "status": "failed",
                "failure_stage": "modeling",
                "summary": "Fixture cannot finish this task",
            },
        )
    )
    row = await run_service_fixture(
        context,
        prepared,
        model=model,
        process_strategy=FakeAbaqusProcess("plate_tension"),
    )
    assert row.terminal == "failed"
    assert row.cause == "task"
    assert row.runtime_calls == 0
    assert row.host_oracle_calls == 0


@pytest.mark.asyncio
async def test_service_preparation_refuses_preexisting_workspace_content(tmp_path):
    prepared = prepare_task(TASK_IDS[0])
    context = context_for(tmp_path, TASK_IDS[0])
    existing = context.workspace / "keep.txt"
    existing.write_text("user content")
    with pytest.raises(ValueError, match="workspace must be empty"):
        await run_service_fixture(
            context,
            prepared,
            model=scripted_model(prepared),
            process_strategy=FakeAbaqusProcess("plate_tension"),
        )
    assert existing.read_text() == "user content"
    assert not (context.workspace / "requirements.md").exists()


@pytest.mark.asyncio
async def test_warning_repair_without_reproduced_warning_is_not_accepted(tmp_path):
    class NoWarningProcess(FakeAbaqusProcess):
        async def run(self, command, *, cwd, timeout_seconds):
            result = await super().run(
                command, cwd=cwd, timeout_seconds=timeout_seconds
            )
            if "cae" not in command and "python" not in command:
                (cwd / f"{self.stem}.dat").write_text("")
                return ProcessOutput(
                    exit_code=0, stdout=b"no warning fixture", stderr=b""
                )
            return result

    prepared = prepared_case(tmp_path, TASK_IDS[3])
    context = context_for(tmp_path, TASK_IDS[3])
    row = await run_service_fixture(
        context,
        prepared,
        model=scripted_model(prepared),
        process_strategy=NoWarningProcess("plate_tension"),
    )
    assert row.terminal == "completed"
    assert row.execution_acceptance == "fail"
    checks = json.loads((context.state_dir / "service-checks.json").read_text())
    assert not checks["repair_execution_checks"]["original_seed_warning_reproduced"]


def test_argument_diagnosis_does_not_relabel_unrelated_validation_or_retry_errors():
    class Required(BaseModel):
        field: str

    with pytest.raises(ValidationError) as caught:
        Required.model_validate({})
    validation = caught.value
    assert _tool_argument_failure(validation) is None
    malformed_response = UnexpectedModelBehavior(
        "Invalid response from chat completions"
    )
    malformed_response.__cause__ = validation
    assert _tool_argument_failure(malformed_response) is None
    retry = UnexpectedModelBehavior(
        "Tool 'write_file' exceeded max retries count of 2."
    )
    retry.__cause__ = RuntimeError("not a parameter validation failure")
    assert _tool_argument_failure(retry) is None
    retry.__cause__ = retry
    assert _tool_argument_failure(retry) is None


@pytest.mark.asyncio
async def test_unasked_missing_input_is_failed_interaction_even_when_budget_fails(
    tmp_path,
):
    prepared = prepare_task(TASK_IDS[2])
    prepared = replace(
        prepared,
        candidate=prepared.candidate.model_copy(
            update={
                "candidate_budget": prepared.candidate.candidate_budget.model_copy(
                    update={"max_model_requests": 2}
                ),
            }
        ),
    )
    context = context_for(tmp_path, TASK_IDS[2])
    steps = iter(
        [
            tool(
                "write_file", {"path": "build_model.py", "content": "THICKNESS = 1.0\n"}
            ),
            tool("run_cae_script", {"script_path": "build_model.py"}),
        ]
    )
    row = await run_service_fixture(
        context,
        prepared,
        model=FunctionModel(lambda messages, info: next(steps)),
        process_strategy=FakeAbaqusProcess("plate_tension"),
    )
    assert row.terminal == "failed"
    assert row.cause == "budget"
    assert row.interaction_acceptance == "fail"
    assert row.runtime_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("crash_after", "interaction"), [(0, "unknown"), (2, "pass")])
async def test_provider_interruption_does_not_erase_observed_clarification(
    tmp_path, crash_after, interaction
):
    prepared = prepare_task(TASK_IDS[2])
    row = await run_service_fixture(
        context_for(tmp_path, TASK_IDS[2]),
        prepared,
        model=scripted_model(prepared, crash_after=crash_after),
        process_strategy=FakeAbaqusProcess("plate_tension"),
    )
    assert row.terminal == "interrupted"
    assert row.cause == "provider"
    assert row.runtime_calls == 0
    assert row.interaction_acceptance == interaction
