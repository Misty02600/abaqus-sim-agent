from __future__ import annotations

import asyncio
import hashlib
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from abaqus_sim_agent.execution import (
    AbaqusExecutionResult,
)
from abaqus_sim_agent.web.contracts import ToolExecution
from tests.support.web import (
    answer_body,
    configured_app,
    create,
    login,
    settled,
    start,
)
from tests.support.web_solver import (
    _background_solver_app,
    _result,
)


def _complete_run(client: TestClient) -> tuple[str, dict]:
    run_id = create(client)
    waiting = start(client, run_id)
    response = client.post(f"/api/v1/runs/{run_id}/answers", json=answer_body(waiting))
    assert response.status_code == 202, response.text
    return run_id, settled(client, run_id, status="completed")


def _selected_solver_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "selected-project"
    workspace.mkdir(exist_ok=True)
    (workspace / "demo.inp").write_text("*HEADING\nfixture\n", encoding="ascii")
    return workspace


class _SuccessfulSolver:
    def __init__(self, result: AbaqusExecutionResult) -> None:
        self.result = result
        self.process_started = None

    def set_solver_process_started_callback(self, callback) -> None:
        self.process_started = callback

    async def run_solver_job(self, *args, **kwargs) -> AbaqusExecutionResult:
        if self.process_started is not None:
            self.process_started(4242)
        return self.result


class _BlockingSolver:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()

    async def run_solver_job(self, *args, **kwargs) -> AbaqusExecutionResult:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


def test_tool_execution_api_persists_terminal_solver_result(tmp_path: Path) -> None:
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id, _ = _complete_run(client)
        controller = app.state.controller
        invocation = controller.invocations.list(run_id)[-1]
        result = _result(tmp_path, controller.registry.load(run_id).request.workspace)

        returned = asyncio.run(
            controller._run_solver_execution(
                run_id,
                invocation.invocation_id,
                _SuccessfulSolver(result),
                job_name="demo",
                input_file="demo.inp",
                timeout_seconds=60.0,
            )
        )
        assert returned == result

        listed = client.get(f"/api/v1/runs/{run_id}/tool-executions")
        assert listed.status_code == 200, listed.text
        [execution] = listed.json()["items"]
        assert execution["status"] == "completed"
        assert execution["job_name"] == "demo"
        assert execution["exit_code"] == 0
        assert execution["process_id"] == 4242
        assert execution["result_succeeded"] is True
        assert execution["result_stage_succeeded"] is True
        assert (
            execution["result_sha256"]
            == hashlib.sha256(result.model_dump_json().encode()).hexdigest()
        )
        assert execution["stdout_raw_path"] == str(result.stdout_raw_path)
        assert execution["stderr_raw_path"] == str(result.stderr_raw_path)

        detail = client.get(
            f"/api/v1/runs/{run_id}/tool-executions/{execution['execution_id']}"
        )
        assert detail.status_code == 200, detail.text
        assert detail.json() == execution

    app2, _ = configured_app(tmp_path)
    with TestClient(app2, base_url="http://127.0.0.1") as client:
        login(client)
        [restored] = client.get(f"/api/v1/runs/{run_id}/tool-executions").json()[
            "items"
        ]
        assert restored["status"] == "completed"
        assert restored["result_sha256"] == execution["result_sha256"]
        # 重启后没有活动 Future，确定性覆盖从持久化结果读取已完成执行的路径。
        controller = app2.state.controller
        assert controller._tool_tasks == {}
        assert (
            asyncio.run(
                controller._wait_solver_execution(run_id, restored["execution_id"])
            )
            == result
        )


