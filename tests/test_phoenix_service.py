import asyncio
import importlib
import io
import os
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from abaqus_sim_agent import telemetry
from abaqus_sim_agent.evaluation import _phoenix_server_host as host
from abaqus_sim_agent.evaluation import phoenix_service as service


@pytest.fixture
def runtime(monkeypatch):
    events = []
    processes = []
    options = {"ready": True, "exit_code": None}
    monkeypatch.setattr(service, "_check_port_available", lambda: events.append("port"))
    monkeypatch.setattr(service.shutil, "which", lambda name: "C:/tools/uvx.exe")

    class Process:
        pid = 12345

        def __init__(self, command, **kwargs):
            events.append("start")
            self.command = command
            self.kwargs = kwargs
            self.stdin = io.BytesIO()
            self.stdout = io.BytesIO(
                b"startup output\n"
                + (command[-1].encode() + b"\n" if options["ready"] else b"")
            )
            self.returncode = options["exit_code"]
            processes.append(self)

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            assert self.stdin.closed
            events.append("stop")
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

    @contextmanager
    def tracing(enabled, *, environment):
        events.append(f"tracing:{enabled}")
        if enabled:
            assert (
                os.environ["PHOENIX_COLLECTOR_ENDPOINT"] == telemetry.DEFAULT_ENDPOINT
            )
        try:
            yield
        finally:
            events.append("flush-and-close-tracing")

    monkeypatch.setattr(service.subprocess, "Popen", Process)
    monkeypatch.setattr(telemetry, "telemetry_session", tracing)
    return SimpleNamespace(events=events, processes=processes, options=options)


def test_disabled_does_not_start_server(runtime):
    with service.evaluation_telemetry_session(False):
        runtime.events.append("body")
    assert runtime.events == ["tracing:False", "body", "flush-and-close-tracing"]


def test_start_flush_stop_order_and_default_environment(runtime, monkeypatch):
    monkeypatch.setenv("PHOENIX_WORKING_DIR", "D:/old-validation-data")
    monkeypatch.setenv("PHOENIX_SQL_DATABASE_URL", "postgresql://not-used")
    monkeypatch.setenv("PHOENIX_PORT", "16007")
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:16007")
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-inherit")
    with service.evaluation_telemetry_session(True):
        runtime.events.append("body")
        assert os.environ["PHOENIX_COLLECTOR_ENDPOINT"] == telemetry.DEFAULT_ENDPOINT
    assert runtime.events == [
        "port",
        "start",
        "tracing:True",
        "body",
        "flush-and-close-tracing",
        "stop",
    ]
    assert os.environ["PHOENIX_COLLECTOR_ENDPOINT"] == "http://127.0.0.1:16007"
    child = runtime.processes[0]
    env = child.kwargs["env"]
    assert (
        not {
            "PHOENIX_WORKING_DIR",
            "PHOENIX_SQL_DATABASE_URL",
            "PHOENIX_PORT",
            "OPENAI_API_KEY",
        }
        & env.keys()
    )
    assert env["PHOENIX_HOST"] == "127.0.0.1"
    assert "arize-phoenix==20.16.0" in child.command
    if os.name == "nt":
        assert child.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize(
    "error",
    [RuntimeError("task failed"), KeyboardInterrupt(), asyncio.CancelledError()],
)
def test_failure_and_cancellation_close_owned_server(runtime, error):
    def run():
        with service.evaluation_telemetry_session(True):
            raise error

    with pytest.raises(type(error)) as captured:
        run()
    assert captured.value is error
    assert runtime.events[-2:] == ["flush-and-close-tracing", "stop"]
    assert "PHOENIX_COLLECTOR_ENDPOINT" not in os.environ


def test_start_failure_does_not_run_evaluation(runtime):
    runtime.options.update(ready=False, exit_code=1)
    with pytest.raises(RuntimeError, match="no evaluation was started"):
        with service.phoenix_server():
            pytest.fail("task started after server failure")
    assert runtime.events == ["port", "start", "stop"]


def test_start_timeout_closes_child_without_running_task(runtime, monkeypatch):
    runtime.options["ready"] = False
    monkeypatch.setattr(service, "STARTUP_TIMEOUT_SECONDS", 0)
    with pytest.raises(RuntimeError, match="no evaluation was started"):
        with service.phoenix_server():
            pytest.fail("task started before readiness")
    assert runtime.events == ["port", "start", "stop"]


