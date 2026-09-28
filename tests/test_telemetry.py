from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import phoenix.otel
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from abaqus_sim_agent import telemetry
from abaqus_sim_agent.coding.cli import build_parser, run_cli
from abaqus_sim_agent.execution.abaqus2024 import Abaqus2024Runner, ProcessOutput

TOKEN = "private-telemetry-value-7fe730"


@pytest.fixture
def captured(monkeypatch, tmp_path):
    exporter = InMemorySpanExporter()
    calls = []

    def local_exporter(**kwargs):
        calls.append(kwargs)
        return exporter

    monkeypatch.setattr(phoenix.otel, "HTTPSpanExporter", local_exporter)
    # Isolate the SDK global only in tests; a production process configures once.
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(
        trace,
        "set_tracer_provider",
        lambda provider: setattr(trace, "_TRACER_PROVIDER", provider),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", TOKEN)
    with telemetry.telemetry_session(True, environment="offline-test"):
        yield exporter, calls


def finished(exporter):
    assert telemetry.flush()
    return [
        span
        for span in exporter.get_finished_spans()
        if span.attributes.get("logfire.span_type") != "pending_span"
    ]


def test_disabled_does_not_configure_or_inherit_credentials(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("disabled session configured Phoenix")

    monkeypatch.setattr(telemetry, "_create_provider", forbidden)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.phoenix").write_text(
        "PHOENIX_COLLECTOR_ENDPOINT=https://invalid.example"
    )
    monkeypatch.setenv("PHOENIX_API_KEY", TOKEN)
    monkeypatch.setenv("PHOENIX_CLIENT_HEADERS", "Authorization=private")
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "https://invalid.example")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=private")
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    with telemetry.telemetry_session(False, environment="cli"):
        assert telemetry.instrumentation_settings() is None
        assert "PHOENIX_API_KEY" not in os.environ
        assert "PHOENIX_CLIENT_HEADERS" not in os.environ
        assert "OTEL_EXPORTER_OTLP_HEADERS" not in os.environ
        with telemetry.span("must_not_record") as current:
            assert not current.is_recording()
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://remote.example",
        "http://localhost@evil.test",
        "http://127.0.0.1:6006/?key=private",
        "http://127.0.0.1:bad",
        "http://127.0.0.1:0",
    ],
)
def test_cli_rejects_remote_or_invalid_endpoint_before_workspace_changes(
    monkeypatch, tmp_path, endpoint
):
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", endpoint)
    workspace = tmp_path / "must-not-exist"
    assert run_cli(["--phoenix", "--workspace", str(workspace), "--goal", "test"]) == 1
    assert not workspace.exists()


def test_flag_and_sdk_settings(captured):
    _, calls = captured
    assert build_parser().parse_args(["--phoenix"]).phoenix is True
    assert build_parser().parse_args([]).phoenix is False
    assert calls[0]["endpoint"] == "http://127.0.0.1:6006/v1/traces"
    assert calls[0]["session"].trust_env is False
    assert calls[0]["session"].max_redirects == 0
    settings = telemetry.instrumentation_settings()
    assert settings is not None
    assert settings.include_content is True
    assert settings.include_binary_content is False
    assert settings.include_model_request_parameters is False
    assert TOKEN not in json.dumps(telemetry.telemetry_metadata())


def test_secret_redaction_and_business_text(captured, capsys):
    exporter, _ = captured
    with telemetry.span(
        "diagnostic",
        ordinary="SCRIPT_CONTENT_SENTINEL",
        detail=TOKEN,
        csrf="CSRF_SENTINEL",
    ):
        pass
    spans = finished(exporter)
    data = json.dumps([dict(s.attributes) for s in spans])
    assert "SCRIPT_CONTENT_SENTINEL" in data
    assert TOKEN not in data
    assert "CSRF_SENTINEL" not in data
    assert "diagnostic" not in capsys.readouterr().out


def test_nested_and_exception_credentials_are_scrubbed(captured):
    exporter, _ = captured
    with pytest.raises(ValueError, match="failure"):
        with telemetry.span("failed-operation", nested=json.dumps({"value": TOKEN})):
            raise ValueError("failure containing " + TOKEN)
    span = next(s for s in finished(exporter) if s.name == "failed-operation")
    data = json.dumps(
        {
            "attributes": dict(span.attributes),
            "events": [dict(e.attributes) for e in span.events],
        }
    )
    assert TOKEN not in data
    assert span.status.status_code.name == "ERROR"