def test_running_solver_is_visible_and_can_be_cancelled_without_model_stop(
    tmp_path: Path,
) -> None:
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id, _ = _complete_run(client)
        controller = app.state.controller
        invocation = controller.invocations.list(run_id)[-1]
        runner = _BlockingSolver()
        errors: list[BaseException] = []

        def execute() -> None:
            try:
                asyncio.run(
                    controller._run_solver_execution(
                        run_id,
                        invocation.invocation_id,
                        runner,
                        job_name="long_job",
                        input_file="long_job.inp",
                        timeout_seconds=600.0,
                    )
                )
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=execute, daemon=True)
        thread.start()
        assert runner.started.wait(3)

        [running] = client.get(f"/api/v1/runs/{run_id}/tool-executions").json()["items"]
        assert running["status"] == "running"
        assert running["elapsed_seconds"] >= 0
        snapshot = client.get(f"/api/v1/runs/{run_id}").json()
        assert snapshot["tool_execution"]["execution_id"] == running["execution_id"]
        assert snapshot["can_cancel_tool_execution"] is True
        assert snapshot["can_stop"] is False

        cancelled = client.post(
            f"/api/v1/runs/{run_id}/tool-executions/{running['execution_id']}/cancel",
            json={"command_id": str(uuid4())},
        )
        assert cancelled.status_code == 202, cancelled.text
        assert cancelled.json()["cancel_requested"] is True

        thread.join(5)
        assert not thread.is_alive()
        assert runner.cancelled.wait(2)
        assert len(errors) == 1
        assert isinstance(errors[0], asyncio.CancelledError)

        terminal = None
        for _ in range(100):
            candidate = client.get(
                f"/api/v1/runs/{run_id}/tool-executions/{running['execution_id']}"
            ).json()
            if candidate["status"] == "cancelled":
                terminal = candidate
                break
            threading.Event().wait(0.02)
        assert terminal is not None
        assert terminal["status"] == "cancelled"
        assert terminal["cancel_requested"] is True
        assert terminal["finished_at"] is not None


def test_restart_marks_unresolved_solver_for_reconcile_and_blocks_new_message(
    tmp_path: Path,
) -> None:
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id, completed = _complete_run(client)
        controller = app.state.controller
        invocation = controller.invocations.list(run_id)[-1]
        now = datetime.now(UTC)
        controller.tool_executions.create(
            ToolExecution(
                execution_id=uuid4().hex,
                thread_id=run_id,
                invocation_id=invocation.invocation_id,
                job_name="interrupted_job",
                input_file="interrupted_job.inp",
                timeout_seconds=600.0,
                created_at=now,
                updated_at=now,
                started_at=now,
                status="running",
            )
        )
        assert completed["can_message"] is True

    app2, _ = configured_app(tmp_path)
    with TestClient(app2, base_url="http://127.0.0.1") as client:
        login(client)
        snapshot = client.get(f"/api/v1/runs/{run_id}").json()
        assert snapshot["tool_execution"]["status"] == "needs_reconcile"
        assert snapshot["can_cancel_tool_execution"] is False
        assert snapshot["can_message"] is False
        assert "Solver" in snapshot["message_block_reason"]

        blocked = client.post(
            f"/api/v1/runs/{run_id}/messages",
            json={
                "command_id": str(uuid4()),
                "text": "continue with another change",
                "expected_task_revision": snapshot["view"]["task_revision"],
                "expected_amendment_sequence": snapshot["view"]["amendment_sequence"],
            },
        )
        assert blocked.status_code == 409, blocked.text


