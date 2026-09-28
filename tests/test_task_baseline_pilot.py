import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from abaqus_sim_agent.evaluation import task_baseline_pilot as pilot
from abaqus_sim_agent.evaluation.task_baseline import (
    REVIEW_ITEMS,
    HumanReview,
    ReviewCheck,
    baseline_execution_config,
    planned_slots,
    prepare_task,
)
from abaqus_sim_agent.evaluation.task_baseline_runner import (
    record_review,
    summarize_batch,
)
from abaqus_sim_agent.evaluation.task_baseline_service import run_service_live
from abaqus_sim_agent.execution import ProcessOutput
from abaqus_sim_agent.execution.abaqus2024 import WindowsProcessStrategy
from tests.support.abaqus import FakeAbaqusProcess
from tests.test_task_baseline_service import context_for


def spec_fixture(tmp_path):
    launcher = tmp_path / "fixture-abq2024.bat"
    launcher.write_text("fixture only")
    spec_path = tmp_path / "spec.json"
    root = tmp_path / "pilot"
    spec = pilot.prepare_pilot_spec(spec_path, root, launcher)
    return spec_path, root, spec


def install_fake_live_resources(
    monkeypatch,
    *,
    first_503=False,
    wrong_rf=False,
    invalid_results=False,
    oracle_timeout=False,
    empty_arguments=False,
    first_read_timeout=False,
    first_read_cancel=False,
    always_timeout=False,
):
    calls, bodies = [], []
    index = 0
    declarations = prepare_task("requirements-plate-v1").candidate.new_deliverables
    steps = [
        ("read_file", {"path": "requirements.md"}),
        ("write_file", {"path": "build_model.py", "content": "# fixture\nvalue = 1\n"}),
        (
            "write_file",
            {"path": "extract_results.py", "content": "# fixture\nvalue = 2\n"},
        ),
        ("run_cae_script", {"script_path": "build_model.py"}),
        (
            "run_solver_job",
            {"job_name": "plate_tension", "input_file": "plate_tension.inp"},
        ),
        ("run_abaqus_python", {"script_path": "extract_results.py"}),
        ("read_file", {"path": "results.json"}),
        (
            "final_result_ReadyForVerificationDecision",
            {
                "status": "ready_for_verification",
                "summary": "Mock pilot ready",
                "deliverables": [{"relative_path": p} for p in declarations],
            },
        ),
    ]

    def reply(request):
        nonlocal index
        body = json.loads(request.content)
        bodies.append(body)
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "high"
        assert request.headers.get("x-opencode-session")
        assert "max_tokens" not in body
        if always_timeout:
            raise httpx.ReadTimeout("offline timeout", request=request)
        if (first_read_timeout or first_read_cancel) and len(bodies) == 1:

            class BrokenStream(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield b'{"choices":'
                    if first_read_cancel:
                        raise asyncio.CancelledError
                    raise httpx.ReadTimeout("private-error-sentinel", request=request)

            return httpx.Response(200, stream=BrokenStream())
        if first_503 and len(bodies) == 1:
            return httpx.Response(
                503,
                headers={"retry-after-ms": "1"},
                json={"error": {"message": "fixture retry"}},
            )
        name, args = steps[index]
        if empty_arguments:
            name, args = "write_file", {}
        index += 1
        return httpx.Response(
            200,
            json={
                "id": "fixture",
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

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.model.AsyncOpenAI",
        lambda **kwargs: AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)),
        ),
    )
    monkeypatch.setattr(
        pilot,
        "take_gateway_api_key_from_environment",
        lambda _: SecretStr("fixture-secret"),
    )
    process = FakeAbaqusProcess("plate_tension")

    async def fake_process(self, command, *, cwd, timeout_seconds):
        calls.append((tuple(command), cwd, timeout_seconds))
        if (
            "python" in command
            and Path(command[2]).name == "extract_plate_tension_oracle.py"
        ):
            if oracle_timeout:
                return ProcessOutput(
                    exit_code=None,
                    stdout=b"",
                    stderr=b"fixture timeout",
                    timed_out=True,
                )
            odb = json.loads(await asyncio.to_thread(Path(command[3]).read_bytes))[
                "odb_path"
            ]
            measured = {
                "odb_path": odb,
                "step_name": "Tension",
                "frame_index": 1,
                "instance_name": "PLATE-1",
                "element_type_counts": {"CPS4R": 80},
                **pilot.REFERENCES,
            }
            if wrong_rf:
                measured["left_sum_rf1"] = 4200.0
            await asyncio.to_thread(Path(command[4]).write_text, json.dumps(measured))
            return ProcessOutput(exit_code=0, stdout=b"fixture oracle", stderr=b"")
        result = await process.run(command, cwd=cwd, timeout_seconds=timeout_seconds)
        if "python" in command:
            data = dict(pilot.REFERENCES)
            if invalid_results:
                data["right_mean_u1"] = True
            (cwd / "results.json").write_text(json.dumps(data))
        return result

    monkeypatch.setattr(WindowsProcessStrategy, "run", fake_process)
    return calls, bodies