class FakeProcess:
    def __init__(self, output=None):
        self.output = output or ProcessOutput(0, b"done", b"")

    async def run(self, command, *, cwd, timeout_seconds):
        return self.output


@pytest.mark.asyncio
async def test_runner_records_structured_failure_and_raw_bytes(captured, tmp_path):
    exporter, _ = captured
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "model.py").write_text("pass")
    raw = b"Abaqus Error: simulated error\r\n"
    runner = Abaqus2024Runner(
        workspace,
        tmp_path / "state",
        process_strategy=FakeProcess(ProcessOutput(0, raw, b"")),
    )
    with telemetry.execution_context(run_id="fixture-run"):
        result = await runner.run_cae_script("model.py")
    assert result.succeeded
    assert not result.stage_succeeded
    assert result.stdout_raw_path.read_bytes() == raw
    runtime = next(s for s in finished(exporter) if s.name == "abaqus.runtime")
    assert runtime.attributes["run_id"] == "fixture-run"
    assert runtime.attributes["process_succeeded"] is True
    assert runtime.attributes["stage_succeeded"] is False


@pytest.mark.asyncio
async def test_background_solver_execution_identity(captured, tmp_path):
    exporter, _ = captured
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "job.inp").write_text("*HEADING\nfixture")
    runner = Abaqus2024Runner(
        workspace, tmp_path / "state", process_strategy=FakeProcess()
    )
    with telemetry.span("agent-work") as parent:
        with telemetry.execution_context(run_id="solver-run"):
            execution_id = runner.submit_solver_job("job", input_file="job.inp")
        await runner.wait_solver_job(execution_id)
    spans = finished(exporter)
    runtime = next(s for s in spans if s.name == "abaqus.runtime")
    assert runtime.context.trace_id == parent.get_span_context().trace_id
    assert runtime.attributes["execution_id"] == execution_id
    assert runtime.attributes["run_id"] == "solver-run"


@pytest.mark.asyncio
async def test_cancellation_propagates(captured):
    exporter, _ = captured
    started = asyncio.Event()

    async def execute():
        with telemetry.span("cancel-me"):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(execute())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    span = next(s for s in finished(exporter) if s.name == "cancel-me")
    assert span.attributes["operation.status"] == "cancelled"
    assert span.status.status_code.name != "ERROR"


