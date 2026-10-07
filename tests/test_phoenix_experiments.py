import asyncio
import json
from collections import Counter

import phoenix.client
import pytest
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator
from pydantic_evals.lifecycle import CaseLifecycle

from abaqus_sim_agent import telemetry
from abaqus_sim_agent.evaluation.phoenix_experiments import (
    PhoenixRecordingError,
    phoenix_experiment,
)
from tests.support.phoenix import PhoenixStub


@pytest.fixture
def api(monkeypatch):
    stub = PhoenixStub()
    monkeypatch.setattr(telemetry, "_endpoint", "http://127.0.0.1:6006")
    monkeypatch.setattr(phoenix.client, "AsyncClient", lambda **kw: stub)
    return stub


def recording(root, names=("pass",), *, invocation="one", goal="public task"):
    return phoenix_experiment(
        root=root,
        family="fixture",
        examples={name: {"input": {"goal": goal}} for name in names},
        name=f"fixture-{invocation}",
        invocation_id=invocation,
        metadata={"scope": "offline fixture"},
    )


@pytest.mark.asyncio
async def test_disabled_does_not_create_client_or_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "_endpoint", None)
    async with recording(tmp_path) as publication:
        assert publication is None
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_unavailable_service_fails_before_task(tmp_path, api):
    api.unavailable = True
    with pytest.raises(PhoenixRecordingError, match="no task was started"):
        async with recording(tmp_path):
            pytest.fail("entered execution before Phoenix was available")
    assert not api.created_experiments


@pytest.mark.asyncio
async def test_reuses_immutable_dataset_and_creates_distinct_experiments(tmp_path, api):
    for invocation in ("one", "two"):
        async with recording(tmp_path, invocation=invocation):
            pass
    assert len(api.saved_datasets) == 1
    assert len(api.created_experiments) == 2
    assert api.created_experiments[0]["dataset_version_id"] == "version-0"
    async with recording(tmp_path, invocation="changed", goal="changed task"):
        pass
    assert len(api.saved_datasets) == 2
    dataset = next(iter(api.saved_datasets.values()))
    dataset.examples[0]["input"]["goal"] = "externally modified"
    with pytest.raises(PhoenixRecordingError, match="ValueError"):
        async with recording(tmp_path, invocation="tampered"):
            pytest.fail("reused changed dataset")
    assert len(api.created_experiments) == 3


@pytest.mark.asyncio
async def test_dataset_reuse_rejects_boolean_replacing_number(tmp_path, api):
    async with recording(tmp_path, goal=1):
        pass
    dataset = next(iter(api.saved_datasets.values()))
    dataset.examples[0]["input"]["goal"] = True
    with pytest.raises(PhoenixRecordingError, match="ValueError"):
        async with recording(tmp_path, invocation="changed-type", goal=1):
            pytest.fail("bool and number are not the same frozen input")


@pytest.mark.asyncio
async def test_copies_results_and_distinguishes_task_and_grading_errors(tmp_path, api):
    names = ("pass", "reject", "task_error", "judge_error")
    calls = Counter()
    saved = []

    async def task(name):
        calls[name] += 1
        if name == "task_error":
            raise RuntimeError("synthetic provider failure")
        return name

    class Judge(Evaluator):
        def evaluate(self, ctx):
            if ctx.output == "judge_error":
                raise ValueError("synthetic grading failure")
            return {
                "first_submission_pass": ctx.output == "pass",
                "final_submission_pass": ctx.output == "pass",
                "task_status": "passed" if ctx.output == "pass" else "failed",
            }

    class Save(CaseLifecycle):
        async def teardown(self, result):
            saved.append(self.case.name)

    def link(result):
        assert result.name in saved
        result.trace_id = "a" * 32

    async with recording(tmp_path, names) as publication:
        report = await Dataset(
            name="fixture",
            cases=[Case(name=name, inputs=name) for name in names],
            evaluators=[Judge()],
        ).evaluate(
            task,
            max_concurrency=2,
            progress=False,
            lifecycle=publication.lifecycle(Save, link_trace=link),
        )
        assert len(report.failures) == 1
    assert calls == dict.fromkeys(names, 1)
    assert len(api.runs) == 4
    assert all(run["trace_id"] == "a" * 32 for run in api.runs)
    assert sum(run["error"] is not None for run in api.runs) == 1
    assert any(
        "synthetic grading failure" in (row.get("error") or "")
        for row in api.evaluations
    )
    assert {
        row["score"]
        for row in api.evaluations
        if row["name"] == "assertion.first_submission_pass"
    } == {0.0, 1.0}
    receipt = json.loads((tmp_path / "telemetry/experiments/one.json").read_text())
    assert receipt["status"] == "complete"
    assert len(receipt["cases"]) == 4


@pytest.mark.asyncio
async def test_recording_failure_keeps_local_execution_and_reports_at_end(
    tmp_path, api
):
    api.fail_recording = True
    calls = []

    async def task(name):
        calls.append(name)
        (tmp_path / f"{name}.json").write_text('{"local": true}')
        return name

    async def run():
        async with recording(tmp_path, ("one", "two")) as publication:
            report = await Dataset(
                name="fixture",
                cases=[Case(name=n, inputs=n) for n in ("one", "two")],
            ).evaluate(
                task,
                max_concurrency=1,
                progress=False,
                lifecycle=publication.lifecycle(CaseLifecycle),
            )
            assert len(report.cases) == 2

    with pytest.raises(PhoenixRecordingError, match="local results are retained"):
        await run()
    assert calls == ["one", "two"]
    assert api.run_attempts == 1
    assert (tmp_path / "two.json").exists()
    receipt = json.loads((tmp_path / "telemetry/experiments/one.json").read_text())
    assert receipt["status"] == "incomplete"


@pytest.mark.asyncio
async def test_cancel_preserves_cleanup_and_original_exception(tmp_path, api):
    began = asyncio.Event()
    cleaned = []

    async def task(value):
        began.set()
        await asyncio.Event().wait()

    class Cleanup(CaseLifecycle):
        async def teardown(self, result):
            cleaned.append(result is None)

    async def run():
        async with recording(tmp_path) as publication:
            await Dataset(
                name="fixture", cases=[Case(name="pass", inputs="pass")]
            ).evaluate(task, progress=False, lifecycle=publication.lifecycle(Cleanup))

    job = asyncio.create_task(run())
    await began.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert cleaned == [True]
    receipt = json.loads((tmp_path / "telemetry/experiments/one.json").read_text())
    assert receipt["status"] == "interrupted"


@pytest.mark.asyncio
async def test_selected_scope_excludes_old_result_projections_and_scrubs_secrets(
    tmp_path, api, monkeypatch
):
    monkeypatch.setattr(telemetry, "_credential_values", ("fixture-secret",))
    async with recording(
        tmp_path, ("new", "old"), goal="fixture-secret"
    ) as publication:
        await Dataset(
            name="fixture", cases=[Case(name=n, inputs=n) for n in ("new", "old")]
        ).evaluate(
            lambda value: "fixture-secret",
            progress=False,
            lifecycle=publication.lifecycle(CaseLifecycle, selected={"new"}),
        )
    assert len(api.runs) == 1
    assert api.runs[0]["dataset_example_id"] == "node-0"
    assert "fixture-secret" not in str(api.runs)
    assert "fixture-secret" not in str(api.saved_datasets)
