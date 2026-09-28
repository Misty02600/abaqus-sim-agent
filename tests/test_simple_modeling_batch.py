import asyncio
import json
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from abaqus_sim_agent.evaluation import simple_modeling_batch as pair
from abaqus_sim_agent.evaluation.serial_runtime import SerialRuntime
from abaqus_sim_agent.evaluation.task_baseline import (
    planned_slots,
    prepare_task,
    simple_slots,
)
from abaqus_sim_agent.evaluation.task_baseline_runner import (
    summarize_batch,
)
from abaqus_sim_agent.execution import ProcessOutput
from abaqus_sim_agent.execution.abaqus2024 import WindowsProcessStrategy
from tests.support.abaqus import FakeAbaqusProcess


def spec_fixture(tmp_path, *, human_confirmed=False):
    launcher = tmp_path / "fake.bat"
    launcher.write_text("fixture only")
    path, root = tmp_path / "spec.json", tmp_path / "batch"
    spec = pair.prepare_spec(path, root, launcher)
    if not human_confirmed:
        data = spec.model_dump(mode="json")
        data["schema_version"] = "simple-modeling-pair-spec-v2"
        data.pop("answer_mode")
        spec = pair.SimpleModelingSpec.model_validate_json(json.dumps(data))
        path.write_text(spec.model_dump_json(), encoding="utf-8")
    return path, root, spec


