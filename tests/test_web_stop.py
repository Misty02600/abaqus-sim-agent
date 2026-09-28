from __future__ import annotations

import asyncio
import threading
import time
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig
from tests.support.web import (
    Client,
    api_catalog_dir,
    create,
    login,
    settled,
)
from tests.support.web_solver import _background_solver_app
from tests.support.web_streaming import poll


@pytest.mark.parametrize("tool_name", ["run_cae_script", "run_abaqus_python"])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_stop_current_task_cancels_foreground_runtime_and_waits_for_cleanup(
    tmp_path, tool_name, cleanup_failure
):
    started = threading.Event()
    stopped = threading.Event()

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

    def model_factory(**kwargs):
        def model(messages, info):
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name,
                        {"script_path": "script.py"},
                        tool_call_id=f"{tool_name}-1",
                    )
                ]
            )

        return FunctionModel(model), Client()

    app = create_app(
        ServerConfig(
            root=tmp_path / "web",
            api_catalog_dir=api_catalog_dir(tmp_path),
            stream_model=False,
        ),
        api_key=SecretStr("fixture"),
        model_factory=model_factory,
        runner_factory=lambda *args, **kwargs: Runner(),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id = create(client)
        workspace = app.state.controller.registry.load(run_id).request.workspace
        (workspace / "script.py").write_text("print('fixture')\n", encoding="utf-8")
        response = client.post(
            f"/api/v1/runs/{run_id}/start",
            json={"command_id": str(uuid4())},
        )
        assert response.status_code == 202, response.text
        assert started.wait(4)
        active = poll(
            client,
            run_id,
            lambda value: (
                value["can_stop"] and value["view"]["phase"] in {"cae", "extract"}
            ),
        )

        stale = client.post(
            f"/api/v1/runs/{run_id}/stop",
            json={
                "command_id": str(uuid4()),
                "expected_operation_id": uuid4().hex,
            },
        )
        assert stale.status_code == 409, stale.text
        assert not stopped.is_set()

        stopped_response = client.post(
            f"/api/v1/runs/{run_id}/stop",
            json={
                "command_id": str(uuid4()),
                "expected_operation_id": active["active_operation_id"],
            },
        )
        assert stopped_response.status_code == 202, stopped_response.text
        result = settled(client, run_id, status="interrupted")

        assert stopped.is_set()
        assert result["view"]["outcome"] is None
        if cleanup_failure:
            assert result["view"]["phase"] != "stopped"
        else:
            assert result["view"]["phase"] == "stopped"
            assert result["can_continue"]
            assert result["can_message"]
        [invocation] = app.state.controller.invocations.list(run_id)
        assert invocation.status == ("interrupted" if cleanup_failure else "stopped")
        if cleanup_failure:
            assert "fixture cleanup failed" in (invocation.last_error or "")


def test_stop_current_task_keeps_background_solver_running(tmp_path):
    app, release, runners, _, _ = _background_solver_app(tmp_path)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            run_id = create(client)
            response = client.post(
                f"/api/v1/runs/{run_id}/start",
                json={"command_id": str(uuid4())},
            )
            assert response.status_code == 202, response.text
            active = poll(
                client,
                run_id,
                lambda value: bool(
                    value["can_stop"]
                    and value["can_cancel_tool_execution"]
                    and value["tool_execution"]
                    and value["tool_execution"]["status"] == "running"
                ),
            )
            execution_id = active["tool_execution"]["execution_id"]

            stopped_response = client.post(
                f"/api/v1/runs/{run_id}/stop",
                json={
                    "command_id": str(uuid4()),
                    "expected_operation_id": active["active_operation_id"],
                },
            )
            assert stopped_response.status_code == 202, stopped_response.text
            stopped_snapshot = settled(client, run_id, status="interrupted")
            assert stopped_snapshot["view"]["phase"] == "stopped"
            assert stopped_snapshot["tool_execution"]["status"] == "running"
            assert stopped_snapshot["tool_execution"]["cancel_requested"] is False
            assert stopped_snapshot["can_cancel_tool_execution"] is True
            assert stopped_snapshot["can_continue"] is False
            assert stopped_snapshot["can_message"] is False
            assert "Solver" in stopped_snapshot["message_block_reason"]

            release.set()
            terminal = None
            for _ in range(200):
                candidate = client.get(
                    f"/api/v1/runs/{run_id}/tool-executions/{execution_id}"
                ).json()
                if candidate["status"] == "completed":
                    terminal = candidate
                    break
                time.sleep(0.03)
            assert terminal is not None
            after = client.get(f"/api/v1/runs/{run_id}").json()
            assert after["view"]["status"] == "interrupted"
            assert after["can_continue"]
            assert len(runners) == 1
            assert runners[0].calls == 1

            continued = client.post(
                f"/api/v1/runs/{run_id}/continue",
                json={"command_id": str(uuid4())},
            )
            assert continued.status_code == 202, continued.text
            done = settled(client, run_id, status="completed")
            assert done["view"]["outcome"]["summary"] == (
                "Solver completed and result was consumed."
            )
            assert sum(runner.calls for runner in runners) == 1
    finally:
        release.set()


def test_cancel_background_solver_after_task_stop_allows_controlled_continue(tmp_path):
    app, release, runners, _, _ = _background_solver_app(tmp_path)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            run_id = create(client)
            response = client.post(
                f"/api/v1/runs/{run_id}/start",
                json={"command_id": str(uuid4())},
            )
            assert response.status_code == 202, response.text
            active = poll(
                client,
                run_id,
                lambda value: bool(
                    value["can_stop"]
                    and value["can_cancel_tool_execution"]
                    and value["tool_execution"]
                ),
            )
            execution_id = active["tool_execution"]["execution_id"]
            stopped_response = client.post(
                f"/api/v1/runs/{run_id}/stop",
                json={
                    "command_id": str(uuid4()),
                    "expected_operation_id": active["active_operation_id"],
                },
            )
            assert stopped_response.status_code == 202, stopped_response.text
            settled(client, run_id, status="interrupted")

            cancelled = client.post(
                f"/api/v1/runs/{run_id}/tool-executions/{execution_id}/cancel",
                json={"command_id": str(uuid4())},
            )
            assert cancelled.status_code == 202, cancelled.text
            terminal = None
            for _ in range(200):
                candidate = client.get(
                    f"/api/v1/runs/{run_id}/tool-executions/{execution_id}"
                ).json()
                if candidate["status"] == "cancelled":
                    terminal = candidate
                    break
                time.sleep(0.03)
            assert terminal is not None
            ready = client.get(f"/api/v1/runs/{run_id}").json()
            assert ready["can_continue"]

            continued = client.post(
                f"/api/v1/runs/{run_id}/continue",
                json={"command_id": str(uuid4())},
            )
            assert continued.status_code == 202, continued.text
            done = settled(client, run_id, status="failed")
            assert done["view"]["outcome"]["failure_stage"] == "solver_cancelled"
            assert sum(runner.calls for runner in runners) == 1
    finally:
        release.set()


def test_stopped_solver_boundary_rejects_unrecorded_workspace_change(tmp_path):
    app, release, _, _, _ = _background_solver_app(tmp_path)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            run_id = create(client)
            response = client.post(
                f"/api/v1/runs/{run_id}/start",
                json={"command_id": str(uuid4())},
            )
            assert response.status_code == 202, response.text
            active = poll(
                client,
                run_id,
                lambda value: bool(
                    value["can_stop"]
                    and value["can_cancel_tool_execution"]
                    and value["tool_execution"]
                ),
            )
            execution_id = active["tool_execution"]["execution_id"]
            stopped_response = client.post(
                f"/api/v1/runs/{run_id}/stop",
                json={
                    "command_id": str(uuid4()),
                    "expected_operation_id": active["active_operation_id"],
                },
            )
            assert stopped_response.status_code == 202, stopped_response.text
            settled(client, run_id, status="interrupted")
            release.set()
            for _ in range(200):
                terminal = client.get(
                    f"/api/v1/runs/{run_id}/tool-executions/{execution_id}"
                ).json()
                if terminal["status"] == "completed":
                    break
                time.sleep(0.03)
            assert terminal["status"] == "completed"

            workspace = app.state.controller.registry.load(run_id).request.workspace
            (workspace / "manual.txt").write_text("outside change", encoding="utf-8")
            continued = client.post(
                f"/api/v1/runs/{run_id}/continue",
                json={"command_id": str(uuid4())},
            )
            assert continued.status_code == 409, continued.text
            snapshot = client.get(f"/api/v1/runs/{run_id}").json()
            assert snapshot["view"]["status"] == "interrupted"
            assert snapshot["active"] is False
    finally:
        release.set()
