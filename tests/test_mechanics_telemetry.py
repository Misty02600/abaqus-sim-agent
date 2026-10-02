import asyncio
import hashlib
import json
from types import SimpleNamespace

import phoenix.client
import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from abaqus_sim_agent import telemetry
from abaqus_sim_agent.coding.contracts import CodingRunOutcome, CodingRunStatus
from abaqus_sim_agent.evaluation import mechanics_telemetry as bridge
from abaqus_sim_agent.evaluation import public_mechanics_suite as suite
from abaqus_sim_agent.evaluation.public_mechanics_cases import (
    BASIC_LINEAR_CASE_IDS,
    PUBLIC_MECHANICS_PROTOCOL,
    get_case,
)
from abaqus_sim_agent.evaluation.public_mechanics_control import (
    build_report_async,
    read_json,
    save_json,
)
from abaqus_sim_agent.verification.public_mechanics_run import PublicMechanicsAttempt


@pytest.fixture
def traces(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "_provider", provider)
    monkeypatch.setattr(telemetry, "_endpoint", "http://127.0.0.1:6006")
    yield exporter
    provider.shutdown()


@pytest.fixture
def mechanics_runtime(tmp_path, monkeypatch):
    names = list(BASIC_LINEAR_CASE_IDS[:2])
    save_json(
        tmp_path / "manifest.json",
        {
            "version": PUBLIC_MECHANICS_PROTOCOL,
            "batch_id": "offline-telemetry",
            "case_ids": names,
            "scoring_case_ids": list(BASIC_LINEAR_CASE_IDS),
            "verification_policy": "results_first_v1",
            "max_explicit_resumes": 1,
            "catalog_mode": "full",
            "foundation": True,
        },
    )
    save_json(
        tmp_path / "cases.json", [get_case(n).model_dump(mode="json") for n in names]
    )
    save_json(
        tmp_path / "seal.json",
        {
            "files": {
                p: hashlib.sha256((tmp_path / p).read_bytes()).hexdigest()
                for p in ("manifest.json", "cases.json")
            }
        },
    )
    config = SimpleNamespace(
        model_provider="fixture",
        model_name="fixture",
        http_timeout_seconds=1,
        http_retries=0,
    )
    monkeypatch.setattr(suite, "_config", lambda root: config)
    monkeypatch.setattr(
        suite,
        "_request",
        lambda root, name, catalog: SimpleNamespace(
            run_id="fixture-" + name, state_dir=root / name / "state", case_id=name
        ),
    )
    monkeypatch.setattr(suite, "create_deepseek_chat_model", lambda **kw: (None, None))
    monkeypatch.setattr(suite, "create_abaqus_coding_agent", lambda *a, **kw: None)
    monkeypatch.setattr(suite, "CodingAgentDependencies", lambda **kw: None)
    monkeypatch.setattr(suite, "load_initial_snapshot", lambda state: {})
    behavior = {"interrupt": False, "cancel": False}

    class Service:
        def __init__(self, **kwargs):
            self.request = kwargs["request"]

        async def run(self):
            req = self.request
            with telemetry.span("fixture.model_and_tool"):
                await asyncio.sleep(0)
                save_json(
                    req.state_dir / "resume_state.json",
                    {
                        "status": "interrupted"
                        if behavior["interrupt"]
                        else "completed",
                        "usage": {},
                        "dependencies": {},
                    },
                )
                if behavior["cancel"]:
                    raise asyncio.CancelledError()
                if behavior["interrupt"]:
                    raise RuntimeError("fixture interruption")
                case = get_case(req.case_id)
                verification = PublicMechanicsAttempt(
                    passed=True,
                    feedback="offline fixture",
                    artifacts=(),
                    measured={k: m.target for k, m in case.metrics.items()},
                    verification_policy="results_first_v1",
                )
                save_json(
                    req.state_dir / "outcome.json",
                    CodingRunOutcome(
                        run_id=req.run_id,
                        status=CodingRunStatus.COMPLETED,
                        summary="offline fixture",
                        artifacts=(),
                        verification_scope="task_oracle",
                    ).model_dump(mode="json"),
                )
                (req.state_dir / "events.jsonl").write_text(
                    json.dumps(
                        {
                            "event": "verification",
                            "task_revision": 0,
                            "amendment_sequence": 0,
                            "data": verification.model_dump(mode="json"),
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

    monkeypatch.setattr(suite, "CodingRunService", Service)
    rows = {}

    def publish(**kwargs):
        for row in kwargs["span_annotations"]:
            rows[(row["span_id"], row["name"], row["identifier"])] = row

    monkeypatch.setattr(
        phoenix.client,
        "Client",
        lambda **kw: SimpleNamespace(
            spans=SimpleNamespace(log_span_annotations=publish)
        ),
    )
    return names, behavior, rows


async def run_case(root, name, *, resume=False):
    await suite._run_case(root, name, resume=resume, key=None, catalog=None)


@pytest.mark.asyncio
async def test_actual_concurrent_attempts_and_report_updates(
    tmp_path, traces, mechanics_runtime
):
    names, _, rows = mechanics_runtime
    await asyncio.gather(*(run_case(tmp_path, n) for n in names))
    spans = traces.get_finished_spans()
    parents = [s for s in spans if s.name == "evaluation.case"]
    assert len(parents) == 2
    assert len({s.context.trace_id for s in parents}) == 2
    for parent in parents:
        assert parent.attributes["duration_scope"] == "actual_case_attempt"
        assert (
            parent.attributes["session.id"] == "fixture-" + parent.attributes["case_id"]
        )
        assert any(
            s.parent and s.parent.span_id == parent.context.span_id for s in spans
        )
    report = await build_report_async(tmp_path, bindings_unchanged=True)
    assert report["scoring"]["task_summary"]["tasks_pending_review"] == 2
    count = len(rows)
    await build_report_async(tmp_path, bindings_unchanged=True)
    assert len(rows) == count
    assert len(traces.get_finished_spans()) == len(spans)
    native = read_json(tmp_path / "pydantic-evals-report.json")
    for case in native["cases"]:
        assert case["span_id"] in {f"{s.context.span_id:016x}" for s in parents}
        evidence = case["output"]["evidence_sha256"]
        save_json(
            tmp_path / "reviews" / (case["name"] + ".json"),
            {
                "case_id": case["name"],
                "reviewer": "offline-fixture",
                "reviewed_at": "2026-09-29T00:00:00Z",
                "passed": True,
                "reason": "Fixture only, not a real requirements review",
                "outcome_sha256": evidence["outcome_sha256"],
                "verification_event_sha256": evidence["verification_event_sha256"],
            },
        )
    report = await build_report_async(tmp_path, bindings_unchanged=True)
    assert report["scoring"]["task_summary"]["tasks_passed"] == 2
    assert len(rows) == count
    assert all(
        r["result"]["score"] == 1 for r in rows.values() if r["name"] == "task_pass"
    )
    for n in names:
        (tmp_path / "reviews" / (n + ".json")).unlink()
    await build_report_async(tmp_path, bindings_unchanged=True)
    assert all(
        r["result"]
        == {"label": "unknown", "explanation": "No value in the current local report."}
        for r in rows.values()
        if r["name"] == "task_pass"
    )


@pytest.mark.asyncio
async def test_resume_links_latest_attempt_without_reparenting_history(
    tmp_path, traces, mechanics_runtime
):
    names, behavior, rows = mechanics_runtime
    name = names[0]
    behavior["interrupt"] = True
    await run_case(tmp_path, name)
    await build_report_async(tmp_path, bindings_unchanged=True)
    first = read_json(tmp_path / "telemetry" / name / "001.json")
    behavior["interrupt"] = False
    await run_case(tmp_path, name, resume=True)
    await build_report_async(tmp_path, bindings_unchanged=True)
    second = read_json(tmp_path / "telemetry" / name / "002.json")
    assert first["trace_id"] != second["trace_id"]
    statuses = {
        key[0]: row["result"]["label"]
        for key, row in rows.items()
        if row["name"] == "task_status"
    }
    assert statuses[first["span_id"]] == "unknown"
    assert statuses[second["span_id"]] == "pending_review"
    parents = [s for s in traces.get_finished_spans() if s.name == "evaluation.case"]
    assert {s.attributes["session.id"] for s in parents} == {"fixture-" + name}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["state", "attempt", "untraced_resume", "corrupt", "batch", "endpoint"]
)
async def test_stale_trace_does_not_receive_new_scores(
    tmp_path, traces, mechanics_runtime, change, monkeypatch
):
    names, _, rows = mechanics_runtime
    name = names[0]
    await run_case(tmp_path, name)
    state = tmp_path / name / "state"
    link_path = tmp_path / "telemetry" / name / "001.json"
    if change == "state":
        (state / "events.jsonl").write_text((state / "events.jsonl").read_text() + "\n")
    elif change == "attempt":
        save_json(state / "attempts" / "001.json", {"error_type": "changed"})
    elif change == "untraced_resume":
        save_json(state / "attempts" / "002.json", {"mode": "resume"})
    elif change == "corrupt":
        link_path.write_text("broken")
    elif change == "batch":
        link = read_json(link_path)
        link["batch_id"] = "different"
        save_json(link_path, link)
    else:
        monkeypatch.setattr(telemetry, "_endpoint", "http://127.0.0.1:6007")
    await build_report_async(tmp_path, bindings_unchanged=True)
    assert not rows


@pytest.mark.asyncio
async def test_export_failure_keeps_local_report_and_outcome(
    tmp_path, traces, mechanics_runtime, monkeypatch
):
    name = mechanics_runtime[0][0]
    await run_case(tmp_path, name)
    outcome = (tmp_path / name / "state" / "outcome.json").read_bytes()

    def unavailable(**kwargs):
        raise ConnectionError("fixture unavailable")

    monkeypatch.setattr(phoenix.client, "Client", unavailable)
    report = await build_report_async(tmp_path, bindings_unchanged=True)
    assert report["scoring"]["task_summary"]["tasks_pending_review"] == 1
    assert (tmp_path / name / "state" / "outcome.json").read_bytes() == outcome
    assert (tmp_path / "report.md").is_file()


@pytest.mark.asyncio
async def test_disabled_and_sidecar_failure_do_not_change_execution(
    tmp_path, mechanics_runtime, monkeypatch
):
    name = mechanics_runtime[0][0]
    await run_case(tmp_path, name)
    assert not (tmp_path / "telemetry").exists()
    await build_report_async(tmp_path, bindings_unchanged=True)
    assert not mechanics_runtime[2]


@pytest.mark.asyncio
async def test_trace_record_failure_does_not_fail_case(
    tmp_path, traces, mechanics_runtime, monkeypatch
):
    def unwritable(*args, **kwargs):
        raise OSError("fixture disk failure")

    monkeypatch.setattr(bridge, "write_text_atomic", unwritable)
    name = mechanics_runtime[0][0]
    await run_case(tmp_path, name)
    assert (
        read_json(tmp_path / name / "state" / "outcome.json")["status"] == "completed"
    )
    report = await build_report_async(tmp_path, bindings_unchanged=True)
    assert report["scoring"]["task_summary"]["tasks_pending_review"] == 1


@pytest.mark.asyncio
async def test_cancel_closes_attempt_trace(tmp_path, traces, mechanics_runtime):
    name, behavior = mechanics_runtime[0][0], mechanics_runtime[1]
    behavior["cancel"] = True
    with pytest.raises(asyncio.CancelledError):
        await run_case(tmp_path, name)
    parent = next(s for s in traces.get_finished_spans() if s.name == "evaluation.case")
    assert parent.attributes["operation.status"] == "cancelled"
    assert read_json(tmp_path / "telemetry" / name / "001.json")["span_id"]


@pytest.mark.parametrize("enabled", [False, True])
def test_launch_forwards_explicit_flag_only(tmp_path, monkeypatch, enabled):
    (tmp_path / "control").mkdir()
    for function in ("_checked_preflight", "_ensure_health"):
        monkeypatch.setattr(suite, function, lambda root: None)
    monkeypatch.setattr(suite, "selected_cases", lambda *args: None)
    commands = []
    monkeypatch.setattr(
        suite.subprocess,
        "Popen",
        lambda command, **kw: commands.append(command) or SimpleNamespace(pid=123),
    )
    suite.launch(tmp_path, "resume", ["example"], phoenix=enabled)
    assert ("--phoenix" in commands[0]) is enabled
    assert commands[0][-2:] == ["--case", "example"]


@pytest.mark.asyncio
async def test_native_dataset_can_invoke_existing_mechanics_executor(
    tmp_path, mechanics_runtime
):
    from pydantic_evals import Case, Dataset

    from abaqus_sim_agent.evaluation.mechanics_grading import (
        TaskPassed,
        observe_saved_case,
    )
    from abaqus_sim_agent.evaluation.public_mechanics_control import summarize_case

    name = mechanics_runtime[0][0]
    manifest = read_json(tmp_path / "manifest.json")

    async def task(case_id):
        await run_case(tmp_path, case_id)
        return observe_saved_case(
            tmp_path,
            get_case(case_id),
            summarize_case(tmp_path, case_id),
            manifest,
            bindings_unchanged=True,
        )

    report = await Dataset(
        name="mechanics-adapter-fixture",
        cases=[Case(name=name, inputs=name, metadata=get_case(name).metadata)],
        evaluators=[TaskPassed()],
    ).evaluate(
        task,
        max_concurrency=1,
        repeat=1,
        retry_task=None,
        retry_evaluators=None,
        progress=False,
    )
    assert not report.failures
    row = report.cases[0]
    assert row.assertions["physics_ok"].value is True
    assert row.labels["task_status"].value == "pending_review"
    assert "task_pass" not in row.assertions
    assert len(list((tmp_path / name / "state/attempts").glob("*.json"))) == 1


def configure_suite(root, monkeypatch, *, concurrency=1, case_count=2):
    from pydantic import SecretStr

    manifest = read_json(root / "manifest.json")
    manifest.update(
        case_ids=list(BASIC_LINEAR_CASE_IDS[:case_count]),
        max_task_concurrency=concurrency,
        max_runtime_concurrency=1,
    )
    save_json(root / "manifest.json", manifest)
    save_json(
        root / "cases.json",
        [get_case(n).model_dump(mode="json") for n in manifest["case_ids"]],
    )
    save_json(
        root / "seal.json",
        {
            "files": {
                name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                for name in ("manifest.json", "cases.json")
            }
        },
    )
    monkeypatch.setattr(suite, "_checked_preflight", lambda root: None)
    monkeypatch.setattr(suite, "_ensure_health", lambda root: None)
    monkeypatch.setattr(suite, "verify_binding", lambda root: None)
    monkeypatch.setattr(suite.ApiCatalog, "open", lambda root: None)
    monkeypatch.setattr(
        suite,
        "take_gateway_api_key_from_environment",
        lambda gateway: SecretStr("fixture"),
    )
    return manifest["case_ids"]


@pytest.mark.asyncio
async def test_suite_executes_through_native_dataset_and_rebuild_does_not_execute(
    tmp_path, mechanics_runtime, traces, monkeypatch
):
    names = configure_suite(tmp_path, monkeypatch)
    await suite.run(tmp_path)
    report = read_json(tmp_path / "report.json")
    assert report["native_result_kind"] == "case_execution"
    assert report["scoring"]["task_summary"]["tasks_pending_review"] == 2
    native = read_json(tmp_path / "pydantic-evals-report.json")
    assert native["experiment_metadata"]["action"] == "run"
    assert not native["failures"]
    for case in native["cases"]:
        assert case["attributes"]["duration_scope"] == "case_execution_and_observation"
        assert case["assertions"]["physics_ok"]["value"] is True
        assert case["span_id"]
    assert len(list((tmp_path / "control").glob("evaluation-*.json"))) == 1
    attempts = {
        n: (tmp_path / n / "state/attempts/001.json").read_bytes() for n in names
    }
    spans = traces.get_finished_spans()

    async def forbidden(*args, **kwargs):
        pytest.fail("report or continue replayed a completed case")

    monkeypatch.setattr(suite, "_run_case", forbidden)
    progress_before = (tmp_path / "progress.json").read_bytes()
    await build_report_async(tmp_path, bindings_unchanged=True)
    assert (tmp_path / "progress.json").read_bytes() == progress_before
    assert (
        read_json(tmp_path / "report.json")["native_result_kind"]
        == "saved_result_projection"
    )
    await suite.run(tmp_path, "continue")
    assert {
        n: (tmp_path / n / "state/attempts/001.json").read_bytes() for n in names
    } == attempts
    assert traces.get_finished_spans() == spans
    progress = read_json(tmp_path / "progress.json")
    assert progress["handled_cases"] == 2
    assert all(not row["selected_for_execution"] for row in progress["cases"])
    assert all(
        row["duration_scope"] == "saved_result_projection_only"
        for row in progress["cases"]
    )
    assert (
        read_json(tmp_path / "report.json")["scoring"]["task_summary"]["planned_tasks"]
        == 2
    )


@pytest.mark.asyncio
async def test_suite_resume_and_continue_keep_original_denominator(
    tmp_path, mechanics_runtime, monkeypatch
):
    names = configure_suite(tmp_path, monkeypatch)
    behavior = mechanics_runtime[1]
    behavior["interrupt"] = True
    await suite.run(tmp_path)
    assert not (tmp_path / names[1]).exists()
    state = tmp_path / names[0] / "state/resume_state.json"
    saved = read_json(state)
    saved["active_wall_time_seconds"] = 12.0
    saved["usage"] = {"requests": 3}
    save_json(state, saved)
    behavior["interrupt"] = False
    await suite.run(tmp_path, "resume", names[:1])
    report = read_json(tmp_path / "report.json")
    assert report["scoring"]["task_summary"]["planned_tasks"] == 2
    assert report["scoring"]["task_summary"]["tasks_pending_review"] == 1
    assert report["scoring"]["task_summary"]["tasks_unknown"] == 1
    assert len(list((state.parent / "attempts").glob("*.json"))) == 2
    assert not (tmp_path / names[1]).exists()
    first_attempts = sorted(
        p.read_bytes() for p in (state.parent / "attempts").glob("*.json")
    )
    await suite.run(tmp_path, "continue")
    assert (
        sorted(p.read_bytes() for p in (state.parent / "attempts").glob("*.json"))
        == first_attempts
    )
    assert (
        read_json(tmp_path / "report.json")["scoring"]["task_summary"][
            "tasks_pending_review"
        ]
        == 2
    )


@pytest.mark.asyncio
async def test_suite_parallel_failure_drains_active_case_and_skips_queued_case(
    tmp_path, mechanics_runtime, monkeypatch
):
    names = configure_suite(tmp_path, monkeypatch, concurrency=2, case_count=3)
    both_started = asyncio.Event()
    release = asyncio.Event()
    called = []
    stopped = asyncio.Event()
    original_save = suite.save_json

    def record_health(path, value):
        original_save(path, value)
        if path == tmp_path / "health.json" and not value["passed"]:
            stopped.set()

    monkeypatch.setattr(suite, "save_json", record_health)

    async def execute(root, name, **kwargs):
        called.append(name)
        if name == names[0]:
            await both_started.wait()
            save_json(
                root / name / "state/resume_state.json",
                {"status": "interrupted", "last_error_type": "ModelAPIError"},
            )
        elif name == names[1]:
            both_started.set()
            await release.wait()
            save_json(
                root / name / "state/outcome.json",
                CodingRunOutcome(
                    run_id="fixture",
                    status=CodingRunStatus.FAILED,
                    summary="ordinary task failure",
                    failure_stage="model_budget",
                ).model_dump(mode="json"),
            )
        else:
            pytest.fail("queued case started after the provider failure")

    monkeypatch.setattr(suite, "_run_case", execute)
    job = asyncio.create_task(suite.run(tmp_path))
    async with asyncio.timeout(5):
        await stopped.wait()
        assert not job.done()
        assert called == names[:2]
        assert not (tmp_path / names[2]).exists()
        release.set()
        await job
    report = read_json(tmp_path / "report.json")
    assert [r["classification"] for r in report["trials"]] == [
        "provider_interrupted",
        "task_failed",
        "not_started",
    ]
    assert report["scoring"]["task_summary"]["planned_tasks"] == 3
    assert report["scoring"]["task_summary"]["tasks_failed"] == 1


@pytest.mark.asyncio
async def test_suite_case_and_runtime_concurrency_are_separate(
    tmp_path, mechanics_runtime, monkeypatch
):
    configure_suite(tmp_path, monkeypatch, concurrency=2, case_count=3)
    active = runtime_active = peak = runtime_peak = 0

    async def execute(root, name, *, runtime_gate, **kwargs):
        nonlocal active, runtime_active, peak, runtime_peak
        active += 1
        peak = max(peak, active)
        async with runtime_gate:
            runtime_active += 1
            runtime_peak = max(runtime_peak, runtime_active)
            await asyncio.sleep(0.01)
            runtime_active -= 1
        save_json(
            root / name / "state/outcome.json",
            CodingRunOutcome(
                run_id="fixture",
                status=CodingRunStatus.FAILED,
                summary="fixture",
                failure_stage="model_budget",
            ).model_dump(mode="json"),
        )
        active -= 1

    monkeypatch.setattr(suite, "_run_case", execute)
    await suite.run(tmp_path)
    assert (peak, runtime_peak) == (2, 1)
    assert (
        read_json(tmp_path / "report.json")["scoring"]["task_summary"]["tasks_failed"]
        == 3
    )


@pytest.mark.asyncio
async def test_suite_setup_exception_stops_execution_but_native_failure_is_preserved(
    tmp_path, mechanics_runtime, monkeypatch
):
    from pydantic_evals.reporting import EvaluationReportAdapter

    names = configure_suite(tmp_path, monkeypatch)
    called = []

    async def execute(root, name, **kwargs):
        called.append(name)
        raise OSError("do-not-export-this-private-detail")

    monkeypatch.setattr(suite, "_run_case", execute)
    await suite.run(tmp_path)
    assert called == names[:1]
    raw = (tmp_path / "pydantic-evals-report.json").read_bytes()
    assert b"do-not-export-this-private-detail" not in raw
    native = EvaluationReportAdapter.validate_json(raw)
    assert len(native.failures) == 1
    assert native.failures[0].name == names[0]
    assert (
        read_json(tmp_path / "report.json")["scoring"]["task_summary"][
            "tasks_grading_error"
        ]
        == 1
    )
    assert not (tmp_path / names[1]).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("report_level", [False, True])
async def test_suite_does_not_regrade_after_native_evaluator_failure(
    tmp_path, mechanics_runtime, monkeypatch, report_level
):
    from abaqus_sim_agent.evaluation.mechanics_grading import SuiteSummary, TaskPassed

    names = configure_suite(tmp_path, monkeypatch)
    original = (SuiteSummary if report_level else TaskPassed).evaluate
    calls = 0

    def fail_only_live(self, ctx):
        nonlocal calls
        calls += 1
        if (
            report_level
            and any(case.output.status == "completed" for case in ctx.report.cases)
        ) or (not report_level and ctx.output.status == "completed"):
            raise RuntimeError("fixture evaluator failed")
        return original(self, ctx)

    monkeypatch.setattr(
        SuiteSummary if report_level else TaskPassed, "evaluate", fail_only_live
    )
    await suite.run(tmp_path)
    report = read_json(tmp_path / "report.json")
    native = read_json(tmp_path / "pydantic-evals-report.json")
    assert calls == (1 if report_level else len(names))
    assert report["scoring"]["summary_valid"] is not report_level
    if report_level:
        assert native["report_evaluator_failures"]
    else:
        assert all(c["evaluator_failures"] for c in native["cases"])
        assert report["scoring"]["task_summary"]["tasks_grading_error"] == 2
    assert all(
        len(list((tmp_path / n / "state/attempts").glob("*.json"))) == 1 for n in names
    )


@pytest.mark.asyncio
async def test_suite_cancel_rebuilds_full_report_without_reexecution(
    tmp_path, mechanics_runtime, monkeypatch
):
    names = configure_suite(tmp_path, monkeypatch)
    mechanics_runtime[1].update(cancel=True, interrupt=True)
    with pytest.raises(asyncio.CancelledError):
        await suite.run(tmp_path)
    report = read_json(tmp_path / "report.json")
    assert report["native_result_kind"] == "saved_result_projection"
    assert report["scoring"]["task_summary"]["planned_tasks"] == 2
    assert report["trials"][0]["classification"] == "interrupted"
    assert report["trials"][1]["classification"] == "not_started"
    assert not (tmp_path / names[1]).exists()
    assert (
        read_json(tmp_path / names[0] / "state/attempts/001.json")["error_type"]
        == "CancelledError"
    )
    assert '"event": "batch_stopped"' in (tmp_path / "control/events.jsonl").read_text(
        encoding="utf-8"
    )

    progress = read_json(tmp_path / "progress.json")
    assert progress["cases"][0]["phase"] == "interrupted"
    assert progress["cases"][0]["task_verdict"] is None
    # The SDK may enter the next lifecycle before cancellation is delivered, but no business task starts.
    assert progress["cases"][1]["phase"] in {"pending", "interrupted"}
    assert progress["cases"][1]["run_status"] is None
    assert progress["cases"][1]["task_verdict"] is None


@pytest.mark.asyncio
async def test_external_batch_cancel_waits_for_case_cleanup_and_rebuilds(
    tmp_path, mechanics_runtime, monkeypatch
):
    names = configure_suite(tmp_path, monkeypatch)
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    async def execute(root, name, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            save_json(
                root / name / "state/resume_state.json", {"status": "interrupted"}
            )
            cleaned.set()

    monkeypatch.setattr(suite, "_run_case", execute)
    job = asyncio.create_task(suite.run(tmp_path))
    await entered.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert cleaned.is_set()
    assert not (tmp_path / names[1]).exists()
    assert (
        read_json(tmp_path / "report.json")["scoring"]["task_summary"]["planned_tasks"]
        == 2
    )


@pytest.mark.asyncio
async def test_case_lifecycle_publishes_progress_before_whole_batch_finishes(
    tmp_path, mechanics_runtime, monkeypatch
):
    names = configure_suite(tmp_path, monkeypatch)
    original = suite._run_case
    second_started = asyncio.Event()
    finish_second = asyncio.Event()

    async def execute(root, name, **kwargs):
        if name == names[1]:
            second_started.set()
            await finish_second.wait()
        await original(root, name, **kwargs)

    monkeypatch.setattr(suite, "_run_case", execute)
    job = asyncio.create_task(suite.run(tmp_path))
    async with asyncio.timeout(5):
        await second_started.wait()
        try:
            assert not job.done()
            assert not (tmp_path / "report.json").exists()
            assert not (tmp_path / "pydantic-evals-report.json").exists()
            progress = read_json(tmp_path / "progress.json")
            assert progress["handled_cases"] == 1
            assert progress["planned_cases"] == 2
            assert progress["cases"][0]["phase"] == "evaluated"
            assert progress["cases"][0]["task_verdict"] == "pending_review"
            assert progress["cases"][1]["phase"] == "evaluating"
            assert progress["cases"][1]["task_verdict"] is None
            assert "1/2" in (tmp_path / "progress.md").read_text(encoding="utf-8")
        finally:
            finish_second.set()
            await job
    assert read_json(tmp_path / "progress.json")["handled_cases"] == 2
    assert read_json(tmp_path / "report.json")["native_result_kind"] == "case_execution"


@pytest.mark.asyncio
async def test_progress_write_failure_does_not_change_case_execution_or_grades(
    tmp_path, mechanics_runtime, monkeypatch
):
    from abaqus_sim_agent.evaluation import mechanics_progress

    names = configure_suite(tmp_path, monkeypatch)

    def unwritable(*args, **kwargs):
        raise OSError("fixture progress file unavailable")

    monkeypatch.setattr(mechanics_progress, "write_text_atomic", unwritable)
    await suite.run(tmp_path)
    report = read_json(tmp_path / "report.json")
    assert report["scoring"]["task_summary"]["tasks_pending_review"] == 2
    assert all(
        len(list((tmp_path / name / "state/attempts").glob("*.json"))) == 1
        for name in names
    )
    assert not (tmp_path / "progress.json").exists()


def add_fixture_review(root, name):
    state = root / name / "state"
    save_json(
        root / "reviews" / f"{name}.json",
        {
            "case_id": name,
            "reviewer": "offline-test",
            "reviewed_at": "2026-10-02T00:00:00Z",
            "passed": True,
            "reason": "Synthetic test only",
            "outcome_sha256": hashlib.sha256(
                (state / "outcome.json").read_bytes()
            ).hexdigest(),
            "verification_event_sha256": hashlib.sha256(
                (state / "events.jsonl").read_bytes().splitlines()[-1]
            ).hexdigest(),
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("withdraw", [False, True])
async def test_review_update_during_batch_keeps_execution_snapshot_and_can_refresh(
    tmp_path, mechanics_runtime, monkeypatch, withdraw, traces
):
    names = configure_suite(tmp_path, monkeypatch)
    original = suite._run_case
    second_started = asyncio.Event()
    release = asyncio.Event()

    async def execute(root, name, **kwargs):
        if name == names[1]:
            second_started.set()
            await release.wait()
        await original(root, name, **kwargs)
        if name == names[0] and withdraw:
            add_fixture_review(root, name)

    monkeypatch.setattr(suite, "_run_case", execute)
    job = asyncio.create_task(suite.run(tmp_path))
    await second_started.wait()
    if withdraw:
        (tmp_path / "reviews" / f"{names[0]}.json").unlink()
    else:
        add_fixture_review(tmp_path, names[0])
    release.set()
    await job
    report = read_json(tmp_path / "report.json")
    assert [r["classification"] for r in report["trials"]] == ["completed", "completed"]
    assert report["review_updates_pending"] == [names[0]]
    span = read_json(tmp_path / "telemetry" / names[0] / "001.json")["span_id"]
    assert not any(key[0] == span for key in mechanics_runtime[2])
    assert report["scoring"]["task_summary"]["tasks_passed"] == int(withdraw)
    assert "审查记录已有更新" in (tmp_path / "report.md").read_text(encoding="utf-8")
    original_archives = {
        p.name: p.read_bytes() for p in (tmp_path / "control").glob("evaluation-*.json")
    }
    await build_report_async(tmp_path, bindings_unchanged=True)
    refreshed = read_json(tmp_path / "report.json")
    assert refreshed["review_updates_pending"] == []
    assert any(key[0] == span for key in mechanics_runtime[2])
    assert refreshed["scoring"]["task_summary"]["tasks_passed"] == int(not withdraw)
    assert {
        p.name: p.read_bytes() for p in (tmp_path / "control").glob("evaluation-*.json")
    } == original_archives
    assert all(
        len(list((tmp_path / n / "state/attempts").glob("*.json"))) == 1 for n in names
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("grading_failure", [False, True])
async def test_error_before_later_cancellation_survives_report_rebuilding(
    tmp_path, mechanics_runtime, monkeypatch, grading_failure
):
    from abaqus_sim_agent.evaluation.mechanics_grading import TaskPassed

    names = configure_suite(tmp_path, monkeypatch, concurrency=2)
    second_started = asyncio.Event()
    error_recorded = asyncio.Event()
    original = suite._run_case
    original_grade = TaskPassed.evaluate
    from abaqus_sim_agent.evaluation import public_mechanics_control as control

    original_append = control.append_event

    def append(path, event, **values):
        original_append(path, event, **values)
        if event == "case_evaluation_error":
            error_recorded.set()

    def grade(self, ctx):
        if (
            grading_failure
            and ctx.inputs == names[0]
            and ctx.attributes.get("duration_scope") == "case_execution_and_observation"
        ):
            raise ValueError("synthetic grader failure")
        return original_grade(self, ctx)

    async def execute(root, name, **kwargs):
        if name == names[0]:
            await second_started.wait()
            if grading_failure:
                await original(root, name, **kwargs)
            else:
                raise OSError("private setup detail")
        else:
            second_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                save_json(
                    root / name / "state/resume_state.json", {"status": "interrupted"}
                )

    monkeypatch.setattr(control, "append_event", append)
    monkeypatch.setattr(suite, "_run_case", execute)
    monkeypatch.setattr(TaskPassed, "evaluate", grade)
    job = asyncio.create_task(suite.run(tmp_path))
    try:
        await asyncio.wait_for(error_recorded.wait(), timeout=2)
    finally:
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    report = read_json(tmp_path / "report.json")
    errors = report["evaluation_errors"]
    assert len(errors) == 1
    assert errors[0]["case_id"] == names[0]
    assert errors[0]["stage"] == ("grading" if grading_failure else "task")
    assert "private setup detail" not in json.dumps(errors)
    assert report["trials"][0]["classification"] == (
        "completed" if grading_failure else "setup_error"
    )
    assert "已记录的评测异常" in (tmp_path / "report.md").read_text(encoding="utf-8")
    await build_report_async(tmp_path, bindings_unchanged=True)
    assert read_json(tmp_path / "report.json")["evaluation_errors"] == errors
    assert not list((tmp_path / "control").glob("evaluation-*.json"))
    if not grading_failure:
        # Resuming another case must keep the earlier startup error visible without fabricating a workspace.
        state = tmp_path / names[1] / "state/resume_state.json"
        saved = read_json(state)
        saved["active_wall_time_seconds"] = 0
        save_json(state, saved)
        monkeypatch.setattr(suite, "_run_case", original)
        await suite.run(tmp_path, "resume", names[1:])
        resumed = read_json(tmp_path / "report.json")
        assert resumed["trials"][0]["classification"] == "setup_error"
        assert resumed["trials"][1]["classification"] == "completed"
        assert resumed["evaluation_errors"] == errors
        assert not (tmp_path / names[0]).exists()
        # A successful explicit retry has its own Service evidence; prior errors remain history, not a failed new outcome.
        await suite.run(tmp_path, "continue")
        continued = read_json(tmp_path / "report.json")
        assert all(row["classification"] == "completed" for row in continued["trials"])
        assert continued["evaluation_errors"] == errors