def install_resources(
    monkeypatch,
    *,
    wrong_bar=False,
    skip_clarification=False,
    unmapped_question=False,
    human_confirmed=False,
):
    requests, calls = {}, []
    both_started = asyncio.Event()
    both_at_cae = asyncio.Event()
    cae_ready = set()
    active = maximum = 0
    processes = {s: FakeAbaqusProcess(s) for s in ("bar_tension", "plate_tension")}

    async def reply(request):
        body = json.loads(request.content)
        session = request.headers["x-opencode-session"]
        bar = "bar_tension.cae" in json.dumps(body, ensure_ascii=False)
        task_id = "requirements-bar3d-v1" if bar else "missing-input-plate-v1"
        stem = "bar_tension" if bar else "plate_tension"
        history = requests.setdefault(session, [])
        index = len(history)
        history.append(body)
        if len(requests) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), 5)
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "high"
        assert "max_tokens" not in body
        assert not {"run_command", "search_abaqus_docs", "lookup_abaqus_api"} & {
            t["function"]["name"] for t in body["tools"]
        }
        steps = [("read_file", {"path": "requirements.md"})]
        if not bar and not skip_clarification:
            steps.append(
                (
                    "final_result_NeedsInputDecision",
                    {
                        "status": "needs_input",
                        "summary": "Need thickness",
                        "clarification": {
                            "field_id": "thickness_mm",
                            "kind": "missing_physical_input",
                            "question": "板厚是多少，单位mm？",
                            "impact": "required",
                        },
                    },
                )
            )
            if index > 1:
                assert "1 mm" in json.dumps(body)
        steps += [
            (
                "write_file",
                {
                    "path": "build_model.py",
                    "content": f"# {task_id} fixture\nvalue = 1\n",
                },
            ),
            (
                "write_file",
                {"path": "extract_results.py", "content": "# fixture\nvalue = 2\n"},
            ),
            ("run_cae_script", {"script_path": "build_model.py"}),
            ("run_solver_job", {"job_name": stem, "input_file": f"{stem}.inp"}),
            ("run_abaqus_python", {"script_path": "extract_results.py"}),
            ("read_file", {"path": "results.json"}),
            (
                "final_result_ReadyForVerificationDecision",
                {
                    "status": "ready_for_verification",
                    "summary": "fixture complete",
                    "deliverables": [
                        {"relative_path": p}
                        for p in prepare_task(task_id).candidate.new_deliverables
                    ],
                },
            ),
        ]
        name, args = steps[index]
        if name == "final_result_NeedsInputDecision" and any(
            t["function"]["name"] == "ask_user" for t in body["tools"]
        ):
            name, args = "ask_user", {"question": args["clarification"]["question"]}
            if unmapped_question:
                args = {"question": "Which API method should I call?"}
        if name == "run_cae_script" and not unmapped_question and not human_confirmed:
            cae_ready.add(session)
            if len(cae_ready) == 2:
                both_at_cae.set()
            await asyncio.wait_for(both_at_cae.wait(), 5)
        return httpx.Response(
            200,
            json={
                "id": f"fixture-{index}",
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
                                    "id": f"call-{index}",
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
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "total_tokens": 110,
                },
            },
        )

    async def process(self, command, *, cwd, timeout_seconds):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        calls.append((tuple(command), cwd))
        try:
            await asyncio.sleep(0.15)
            bar = "requirements-bar3d-v1" in str(cwd)
            stem = "bar_tension" if bar else "plate_tension"
            task_id = "requirements-bar3d-v1" if bar else "missing-input-plate-v1"
            values = pair.numeric_references()[task_id]
            if "python" in command and "oracle" in Path(command[2]).name:
                odb = json.loads(await asyncio.to_thread(Path(command[3]).read_bytes))[
                    "odb_path"
                ]
                payload = {
                    "odb_path": odb,
                    "step_name": "Tension",
                    "frame_index": 1,
                    "instance_name": "INSTANCE",
                    "element_type_counts": {"C3D8R" if bar else "CPS4R": 80},
                    **values,
                }
                if bar:
                    payload.update(
                        node_count=189,
                        axis_spans=[100.0, 10.0, 10.0],
                        coordinate_layer_counts=[21, 3, 3],
                        y_face_mean_u2_delta=-0.003,
                        z_face_mean_u3_delta=-0.003,
                    )
                    if wrong_bar:
                        payload["left_sum_rf1"] = 21000.0
                await asyncio.to_thread(
                    Path(command[4]).write_text, json.dumps(payload)
                )
                return ProcessOutput(exit_code=0, stdout=b"fixture oracle", stderr=b"")
            result = await processes[stem].run(
                command, cwd=cwd, timeout_seconds=timeout_seconds
            )
            if "python" in command:
                (cwd / "results.json").write_text(json.dumps(values))
            return result
        finally:
            active -= 1

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.model.AsyncOpenAI",
        lambda **kwargs: AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)),
        ),
    )
    monkeypatch.setattr(
        pair,
        "take_gateway_api_key_from_environment",
        lambda _: SecretStr("fixture-secret"),
    )
    monkeypatch.setattr(WindowsProcessStrategy, "run", process)
    return requests, calls, lambda: maximum


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_pair_runs_models_concurrently_but_serializes_runtime_and_keeps_reviews_pending(
    tmp_path, monkeypatch
):
    requests, calls, maximum = install_resources(monkeypatch)
    path, root, spec = spec_fixture(tmp_path)
    assert spec.slots == simple_slots()
    assert not root.exists()
    summary = await pair.run_batch(path, pair.digest(path))
    assert len(requests) == 2
    assert maximum() == 1
    assert len(calls) == 8
    assert summary["post_run_host_calls"] == 2
    assert summary["counts"] == {"pass": 0, "fail": 0, "unknown": 2}
    assert all(r["numeric_check"] == "pass" for r in summary["rows"])
    original = {}
    queue_waits = []
    run_ids = set()
    for slot in simple_slots():
        state = root / "trials" / slot / "state"
        original[slot] = (state / "trial-result.json").read_bytes()
        row = json.loads(original[slot])
        assert row["interaction_acceptance"] == "pass"
        assert row["runtime_calls"] == 3
        assert row["solver_calls"] == 1
        run_ids.add(row["service_run_id"])
        questions_path = state / "coding-run/user_questions.json"
        questions = (
            json.loads(questions_path.read_bytes())["questions"]
            if questions_path.exists()
            else []
        )
        assert len(questions) == (1 if slot.startswith("missing-input") else 0)
        checks = json.loads((state / "service-checks.json").read_bytes())
        queue_waits.append(checks["runtime_queue_wait_seconds"])
        assert checks["runtime_queue_wait_seconds"] >= 0
        assert row["service_wall_seconds"] >= checks["runtime_queue_wait_seconds"]
    assert len(run_ids) == 2
    assert sum(queue_waits) > 0
    assert await pair.verify_batch(root) == summary
    assert summarize_batch(root) == summary
    assert len(calls) == 8
    assert all(
        (root / "trials" / slot / "state/trial-result.json").read_bytes() == raw
        for slot, raw in original.items()
    )


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
@pytest.mark.parametrize("variant", ["wrong_bar", "skip_clarification"])
async def test_pair_rejects_wrong_signed_reference_or_missing_clarification(
    tmp_path, monkeypatch, variant
):
    install_resources(monkeypatch, **{variant: True})
    path, _root, _ = spec_fixture(tmp_path)
    summary = await pair.run_batch(path, pair.digest(path))
    assert summary["counts"] == {"pass": 0, "fail": 1, "unknown": 1}


@pytest.mark.asyncio
async def test_spec_mismatch_never_reads_credentials_or_runs(tmp_path, monkeypatch):
    path, root, spec = spec_fixture(tmp_path)

    def forbidden(*args):
        raise AssertionError("credential access")

    monkeypatch.setattr(pair, "take_gateway_api_key_from_environment", forbidden)
    with pytest.raises(ValueError, match="approval"):
        await pair.run_batch(path, "0" * 64)
    assert not root.exists()
    for field, value in [
        ("slots", planned_slots()[:1]),
        ("max_task_concurrency", 1),
        ("max_runtime_concurrency", 2),
    ]:
        data = spec.model_dump(mode="json")
        data[field] = value
        with pytest.raises(ValidationError):
            pair.SimpleModelingSpec.model_validate_json(json.dumps(data))
    monkeypatch.setattr(pair, "source_binding", lambda: {})
    with pytest.raises(ValueError, match="drifted"):
        await pair.run_batch(path, pair.digest(path))