def test_service_exits_during_evaluation_preserves_local_result(runtime, tmp_path):
    result = tmp_path / "result.json"

    def run():
        with service.evaluation_telemetry_session(True):
            result.write_text('{"completed": true}')
            runtime.processes[0].returncode = 9

    with pytest.raises(RuntimeError, match="local results are retained"):
        run()
    assert result.read_text() == '{"completed": true}'


def test_occupied_port_never_starts_or_stops_existing_service(monkeypatch):
    class Occupied:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            assert address == ("127.0.0.1", 6006)
            raise OSError("occupied")

    monkeypatch.setattr(service.socket, "socket", lambda *args: Occupied())
    monkeypatch.setattr(
        service.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not launch")
    )
    with pytest.raises(RuntimeError, match="will not be reused or terminated"):
        with service.phoenix_server():
            pytest.fail("must not reuse existing service")


def test_helper_closes_app_without_deleting_persistent_database(tmp_path, monkeypatch):
    database = tmp_path / ".phoenix" / "phoenix.db"
    calls = []

    def launch_app(**kwargs):
        assert kwargs == {"host": "127.0.0.1", "use_temp_dir": False}
        database.parent.mkdir()
        database.write_bytes(b"existing persistent data")
        calls.append("launch")
        return object()

    backend = SimpleNamespace(
        launch_app=launch_app, close_app=lambda: calls.append("close")
    )
    app = SimpleNamespace()
    monkeypatch.setattr(
        host, "import_module", lambda name: backend if name == "phoenix" else app
    )
    monkeypatch.setattr(host.sys, "argv", ["host.py", "ready-fixture"])
    monkeypatch.setattr(host.sys, "stdin", SimpleNamespace(buffer=io.BytesIO()))
    host.main()
    assert calls == ["launch", "close"]
    assert database.read_bytes() == b"existing persistent data"
    assert app.GrpcServer is host._without_grpc


@pytest.mark.parametrize(
    ("module_name", "mode", "function", "asynchronous"),
    [
        ("public_mechanics_cli", "report", "suite.report", False),
        ("task_baseline_runner", "summarize", "summarize_batch", False),
        ("task_baseline_pilot", "verify", "verify_pilot", True),
        ("simple_modeling_batch", "verify", "verify_batch", True),
    ],
)
def test_evaluation_entrypoints_use_managed_session(
    module_name, mode, function, asynchronous, tmp_path, monkeypatch
):
    import sys

    module = importlib.import_module(f"abaqus_sim_agent.evaluation.{module_name}")
    events = []

    @contextmanager
    def session(enabled):
        assert enabled
        events.append("start")
        yield
        events.append("close")

    def work(*args, **kwargs):
        assert events == ["start"]
        events.append("work")
        return {}

    async def async_work(*args, **kwargs):
        return work(*args, **kwargs)

    owner = module.suite if function.startswith("suite.") else module
    monkeypatch.setattr(
        owner, function.split(".")[-1], async_work if asynchronous else work
    )
    monkeypatch.setattr(module, "evaluation_telemetry_session", session)
    args = [module_name, mode]
    if module_name == "public_mechanics_cli":
        args.append("--root")
    args.extend([str(tmp_path), "--phoenix"])
    monkeypatch.setattr(sys, "argv", args)
    module.main()
    assert events == ["start", "work", "close"]


def test_shutdown_failure_does_not_mask_original_exception(runtime, monkeypatch):
    original = ValueError("original evaluation failure")

    def broken_cleanup(process):
        raise OSError("cleanup failed")

    monkeypatch.setattr(service, "_stop", broken_cleanup)

    def run():
        with service.phoenix_server():
            raise original

    with pytest.raises(ValueError, match="original evaluation failure") as captured:
        run()
    assert captured.value is original


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree termination")
def test_forced_shutdown_targets_only_owned_pid(monkeypatch):
    calls = []

    class Process:
        pid = 4242
        stdin = io.BytesIO()
        attempts = 0

        def wait(self, timeout):
            self.attempts += 1
            if self.attempts == 1:
                raise subprocess.TimeoutExpired("owned server", timeout)
            return 0

    monkeypatch.setattr(
        service.subprocess, "run", lambda command, **kw: calls.append((command, kw))
    )
    child = Process()
    service._stop(child)
    assert child.stdin.closed
    assert calls[0][0] == ["taskkill", "/PID", "4242", "/T", "/F"]
    assert calls[0][1]["creationflags"] & subprocess.CREATE_NO_WINDOW