def test_prepare_is_read_only_for_runtime_and_requires_exact_approval(
    tmp_path, monkeypatch
):
    spec_path, root, spec = spec_fixture(tmp_path)
    assert not root.exists()
    assert spec.configuration.reasoning_policy == "enabled_high"
    assert spec.budget.max_model_requests == 30
    assert spec.reference_values["left_sum_rf1"] == -4200.0
    with pytest.raises(ValueError, match="explicit approval"):
        pilot.validate_pilot_spec(spec_path, "0" * 64)
    assert pilot.validate_pilot_spec(spec_path, pilot.digest(spec_path)) == spec
    monkeypatch.setattr(pilot, "source_binding", lambda: {})
    with pytest.raises(ValueError, match="drifted"):
        pilot.validate_pilot_spec(spec_path, pilot.digest(spec_path))


@pytest.mark.asyncio
async def test_unapproved_run_never_reads_key_or_dispatches(tmp_path, monkeypatch):
    spec_path, root, _ = spec_fixture(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("must fail before credential or runtime access")

    monkeypatch.setattr(pilot, "take_gateway_api_key_from_environment", forbidden)
    with pytest.raises(ValueError, match="explicit approval"):
        await pilot.run_pilot(spec_path, approved_sha256="0" * 64)
    assert not root.exists()


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_live_entry_uses_real_adapter_with_mock_http_and_keeps_post_review_separate(
    tmp_path, monkeypatch
):
    calls, bodies = install_fake_live_resources(monkeypatch, first_503=True)
    spec_path, root, _ = spec_fixture(tmp_path)
    summary = await pilot.run_pilot(spec_path, approved_sha256=pilot.digest(spec_path))
    assert summary["purpose"] == "live_development_pilot"
    assert summary["planned"] == 1
    assert summary["counts"] == {"pass": 0, "fail": 0, "unknown": 1}
    assert summary["rows"][0]["numeric_check"] == "pass"
    assert summary["post_run_host_calls"] == 1
    assert len(bodies) == 9  # 8 logical requests, one SDK 503 retry.
    assert len(calls) == 4  # Three simulated stages plus one simulated host read.
    slot = planned_slots()[0]
    state = root / "trials" / slot / "state"
    original_files = [p for p in (state / "coding-run").rglob("*") if p.is_file()]
    before = {p: p.read_bytes() for p in original_files}
    observation_bytes = (state / "trial-result.json").read_bytes()
    observation = json.loads(observation_bytes)
    assert observation["numeric_acceptance"] == "unknown"
    assert observation["http_attempts"] == 9
    assert observation["logical_requests"] == 8
    assert observation["model_requests_started"] == 8
    assert "fixture-secret" not in (state / "http-attempts.json").read_text()
    reference = f"trials/{slot}/state/post-verification/report.json"
    review = HumanReview(
        source="human",
        reviewer="unit-test reviewer fixture",
        reviewed_at=datetime.now(UTC),
        trial_result_sha256=pilot.digest(state / "trial-result.json"),
        checks={
            name: ReviewCheck(
                state="pass",
                reason="Synthetic unit-test judgment only",
                evidence=(reference,),
            )
            for name in REVIEW_ITEMS
        },
    )
    summary = record_review(root, slot, review)
    assert summary["counts"]["pass"] == 1
    await pilot.verify_pilot(root)
    assert summarize_batch(root)["counts"]["pass"] == 1
    assert len(calls) == 4
    assert len(bodies) == 9
    assert (state / "trial-result.json").read_bytes() == observation_bytes
    assert {p: p.read_bytes() for p in original_files} == before


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["wrong_rf", "invalid_results", "oracle_timeout"])
async def test_postcheck_distinguishes_numeric_failure_from_extraction_unknown(
    tmp_path, monkeypatch, bad
):
    calls, _ = install_fake_live_resources(monkeypatch, **{bad: True})
    spec_path, root, _ = spec_fixture(tmp_path)
    summary = await pilot.run_pilot(spec_path, approved_sha256=pilot.digest(spec_path))
    assert summary["rows"][0]["numeric_check"] == (
        "unknown" if bad == "oracle_timeout" else "fail"
    )
    outcome = json.loads(
        (
            root / "trials" / planned_slots()[0] / "state/coding-run/outcome.json"
        ).read_bytes()
    )
    assert outcome["status"] == "completed"
    assert outcome["verification_scope"] == "execution_evidence"
    await pilot.verify_pilot(root)
    assert len(calls) == 4


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_live_review_rejects_fixture_and_stale_binding(tmp_path, monkeypatch):
    install_fake_live_resources(monkeypatch)
    spec_path, root, _ = spec_fixture(tmp_path)
    await pilot.run_pilot(spec_path, approved_sha256=pilot.digest(spec_path))
    slot = planned_slots()[0]
    ref = f"trials/{slot}/state/post-verification/report.json"
    review = HumanReview(
        source="fixture",
        reviewer="fixture",
        reviewed_at=datetime.now(UTC),
        checks={
            name: ReviewCheck(state="pass", reason="fixture", evidence=(ref,))
            for name in REVIEW_ITEMS
        },
    )
    with pytest.raises(ValueError, match="review requires"):
        record_review(root, slot, review)
    with pytest.raises(ValueError, match="not bound"):
        record_review(root, slot, review.model_copy(update={"source": "human"}))


