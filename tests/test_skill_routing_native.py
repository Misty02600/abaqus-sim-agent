import asyncio
import json

import pytest
from pydantic import SecretStr
from pydantic_ai import RequestUsage
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.evaluation import skill_routing_behavior as linear
from abaqus_sim_agent.evaluation import structural_topic_skill_routing as topic


@pytest.fixture(params=[linear, topic], ids=["linear", "structural"])
def suite(request, monkeypatch):
    module = request.param
    cases = module.load_cases()[:2]
    monkeypatch.setattr(module, "load_cases", lambda *args: cases)
    monkeypatch.setattr(
        module,
        "take_gateway_api_key_from_environment",
        lambda gateway: SecretStr("offline-key"),
    )
    return module, cases


@pytest.fixture
def models(suite, monkeypatch):
    module, cases = suite
    clients = []

    class Client:
        closed = False

        async def close(self):
            self.closed = True

    def create(**kwargs):
        case = cases[len(clients)]
        client = Client()
        clients.append(client)
        stage = 0

        def answer(messages, info):
            nonlocal stage
            stage += 1
            if stage == 1 and case.required_loads:
                parts = [
                    ToolCallPart("load_capability", {"id": name})
                    for name in case.required_loads
                ]
            elif stage <= 2:
                parts = [
                    ToolCallPart(
                        "write_file",
                        {
                            "path": "analysis.json",
                            "content": '{"summary":"offline fixture","findings":[],"next_action":"review"}',
                        },
                    )
                ]
            else:
                name = next(
                    t.name
                    for t in info.output_tools
                    if t.name.endswith("ReadyForVerificationDecision")
                )
                parts = [
                    ToolCallPart(
                        name,
                        {
                            "status": "ready_for_verification",
                            "deliverables": [{"relative_path": "analysis.json"}],
                            "summary": "fixture ready",
                        },
                    )
                ]
            return ModelResponse(
                parts=parts, usage=RequestUsage(input_tokens=1, output_tokens=1)
            )

        return FunctionModel(answer), client

    monkeypatch.setattr(module, "create_deepseek_chat_model", create)
    return clients


@pytest.mark.asyncio
async def test_dataset_runs_real_service_and_preserves_existing_routing_view(
    tmp_path, suite, models
):
    module, cases = suite
    root = tmp_path / "batch"
    report = await module.run_evaluation(root)
    native = json.loads((root / "pydantic-evals-report.json").read_bytes())
    assert [r["name"] for r in native["cases"]] == [c.case_id for c in cases]
    assert report["case_count"] == len(cases)
    assert report["exact_route_passes"] == len(cases)
    assert report["review_status"] == "pending_manual_review"
    assert not native["failures"]
    summary = {a["title"]: a["value"] for a in native["analyses"]}
    assert summary["exact_route_passes"] == report["exact_route_passes"]
    assert all(c.closed for c in models)
    assert len(models) == len(cases)
    for case, observed, row in zip(
        cases, report["cases"], native["cases"], strict=True
    ):
        assert observed["outcome"]["status"] == "completed"
        assert observed["actual_abaqus_calls"] == 0
        assert observed["actual_solver_calls"] == 0
        assert row["output"] == observed
        assert row["assertions"]["exact_route_pass"]["value"] is True
        assert "task_pass" not in row["assertions"]
        restored = module.observe_case(root, case)
        assert all(observed[key] == value for key, value in restored.items())
    manifest = json.loads((root / "manifest.json").read_bytes())
    assert any(
        p.endswith("skill_routing_evals.py") for p in manifest["implementation_sha256"]
    )
    assert native["experiment_metadata"]["model"] == manifest["model"]
    assert native["experiment_metadata"]["budget"] == manifest["budget"]
    assert native["experiment_metadata"]["manifest_sha256"] == report["manifest_sha256"]


@pytest.mark.asyncio
async def test_preparation_failure_stops_later_case_and_closes_client(
    tmp_path, suite, models, monkeypatch
):
    module, cases = suite

    def broken(*args, **kwargs):
        raise ValueError("fixture assembly failure")

    monkeypatch.setattr(module, "create_abaqus_coding_agent", broken)
    root = tmp_path / "batch"
    with pytest.raises(ValueError, match="fixture assembly failure"):
        await module.run_evaluation(root)
    assert len(models) == 1
    assert models[0].closed
    assert not (root / cases[1].case_id).exists()
    assert not (root / "completion.json").exists()


@pytest.mark.asyncio
async def test_service_exception_still_records_observation_and_continues(
    tmp_path, suite, models, monkeypatch
):
    module, cases = suite

    class Interrupted:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            raise ConnectionError("fixture provider interruption")

    monkeypatch.setattr(module, "CodingRunService", Interrupted)
    report = await module.run_evaluation(tmp_path / "batch")
    assert len(models) == len(cases)
    assert all(c.closed for c in models)
    assert all(
        r["infrastructure_error_type"] == "ConnectionError" for r in report["cases"]
    )
    # This suite reports routing separately from the interrupted host outcome.
    native = json.loads((tmp_path / "batch/pydantic-evals-report.json").read_bytes())
    assert all(
        r["labels"]["infrastructure_error"]["value"] == "ConnectionError"
        for r in native["cases"]
    )


@pytest.mark.asyncio
async def test_cancel_drains_current_task_and_does_not_start_next_case(
    tmp_path, suite, models, monkeypatch
):
    module, cases = suite
    entered = asyncio.Event()
    finalized = []

    class Waiting:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalized.append(True)

    monkeypatch.setattr(module, "CodingRunService", Waiting)
    root = tmp_path / "batch"
    task = asyncio.create_task(module.run_evaluation(root))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finalized == [True]
    assert len(models) == 1
    assert models[0].closed
    assert not (root / cases[1].case_id).exists()
    assert not (root / "completion.json").exists()


@pytest.mark.asyncio
async def test_grading_error_retains_native_failure_without_rerunning_tasks(
    tmp_path, suite, models, monkeypatch
):
    from abaqus_sim_agent.evaluation.skill_routing_evals import SkillRouteEvaluator

    module, cases = suite

    def fail_grade(self, ctx):
        raise ValueError("fixture grader failure")

    monkeypatch.setattr(SkillRouteEvaluator, "evaluate", fail_grade)
    root = tmp_path / "batch"
    with pytest.raises(RuntimeError, match="per-case evidence was retained"):
        await module.run_evaluation(root)
    assert len(models) == len(cases)
    assert all(client.closed for client in models)
    native = json.loads((root / "pydantic-evals-report.json").read_bytes())
    assert len(native["cases"]) == len(cases)
    assert all(row["evaluator_failures"] for row in native["cases"])
    assert native["experiment_metadata"]["environment_verified_after_run"] is False
    assert not (root / "completion.json").exists()


@pytest.mark.asyncio
async def test_internal_case_cancel_stops_batch_after_cleanup(
    tmp_path, suite, models, monkeypatch
):
    module, cases = suite
    finalized = []

    class InternallyCancelled:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            try:
                raise asyncio.CancelledError("fixture internal cancellation")
            finally:
                await asyncio.sleep(0)
                finalized.append(True)

    monkeypatch.setattr(module, "CodingRunService", InternallyCancelled)
    root = tmp_path / "batch"
    job = asyncio.create_task(module.run_evaluation(root))
    with pytest.raises(asyncio.CancelledError):
        await job
    assert job.cancelled()
    assert finalized == [True]
    assert len(models) == 1
    assert models[0].closed
    assert not (root / cases[1].case_id).exists()
    assert not (root / "completion.json").exists()
    assert not (root / "report.json").exists()