def test_actual_web_file_delivery_links_verifier(captured, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from tests.support.web import login, settled
    from tests.test_open_requests import new_request, open_request_app

    exporter, _ = captured
    app, _ = open_request_app(tmp_path, mode="script")
    with (
        telemetry.instrument_web(app),
        TestClient(app, base_url="http://127.0.0.1") as client,
    ):
        login(client)
        rid = new_request(client, "Write a Python script, without running Abaqus")
        view = settled(client, rid, status="completed")["view"]
    assert view["outcome"]["verification_scope"] == "execution_evidence"
    assert view["usage"]["model_requests"] == 2
    assert view["usage"]["abaqus_calls"] == 0
    spans = finished(exporter)
    segment = next(s for s in spans if s.name == "coding.work_segment")
    agent = next(s for s in spans if s.name.startswith("invoke_agent"))
    verifier = next(s for s in spans if s.name == "coding.verify")
    http = next(s for s in spans if s.name.endswith("/start"))
    assert len({s.context.trace_id for s in (segment, agent, verifier, http)}) == 1
    assert agent.parent.span_id == segment.context.span_id
    assert verifier.parent.span_id == segment.context.span_id
    assert segment.attributes["run_id"] == rid
    assert "invocation_id" in segment.attributes
    assert segment.attributes["product.status"] == "completed"
    assert verifier.attributes["verification.passed"] is True


@pytest.mark.asyncio
async def test_native_evals_content_metrics_and_large_message(captured):
    pytest.importorskip("pydantic_evals")
    from pydantic_ai import Agent
    from pydantic_ai.capabilities import Instrumentation
    from pydantic_ai.models.test import TestModel
    from pydantic_evals import Case, Dataset
    from pydantic_evals.dataset import increment_eval_metric

    from abaqus_sim_agent.evaluation.telemetry import PhoenixCaseLifecycle

    exporter, _ = captured
    agent = Agent(
        TestModel(custom_output_text="OUTPUT_SENTINEL"),
        capabilities=[Instrumentation(settings=telemetry.instrumentation_settings())],
    )

    async def task(value):
        increment_eval_metric("case_metric", 2)
        return (await agent.run(value)).output

    report = await Dataset(
        name="synthetic",
        cases=[Case(name="one", inputs="INPUT_SENTINEL" + "x" * 300_000)],
    ).evaluate(task, progress=False, lifecycle=PhoenixCaseLifecycle)
    assert report.cases[0].metrics["case_metric"] == 2
    assert report.cases[0].output == "OUTPUT_SENTINEL"
    spans = finished(exporter)
    assert len({s.context.trace_id for s in spans}) == 1
    data = json.dumps([dict(s.attributes) for s in spans])
    assert "INPUT_SENTINEL" in data
    assert "OUTPUT_SENTINEL" in data


@pytest.mark.parametrize(
    "scenario", ["stop_background", "cancel_background", "continuation_restart"]
)
def test_existing_lifecycle_contracts_with_tracing(captured, tmp_path, scenario):
    pytest.importorskip("fastapi")
    from tests.test_web_conversation import (
        test_three_turns_keep_context_order_budget_and_survive_restart,
    )
    from tests.test_web_stop import (
        test_cancel_background_solver_after_task_stop_allows_controlled_continue,
        test_stop_current_task_keeps_background_solver_running,
    )

    scenarios = {
        "stop_background": test_stop_current_task_keeps_background_solver_running,
        "cancel_background": test_cancel_background_solver_after_task_stop_allows_controlled_continue,
        "continuation_restart": test_three_turns_keep_context_order_budget_and_survive_restart,
    }
    scenarios[scenario](tmp_path)
    exporter, _ = captured
    segments = [s for s in finished(exporter) if s.name == "coding.work_segment"]
    assert segments
    if scenario == "continuation_restart":
        assert len(segments) >= 3
        assert len({s.context.span_id for s in segments}) == len(segments)
        assert len({s.attributes["run_id"] for s in segments}) == 1
    else:
        cancelled = [
            s for s in segments if s.attributes.get("operation.status") == "cancelled"
        ]
        assert cancelled
        assert all(s.status.status_code.name != "ERROR" for s in cancelled)


def test_export_failure_does_not_fail_operation(captured, monkeypatch):
    exporter, _ = captured

    def fail(*args, **kwargs):
        raise ConnectionError("synthetic exporter failure")

    monkeypatch.setattr(exporter, "export", fail)
    with telemetry.span("export-failed"):
        result = 42
    assert result == 42


def test_shutdown_is_bounded(monkeypatch):
    import threading

    completed = threading.Event()
    release = threading.Event()

    class Provider:
        def shutdown(self):
            release.wait()
            completed.set()

    monkeypatch.setattr(telemetry, "SHUTDOWN_TIMEOUT_MILLIS", 10)
    try:
        telemetry._shutdown(Provider())
        assert not completed.is_set()
    finally:
        release.set()
        assert completed.wait(1)


def test_import_and_help_do_not_connect(tmp_path):
    script = """
import os, socket, sys
os.environ['PHOENIX_API_KEY'] = 'must-not-use'
os.environ['PHOENIX_COLLECTOR_ENDPOINT'] = 'https://invalid.example'
os.environ['OTEL_EXPORTER_OTLP_ENDPOINT'] = 'https://invalid.example'
def forbidden(*args, **kwargs):
    raise AssertionError('unexpected network')
socket.socket.connect = forbidden
import abaqus_sim_agent.telemetry
from abaqus_sim_agent.coding.cli import run_cli
try:
    run_cli(['--help'])
except SystemExit as exc:
    assert exc.code == 0
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, result.stderr
    assert "--phoenix" in result.stdout
    assert "--logfire" not in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_case_lifecycle_preserves_scores_links_and_span_tree(captured):
    pytest.importorskip("pydantic_evals")
    from pydantic_evals import Case, Dataset
    from pydantic_evals.evaluators import Evaluator, HasMatchingSpan

    from abaqus_sim_agent.evaluation.telemetry import PhoenixCaseLifecycle

    class Score(Evaluator):
        def evaluate(self, ctx):
            return {"correct": ctx.output == ctx.inputs}

    async def task(value):
        with telemetry.span("fixture.task"):
            await asyncio.sleep(0)
            return value

    report = await Dataset(
        name="paired",
        cases=[Case(name="a", inputs="A"), Case(name="b", inputs="B")],
        evaluators=[Score(), HasMatchingSpan(query={"name_contains": "fixture.task"})],
    ).evaluate(
        task,
        lifecycle=PhoenixCaseLifecycle,
        repeat=2,
        max_concurrency=2,
        progress=False,
    )
    assert len(report.cases) == 4
    assert len({case.trace_id for case in report.cases}) == 4
    assert all(case.span_id for case in report.cases)
    assert all(
        all(result.value for result in case.assertions.values())
        for case in report.cases
    )
    spans = finished(captured[0])
    by_id = {f"{s.context.span_id:016x}": s for s in spans}
    for case in report.cases:
        observed = by_id[case.span_id]
        assert observed.attributes["case_name"] == case.name
        assert observed.attributes["evaluation.status"] == "completed"
        children = [
            s
            for s in spans
            if s.parent and s.parent.span_id == observed.context.span_id
        ]
        assert len(children) == 1
        assert children[0].attributes["case_id"] == observed.attributes["case_id"]


@pytest.mark.asyncio
async def test_failed_case_keeps_trace_link(captured):
    pytest.importorskip("pydantic_evals")
    from pydantic_evals import Case, Dataset

    from abaqus_sim_agent.evaluation.telemetry import PhoenixCaseLifecycle

    async def fail(value):
        raise ValueError("injected failure")

    report = await Dataset(
        name="failure", cases=[Case(name="failed-case", inputs="x")]
    ).evaluate(
        fail,
        lifecycle=PhoenixCaseLifecycle,
        progress=False,
    )
    assert len(report.failures) == 1
    assert report.failures[0].trace_id
    assert report.failures[0].span_id
    observed = next(s for s in finished(captured[0]) if s.name == "evaluation.case")
    assert observed.attributes["evaluation.status"] == "failed"


@pytest.mark.asyncio
async def test_feedback_bridge_copies_existing_results(captured, monkeypatch):
    pytest.importorskip("pydantic_evals")
    from types import SimpleNamespace

    import phoenix.client
    from pydantic_evals import Case, Dataset
    from pydantic_evals.evaluators import Evaluator

    from abaqus_sim_agent.evaluation.telemetry import (
        PhoenixCaseLifecycle,
        publish_evaluation_feedback,
    )

    class Score(Evaluator):
        def evaluate(self, ctx):
            return {"passed": True, "value": 0.75, "label": "fixture"}

    captured_rows = []

    def save(**kwargs):
        captured_rows.extend(kwargs["span_annotations"])

    monkeypatch.setattr(
        phoenix.client,
        "Client",
        lambda **kw: SimpleNamespace(spans=SimpleNamespace(log_span_annotations=save)),
    )
    report = await Dataset(
        name="feedback", cases=[Case(name="one", inputs="x")], evaluators=[Score()]
    ).evaluate(
        lambda value: value,
        lifecycle=PhoenixCaseLifecycle,
        progress=False,
    )
    await publish_evaluation_feedback(report)
    assert {row["name"] for row in captured_rows} == {"passed", "value", "label"}
    assert {row["span_id"] for row in captured_rows} == {report.cases[0].span_id}
    assert report.cases[0].scores["value"].value == 0.75


def test_working_directory_phoenix_config_is_not_loaded(
    captured, monkeypatch, tmp_path
):
    from phoenix.otel.settings import get_env_phoenix_auth_header

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.phoenix").write_text(
        "PHOENIX_API_KEY=untrusted\nPHOENIX_COLLECTOR_ENDPOINT=https://remote.invalid\n"
    )
    assert get_env_phoenix_auth_header() is None
    assert telemetry.collector_endpoint() == "http://127.0.0.1:6006"


def test_full_logfire_sdk_is_not_installed():
    from importlib.util import find_spec

    assert find_spec("logfire") is None