@pytest.mark.asyncio
async def test_waiting_runtime_cancel_does_not_dispatch_or_release_another_owner(
    tmp_path,
):
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    fake = FakeAbaqusProcess("plate_tension")
    runner = SerialRuntime(gate, fake)
    task = asyncio.create_task(runner.run(("fake",), cwd=tmp_path, timeout_seconds=10))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not fake.calls
    assert gate.locked()
    gate.release()


@pytest.mark.asyncio
async def test_runtime_gate_is_held_until_cancelled_process_cleanup_finishes(tmp_path):
    entered, cleaning, release_cleanup = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    calls = []

    class Process:
        async def run(self, command, *, cwd, timeout_seconds):
            calls.append(command)
            if len(calls) == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaning.set()
                    await release_cleanup.wait()
            return ProcessOutput(exit_code=0, stdout=b"", stderr=b"")

    gate = asyncio.Semaphore(1)
    runner = SerialRuntime(gate, Process())
    first = asyncio.create_task(
        runner.run(("first",), cwd=tmp_path, timeout_seconds=10)
    )
    await entered.wait()
    first.cancel()
    await cleaning.wait()
    second = asyncio.create_task(
        runner.run(("second",), cwd=tmp_path, timeout_seconds=10)
    )
    await asyncio.sleep(0)
    assert len(calls) == 1
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(second, 2)
    assert calls == [("first",), ("second",)]


@pytest.mark.asyncio
async def test_wall_timeout_includes_runtime_queue_wait(tmp_path):
    gate = asyncio.Semaphore(0)
    process = FakeAbaqusProcess("plate_tension")
    runner = SerialRuntime(gate, process)
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await runner.run(("fake",), cwd=tmp_path, timeout_seconds=10)
    assert runner.wait_seconds > 0
    assert not process.calls
    assert gate.locked()


def test_spec_refuses_existing_paths_and_nested_run_root(tmp_path):
    path, root, spec = spec_fixture(tmp_path)
    with pytest.raises(ValueError, match="new"):
        pair.prepare_spec(path, root, spec.launcher)
    with pytest.raises(ValueError, match="outside"):
        pair.prepare_spec(root / "spec.json", root, spec.launcher)
    assert not root.exists()


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_unmapped_question_stays_waiting_without_blocking_the_other_task(
    tmp_path, monkeypatch
):
    requests, calls, _ = install_resources(monkeypatch, unmapped_question=True)
    path, _root, _ = spec_fixture(tmp_path)
    summary = await pair.run_batch(path, pair.digest(path))
    assert len(requests) == 2
    assert summary["rows"][0]["terminal"] == "waiting_for_user"
    assert summary["rows"][0]["cause"] == "adapter_gap"
    assert summary["rows"][0]["verdict"] == "unknown"
    assert summary["rows"][1]["terminal"] == "completed"
    assert summary["post_run_host_calls"] == 1
    assert len(calls) == 4


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
@pytest.mark.parametrize("unmapped", [False, True])
async def test_human_confirmed_profile_never_uses_question_regex(
    tmp_path, monkeypatch, unmapped
):
    from abaqus_sim_agent.evaluation.task_baseline import ThicknessClarificationFixture

    def forbidden(*args):
        raise AssertionError("human profile must not classify or answer question text")

    monkeypatch.setattr(ThicknessClarificationFixture, "get_question_answer", forbidden)
    requests, calls, _ = install_resources(
        monkeypatch, human_confirmed=True, unmapped_question=unmapped
    )
    path, root, spec = spec_fixture(tmp_path, human_confirmed=True)
    assert spec.schema_version == "simple-modeling-pair-spec-v3"
    assert spec.answer_mode == "human_confirmed"
    summary = await pair.run_batch(path, pair.digest(path))
    assert len(requests) == 2
    row = summary["rows"][0]
    assert row["terminal"] == "waiting_for_user"
    assert row["cause"] != "adapter_gap"
    assert row["verdict"] == "unknown"
    assert summary["rows"][1]["terminal"] == "completed"
    assert summary["post_run_host_calls"] == 1
    assert len(calls) == 4
    ledger_path = next(root.rglob("user_questions.json"))
    question = json.loads(ledger_path.read_bytes())["questions"][0]
    assert question["answer"] is None
    assert question["abaqus_calls_before"] == 0


def test_answer_mode_is_versioned_without_reinterpreting_old_specs(tmp_path):
    _path, _root, spec = spec_fixture(tmp_path, human_confirmed=True)
    raw = spec.model_dump(mode="json")
    for version in ("simple-modeling-pair-spec-v1", "simple-modeling-pair-spec-v2"):
        old = {**raw, "schema_version": version}
        if version.endswith("v1"):
            old.pop("question_protocol")
        with pytest.raises(ValidationError, match="answer mode"):
            pair.SimpleModelingSpec.model_validate_json(json.dumps(old))
        old.pop("answer_mode")
        parsed = pair.SimpleModelingSpec.model_validate_json(json.dumps(old))
        assert "answer_mode" not in parsed.model_dump(mode="json")
    raw.pop("answer_mode")
    with pytest.raises(ValidationError, match="human_confirmed"):
        pair.SimpleModelingSpec.model_validate_json(json.dumps(raw))