def test_pilot_rejects_unapproved_budget_increase(tmp_path):
    path, _, _ = spec_fixture(tmp_path)
    data = json.loads(path.read_bytes())
    data["budget"]["max_model_requests"] = 31
    with pytest.raises(ValueError, match="upper bound"):
        pilot.T1PilotSpec.model_validate_json(json.dumps(data))


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_oracle_reservation_without_receipt_never_buys_second_attempt(
    tmp_path, monkeypatch
):
    calls, _ = install_fake_live_resources(monkeypatch)
    spec_path, root, _ = spec_fixture(tmp_path)
    real_verify = pilot.verify_pilot

    async def defer_verify(root):
        return summarize_batch(root)

    monkeypatch.setattr(pilot, "verify_pilot", defer_verify)
    await pilot.run_pilot(spec_path, approved_sha256=pilot.digest(spec_path))
    monkeypatch.setattr(pilot, "verify_pilot", real_verify)
    post = root / "trials" / planned_slots()[0] / "state/post-verification"
    post.mkdir()
    (post / "attempt.json").write_text('{"status":"reserved"}')
    summary = await pilot.verify_pilot(root)
    assert summary["rows"][0]["numeric_check"] == "unknown"
    assert summary["post_run_host_calls"] == 1
    assert len(calls) == 3
    await pilot.verify_pilot(root)
    assert len(calls) == 3


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_human_pass_cannot_override_numeric_fail(tmp_path, monkeypatch):
    install_fake_live_resources(monkeypatch, wrong_rf=True)
    spec_path, root, _ = spec_fixture(tmp_path)
    await pilot.run_pilot(spec_path, approved_sha256=pilot.digest(spec_path))
    slot = planned_slots()[0]
    ref = f"trials/{slot}/state/post-verification/report.json"
    review = HumanReview(
        source="human",
        reviewer="unit-test fixture",
        reviewed_at=datetime.now(UTC),
        trial_result_sha256=pilot.digest(
            root / "trials" / slot / "state/trial-result.json"
        ),
        checks={
            name: ReviewCheck(
                state="pass", reason="Synthetic judgment", evidence=(ref,)
            )
            for name in REVIEW_ITEMS
        },
    )
    assert record_review(root, slot, review)["counts"]["fail"] == 1


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_changed_post_report_is_not_reused_as_valid_score(tmp_path, monkeypatch):
    calls, _ = install_fake_live_resources(monkeypatch)
    spec_path, root, _ = spec_fixture(tmp_path)
    await pilot.run_pilot(spec_path, approved_sha256=pilot.digest(spec_path))
    report = (
        root / "trials" / planned_slots()[0] / "state/post-verification/report.json"
    )
    report.write_text("{}")
    summary = summarize_batch(root)
    assert summary["rows"][0]["integrity_error"] == "post-run report changed"
    assert len(calls) == 4


