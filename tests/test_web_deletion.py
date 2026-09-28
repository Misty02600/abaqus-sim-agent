import asyncio
import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.coding.model import DeepSeekGateway
from abaqus_sim_agent.coding.options import RunOptions
from abaqus_sim_agent.coding.run_lock import CodingRunFileLock
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig
from tests.support.web import (
    Client,
    answer_body,
    configured_app,
    create,
    create_selected,
    login,
    settled,
    start,
)
from tests.support.web_solver import _background_solver_app
from tests.support.web_streaming import Flow, launch, poll, streaming_app


def delete(client, run_id):
    return client.request(
        "DELETE", f"/api/v1/runs/{run_id}", json={"command_id": str(uuid4())}
    )


def test_delete_registered_preserves_workspace_and_cannot_resurrect(tmp_path):
    app, _ = configured_app(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    key = uuid4()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        created = create_selected(client, folder, key=key)
        assert created.status_code == 201, created.text
        rid = created.json()["view"]["run_id"]
        assert app.state.controller.registry.load(rid).workspace_kind == "selected"
        (folder / "keep.txt").write_text("user project", encoding="utf-8")
        result = delete(client, rid)
        assert result.status_code == 200, result.text
        assert result.json()["deleted"]
        assert (folder / "keep.txt").read_text(encoding="utf-8") == "user project"
        assert not (tmp_path / "web/states" / rid).exists()
        assert not (tmp_path / "web/registry" / rid).exists()
        assert client.get("/api/v1/runs").json()["runs"] == []
        assert client.get(f"/api/v1/runs/{rid}").status_code == 410
        assert delete(client, rid).status_code == 200
        assert (
            client.post(
                "/api/v1/runs",
                json={
                    "idempotency_key": str(key),
                    "goal": "new",
                    "workspace": str(folder),
                },
            ).status_code
            == 410
        )
    restarted, _ = configured_app(tmp_path)
    with TestClient(restarted, base_url="http://127.0.0.1") as client:
        login(client)
        assert delete(client, rid).status_code == 200
        assert client.get("/api/v1/runs").json()["runs"] == []


@pytest.mark.parametrize("complete", [False, True])
def test_delete_waiting_or_complete_and_keep_other_conversations(tmp_path, complete):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid, other = create(client), create(client)
        workspace = app.state.controller.registry.load(rid).request.workspace
        waiting = start(client, rid)
        if complete:
            client.post(f"/api/v1/runs/{rid}/answers", json=answer_body(waiting))
            settled(client, rid, status="completed")
        assert delete(client, rid).status_code == 200
        assert [
            r["view"]["run_id"] for r in client.get("/api/v1/runs").json()["runs"]
        ] == [other]
        assert (tmp_path / "web/states" / other).exists()
        if complete:
            assert (workspace / "out.txt").read_text(encoding="utf-8") == "result"


def test_delete_active_model_waits_for_stream_to_close(tmp_path):
    flow = Flow()
    app = streaming_app(tmp_path, flow)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            rid = launch(client)
            poll(
                client,
                rid,
                lambda s: bool(
                    s["can_stop"]
                    and s["view"].get("live_reply")
                    and s["view"]["live_reply"]["text"]
                ),
            )
            response = delete(client, rid)
            assert response.status_code == 200, response.text
            assert flow.closed.is_set()
            assert not (tmp_path / "web/states" / rid).exists()
            assert app.state.controller._active is None
    finally:
        flow.next.set()
        flow.finish.set()


def test_delete_active_solver_waits_for_actual_cleanup(tmp_path, monkeypatch):
    app, release, runners, _, _ = _background_solver_app(tmp_path)
    entered = threading.Event()
    cleanup = threading.Event()
    finished = threading.Event()

    async def controlled_solver(self, *args, **kwargs):
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            entered.set()
            await asyncio.to_thread(cleanup.wait, 5)
            finished.set()
            raise

    from tests.support.web_solver import _DeferredSuccessfulSolver

    monkeypatch.setattr(_DeferredSuccessfulSolver, "run_solver_job", controlled_solver)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            rid = create(client)
            client.post(f"/api/v1/runs/{rid}/start", json={"command_id": str(uuid4())})
            poll(client, rid, lambda s: s["can_cancel_tool_execution"])
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(delete, client, rid)
                assert entered.wait(3)
                try:
                    assert not pending.done()
                    assert (tmp_path / "web/states" / rid / "request.json").exists()
                    assert (tmp_path / "web/registry" / rid).exists()
                finally:
                    cleanup.set()
                response = pending.result(timeout=5)
            assert response.status_code == 200, response.text
            assert finished.is_set()
            assert not (tmp_path / "web/states" / rid).exists()
            assert len(runners) == 1
    finally:
        cleanup.set()
        release.set()


@pytest.mark.parametrize("tool_name", ["run_cae_script", "run_abaqus_python"])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_delete_active_vendor_stage_cancels_before_removing_state(
    tmp_path, tool_name, cleanup_failure
):
    started, stopped = threading.Event(), threading.Event()

    class Runner:
        async def run_cae_script(self, *args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
                if cleanup_failure:
                    raise RuntimeError("fixture cleanup failed")

        run_abaqus_python = run_cae_script

    def factory(**kwargs):
        def model(messages, info):
            return ModelResponse(
                parts=[ToolCallPart(tool_name, {"script_path": "script.py"})]
            )

        return FunctionModel(model), Client()

    app = create_app(
        ServerConfig(
            root=tmp_path / "web",
            overrides=RunOptions(),
            stream_model=False,
        ),
        api_keys={DeepSeekGateway.OFFICIAL: SecretStr("fixture")},
        model_factory=factory,
        runner_factory=lambda *a, **kw: Runner(),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        workspace = app.state.controller.registry.load(rid).request.workspace
        (workspace / "script.py").write_text("print('fixture')", encoding="utf-8")
        client.post(f"/api/v1/runs/{rid}/start", json={"command_id": str(uuid4())})
        assert started.wait(4)
        result = delete(client, rid)
        if cleanup_failure:
            assert result.status_code == 409, result.text
            assert (tmp_path / "web/states" / rid).exists()
        else:
            assert result.status_code == 200, result.text
            assert stopped.is_set()
            assert (workspace / "script.py").exists()
            assert not (tmp_path / "web/states" / rid).exists()
    if cleanup_failure:
        restarted, _ = configured_app(tmp_path)
        with TestClient(restarted, base_url="http://127.0.0.1") as client:
            login(client)
            assert delete(client, rid).status_code == 409
            assert (tmp_path / "web/states" / rid).exists()


def test_delete_checks_auth_csrf_and_external_state_lock(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert delete(client, "web-" + uuid4().hex).status_code == 401
        login(client)
        rid = create(client)
        assert (
            client.request(
                "DELETE",
                f"/api/v1/runs/{rid}",
                json={"command_id": str(uuid4())},
                headers={"x-csrf-token": "wrong"},
            ).status_code
            == 403
        )
        with CodingRunFileLock(app.state.controller.registry.state_dir(rid)):
            assert delete(client, rid).status_code == 409
        assert client.get(f"/api/v1/runs/{rid}").status_code == 200


def test_delete_retries_partial_file_cleanup(tmp_path, monkeypatch):
    from abaqus_sim_agent.web.deletion import RunDeletionStore

    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        state = app.state.controller.registry.state_dir(rid)
        (state / "a.txt").write_text("record", encoding="utf-8")
        original = RunDeletionStore._remove
        failed = False

        def fail_once(target, parent):
            nonlocal failed
            if target.name == "a.txt" and not failed:
                failed = True
                raise PermissionError("injected busy file")
            return original(target, parent)

        monkeypatch.setattr(RunDeletionStore, "_remove", staticmethod(fail_once))
        assert delete(client, rid).status_code == 409
        assert app.state.controller.deletions.load(rid).state == "purging"
        assert client.get("/api/v1/runs").json()["unavailable"][0]["can_retry_delete"]
        assert delete(client, rid).status_code == 200
        assert not state.exists()


def test_delete_does_not_follow_link_inside_state(tmp_path):
    app, _ = configured_app(tmp_path)
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        state = app.state.controller.registry.state_dir(rid)
        try:
            (state / "linked").symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation unavailable")
        assert delete(client, rid).status_code == 400
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"
        assert app.state.controller.deletions.load(rid) is None


def test_delete_retains_unknown_runtime_effect_records(tmp_path):
    from pydantic_ai_harness.step_persistence import ToolEffectRecord

    from abaqus_sim_agent.coding.resume import create_step_store

    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        start(client, rid)
        state = app.state.controller.registry.state_dir(rid)

        async def interrupted_effect():
            store = create_step_store(state)
            runs = await store.list_runs(conversation_id=rid)
            await store.record_tool_effect(
                ToolEffectRecord(
                    run_id=runs[-1].run_id,
                    tool_call_id="lost-runtime",
                    tool_name="run_cae_script",
                    status="started",
                )
            )

        asyncio.run(interrupted_effect())
        response = delete(client, rid)
        assert response.status_code == 409
        assert response.json()["code"] == "delete_unresolved"
        assert (state / "request.json").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Shell process-tree lifecycle")
@pytest.mark.parametrize("tool_name", ["run_command", "start_command"])
def test_delete_shell_terminates_parent_and_child_processes(tmp_path, tool_name):
    import ctypes
    from ctypes import wintypes

    from pydantic_ai.messages import ToolReturnPart

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL

    def factory(**kwargs):
        async def model(messages, info):
            if any(
                isinstance(p, ToolReturnPart) and p.tool_name == tool_name
                for m in messages
                for p in m.parts
            ):
                await asyncio.Event().wait()
            return ModelResponse(
                parts=[
                    ToolCallPart(tool_name, {"command": "python process_fixture.py"})
                ]
            )

        return FunctionModel(model), Client()

    app = create_app(
        ServerConfig(
            root=tmp_path / "web",
            overrides=RunOptions(),
            stream_model=False,
        ),
        api_keys={DeepSeekGateway.OFFICIAL: SecretStr("fixture")},
        model_factory=factory,
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        workspace = app.state.controller.registry.load(rid).request.workspace
        (workspace / "process_fixture.py").write_text(
            "import json, os, pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "pathlib.Path('fixture_pids.json').write_text(json.dumps([os.getpid(), child.pid]))\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        client.post(f"/api/v1/runs/{rid}/start", json={"command_id": str(uuid4())})
        pid_file = workspace / "fixture_pids.json"
        for _ in range(100):
            if pid_file.exists():
                break
            threading.Event().wait(0.05)
        assert pid_file.exists()
        pids = json.loads(pid_file.read_text(encoding="utf-8"))
        handles = [kernel.OpenProcess(0x00100000, False, pid) for pid in pids]
        assert all(handles)
        try:
            response = delete(client, rid)
            exited = [
                kernel.WaitForSingleObject(handle, 1000) == 0 for handle in handles
            ]
        finally:
            for pid, handle in zip(pids, handles, strict=True):
                if kernel.WaitForSingleObject(handle, 0) != 0:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        check=False,
                        capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                kernel.CloseHandle(handle)
        assert response.status_code == 200, (response.text, exited)
        assert all(exited), "A Shell descendant survived deletion"
        assert (workspace / "process_fixture.py").exists()