def test_background_solver_completion_returns_in_same_invocation(
    tmp_path: Path,
) -> None:
    app, release, runners, model_calls, _solver_call_id = _background_solver_app(
        tmp_path
    )
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            workspace = _selected_solver_workspace(tmp_path)
            created = client.post(
                "/api/v1/runs",
                json={
                    "idempotency_key": str(uuid4()),
                    "goal": "Run the existing demo Solver job and report its result.",
                    "workspace": str(workspace),
                },
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["view"]["run_id"]
            response = client.post(
                f"/api/v1/runs/{run_id}/start",
                json={"command_id": str(uuid4())},
            )
            assert response.status_code == 202, response.text

            for _ in range(200):
                if runners and runners[0].started.is_set():
                    break
                threading.Event().wait(0.03)
            assert runners
            assert runners[0].started.is_set()

            waiting = None
            for _ in range(200):
                candidate = client.get(f"/api/v1/runs/{run_id}").json()
                if (
                    candidate["tool_execution"] is not None
                    and candidate["tool_execution"]["status"] == "running"
                ):
                    waiting = candidate
                    break
                threading.Event().wait(0.03)
            assert waiting is not None
            assert waiting["tool_execution"]["status"] == "running"
            assert waiting["can_continue"] is False

            [invocation_before] = app.state.controller.invocations.list(run_id)
            [execution_before] = app.state.controller.tool_executions.list(run_id)
            assert execution_before.invocation_id == invocation_before.invocation_id
            assert execution_before.tool_call_id is None
            assert execution_before.tool_name == "solver_job"

            release.set()
            done = settled(client, run_id, status="completed")
            assert done["view"]["outcome"]["summary"] == (
                "Solver completed and result was consumed."
            )

            [invocation_after] = app.state.controller.invocations.list(run_id)
            [execution_after] = app.state.controller.tool_executions.list(run_id)
            assert invocation_after.invocation_id == invocation_before.invocation_id
            assert invocation_after.status == "completed"
            assert invocation_after.resume_count == 0
            assert execution_after.status == "completed"
            assert execution_after.tool_call_id is None
            assert sum(runner.calls for runner in runners) == 1
            assert len(model_calls) == 3
    finally:
        release.set()


def test_background_solver_cancel_returns_failure_to_wait_call(
    tmp_path: Path,
) -> None:
    app, release, runners, model_calls, _solver_call_id = _background_solver_app(
        tmp_path
    )
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            workspace = _selected_solver_workspace(tmp_path)
            created = client.post(
                "/api/v1/runs",
                json={
                    "idempotency_key": str(uuid4()),
                    "goal": "Run the existing demo Solver job and report its result.",
                    "workspace": str(workspace),
                },
            )
            assert created.status_code == 201, created.text
            run_id = created.json()["view"]["run_id"]
            response = client.post(
                f"/api/v1/runs/{run_id}/start",
                json={"command_id": str(uuid4())},
            )
            assert response.status_code == 202, response.text

            waiting = None
            for _ in range(200):
                candidate = client.get(f"/api/v1/runs/{run_id}").json()
                if (
                    candidate["tool_execution"] is not None
                    and candidate["can_cancel_tool_execution"]
                ):
                    waiting = candidate
                    break
                threading.Event().wait(0.03)
            assert waiting is not None
            execution_id = waiting["tool_execution"]["execution_id"]
            [invocation_before] = app.state.controller.invocations.list(run_id)

            cancelled = client.post(
                f"/api/v1/runs/{run_id}/tool-executions/{execution_id}/cancel",
                json={"command_id": str(uuid4())},
            )
            assert cancelled.status_code == 202, cancelled.text

            terminal_execution = None
            for _ in range(200):
                candidate = client.get(
                    f"/api/v1/runs/{run_id}/tool-executions/{execution_id}"
                ).json()
                if candidate["status"] == "cancelled":
                    terminal_execution = candidate
                    break
                threading.Event().wait(0.03)
            assert terminal_execution is not None
            release.set()

            done = settled(client, run_id, status="failed")
            assert done["view"]["outcome"]["failure_stage"] == "solver_cancelled"
            [invocation_after] = app.state.controller.invocations.list(run_id)
            [execution_after] = app.state.controller.tool_executions.list(run_id)
            assert invocation_after.invocation_id == invocation_before.invocation_id
            assert invocation_after.status == "failed"
            assert invocation_after.resume_count == 0
            assert execution_after.status == "cancelled"
            assert execution_after.tool_call_id is None
            assert sum(runner.calls for runner in runners) == 1
            assert len(model_calls) == 3
    finally:
        release.set()