def test_spec_cannot_create_the_reserved_run_directory(tmp_path):
    launcher = tmp_path / "fixture.bat"
    launcher.write_text("fixture")
    root = tmp_path / "new-run"
    with pytest.raises(ValueError, match="outside"):
        pilot.prepare_pilot_spec(root / "spec.json", root, launcher)
    assert not root.exists()


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_empty_tool_arguments_are_diagnosed_without_changing_product_terminal(
    tmp_path, monkeypatch
):
    calls, bodies = install_fake_live_resources(monkeypatch, empty_arguments=True)
    spec_path, root, _ = spec_fixture(tmp_path)
    summary = await pilot.run_pilot(spec_path, approved_sha256=pilot.digest(spec_path))
    row = summary["rows"][0]
    assert row["cause"] == "tool_arguments"
    assert row["terminal"] == "interrupted"
    assert row["verdict"] == "fail"
    assert len(bodies) == 3
    assert not calls
    assert summary["post_run_host_calls"] == 0
    state = root / "trials" / planned_slots()[0] / "state"
    checks = json.loads((state / "service-checks.json").read_bytes())
    assert checks["failure_diagnostic"] == {
        "kind": "tool_argument_validation_exhausted",
        "tool_name": "write_file",
        "retry_limit": 2,
        "upstream_responsibility": "unknown",
    }
    attempts = json.loads((state / "http-attempts.json").read_bytes())
    assert len(attempts) == 3
    for attempt in attempts:
        assert attempt["status_code"] == 200
        diag = attempt["response_diagnostic"]
        assert diag["finish_reason"] == "tool_calls"
        assert diag["tools"][0]["arguments_state"] == "empty_object"
        assert diag["tools"][0]["arguments_bytes"] == 2
    history = json.loads((state / "coding-run/history.json").read_bytes())
    assert (
        len(
            [p for m in history for p in m["parts"] if p["part_kind"] == "retry-prompt"]
        )
        == 2
    )
    original = (state / "trial-result.json").read_bytes()
    assert summarize_batch(root)["rows"][0]["cause"] == "tool_arguments"
    assert (state / "trial-result.json").read_bytes() == original


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_response_read_failure_still_retries_and_records_no_error_text(
    tmp_path, monkeypatch
):
    calls, bodies = install_fake_live_resources(monkeypatch, first_read_timeout=True)
    spec_path, root, _ = spec_fixture(tmp_path)
    summary = await pilot.run_pilot(spec_path, approved_sha256=pilot.digest(spec_path))
    assert summary["rows"][0]["terminal"] == "completed"
    assert len(bodies) == 9
    assert len(calls) == 4
    state = root / "trials" / planned_slots()[0] / "state"
    raw = (state / "http-attempts.json").read_bytes()
    attempts = json.loads(raw)
    assert attempts[0]["response_diagnostic"] == {
        "schema_version": "response-diagnostic-v1",
        "state": "read_error",
        "error_type": "ReadTimeout",
    }
    assert attempts[1]["response_diagnostic"]["state"] == "parsed"
    assert b"private-error-sentinel" not in raw
    assert b"fixture-secret" not in raw


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_response_diagnostic_does_not_swallow_cancellation(tmp_path, monkeypatch):
    calls, bodies = install_fake_live_resources(monkeypatch, first_read_cancel=True)
    context = context_for(tmp_path, "requirements-plate-v1")
    launcher = tmp_path / "fixture.bat"
    launcher.write_text("fixture only")
    with pytest.raises(asyncio.CancelledError):
        await run_service_live(
            context,
            prepare_task("requirements-plate-v1"),
            launcher=launcher,
            api_key=SecretStr("fixture-secret"),
            config=baseline_execution_config(),
        )
    assert len(bodies) == 1
    assert not calls
    state = context.state_dir
    attempts = json.loads((state / "http-attempts.json").read_bytes())
    assert attempts[0]["response_diagnostic"]["error_type"] == "CancelledError"
    resume = json.loads((state / "coding-run/resume_state.json").read_bytes())
    assert resume["status"] == "interrupted"


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_child_http_cancellation_preserves_pilot_receipt_without_framework_error(
    tmp_path, monkeypatch
):
    calls, bodies = install_fake_live_resources(monkeypatch, first_read_cancel=True)
    spec_path, root, _ = spec_fixture(tmp_path)
    job = asyncio.create_task(
        pilot.run_pilot(spec_path, approved_sha256=pilot.digest(spec_path))
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(job, 10)
    assert len(bodies) == 1
    assert not calls
    state = root / "trials" / planned_slots()[0] / "state"
    receipt = json.loads((state / "interrupted-observation.json").read_bytes())
    original = (state / "trial-result.json").read_bytes()
    row = json.loads(original)
    assert row["logical_requests"] == receipt["logical_requests"]
    assert row["http_attempts"] == receipt["http_attempts"] == 1
    assert row["service_run_id"] == receipt["service_run_id"]
    assert row["evidence_references"] == receipt["evidence_references"]
    summary = summarize_batch(root)
    assert summary["status"] == "interrupted"
    assert summary["started"] == 1
    assert summary["rows"][0]["terminal"] == "interrupted"
    assert summary["rows"][0]["integrity_error"] is None
    assert (state / "trial-result.json").read_bytes() == original
    assert not (state / "post-verification").exists()


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_failed_logical_request_is_counted_once_despite_eleven_http_attempts(
    tmp_path, monkeypatch
):
    calls, bodies = install_fake_live_resources(monkeypatch, always_timeout=True)
    monkeypatch.setattr(AsyncOpenAI, "_calculate_retry_timeout", lambda *args: 0.0)
    spec_path, root, _ = spec_fixture(tmp_path)
    summary = await pilot.run_pilot(spec_path, approved_sha256=pilot.digest(spec_path))
    assert summary["rows"][0]["cause"] == "provider"
    assert summary["costs"]["model_requests_started"]["known_sum"] == 1
    assert summary["costs"]["logical_requests"]["known_sum"] == 0
    assert summary["costs"]["http_attempts"]["known_sum"] == 11
    assert len(bodies) == 11
    assert not calls
    state = root / "trials" / planned_slots()[0] / "state"
    attempts = json.loads((state / "http-attempts.json").read_bytes())
    assert [a["sdk_retry_count"] for a in attempts] == list(range(11))
