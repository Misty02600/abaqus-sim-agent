import asyncio
import hashlib
import json
import threading
import time
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig
from abaqus_sim_agent.web.events import ViewEvents


class NoProcess:
    async def run(self, *args, **kwargs):
        raise AssertionError("Web contract test must never execute a process")


class Client:
    async def close(self):
        pass


def api_catalog_dir(tmp_path):
    path = tmp_path / "api-catalog"
    if not path.exists():
        ApiCatalog([], runtime_release="2024").write(path)
    return path


def configured_app(tmp_path, *, fail=False, block=None, crash_once=None):
    calls = []

    def factory(**kwargs):
        def model(messages, info):
            calls.append([t.name for t in info.function_tools])
            returned = [
                p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)
            ]
            if block is not None:
                assert block.wait(8)
            if not any(p.tool_name == "ask_user" for p in returned):
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "ask_user",
                            {"question": "Plate thickness?"},
                            tool_call_id="q1",
                        )
                    ]
                )
            if crash_once is not None and not crash_once[0]:
                crash_once[0] = True
                raise ConnectionError("fixture provider interruption")
            if not any(p.tool_name == "write_file" for p in returned):
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "write_file",
                            {"path": "out.txt", "content": "result"},
                            tool_call_id="w1",
                        )
                    ]
                )
            name = next(
                t.name
                for t in info.output_tools
                if t.name.endswith("ReadyForVerificationDecision")
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        name,
                        {
                            "status": "ready_for_verification",
                            "summary": "model claims complete",
                            "deliverables": [
                                {"relative_path": "missing.txt" if fail else "out.txt"}
                            ],
                        },
                        tool_call_id="final",
                    )
                ]
            )

        return FunctionModel(model), Client()

    config = ServerConfig(
        root=tmp_path / "web",
        api_catalog_dir=api_catalog_dir(tmp_path),
        stream_model=False,
        budget=CodingRunBudget(
            max_model_requests=8, max_abaqus_calls=0, max_solver_calls=0
        ),
    )
    app = create_app(
        config,
        api_key=SecretStr("fixture-key"),
        model_factory=factory,
        runner_factory=lambda workspace, state, **kw: Abaqus2024Runner(
            workspace, state, process_strategy=NoProcess()
        ),
    )
    return app, calls


def login(client):
    response = client.get("/api/v1/session")
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf_token"]


def create(client, *, key=None):
    key = key or uuid4()
    workspace = (
        client.app.state.controller.registry.root.parent
        / "selected-workspaces"
        / key.hex
    )
    workspace.mkdir(parents=True, exist_ok=True)
    response = client.post(
        "/api/v1/runs",
        json={
            "idempotency_key": str(key),
            "goal": "Write out.txt after asking for thickness",
            "workspace": str(workspace),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["view"]["run_id"]


def test_create_requires_a_selected_workspace(tmp_path) -> None:
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        response = client.post(
            "/api/v1/runs",
            json={"idempotency_key": str(uuid4()), "goal": "Create a run"},
        )

    assert response.status_code == 422
    assert app.state.controller.registry.ids() == []
    assert list((tmp_path / "web/states").iterdir()) == []
    assert calls == []


def test_create_run_persists_explicit_runtime_permissions(tmp_path) -> None:
    app, _ = configured_app(tmp_path)
    workspace = tmp_path / "configured-workspace"
    workspace.mkdir()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        response = client.post(
            "/api/v1/runs",
            json={
                "idempotency_key": str(uuid4()),
                "goal": "Create a configured run",
                "abaqus_permission": {"mode": "allow"},
                "solver_permission": {
                    "mode": "allow_with_limit",
                    "limit": 2,
                },
                "workspace": str(workspace),
            },
        )

    assert response.status_code == 201, response.text
    snapshot = response.json()
    assert snapshot["runtime_policy"]["abaqus"] == {"mode": "allow"}
    assert snapshot["runtime_policy"]["solver"] == {
        "mode": "allow_with_limit",
        "limit": 2,
    }


def settled(client, run_id, *, status=None):
    for _ in range(200):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        value = response.json()
        if not value["active"] and (
            status is None or value["view"]["status"] == status
        ):
            return value
        time.sleep(0.03)
    raise AssertionError(value)


def start(client, run_id):
    response = client.post(
        f"/api/v1/runs/{run_id}/start", json={"command_id": str(uuid4())}
    )
    assert response.status_code == 202, response.text
    return settled(client, run_id, status="waiting_for_user")


def answer_body(snapshot):
    return {
        "command_id": str(uuid4()),
        "question_id": snapshot["view"]["question"]["question_id"],
        "expected_task_revision": snapshot["view"]["task_revision"],
        "answer": "1 mm",
    }


def test_commands_questions_and_read_only_state(tmp_path):
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/runs").status_code == 401
        login(client)
        key = uuid4()
        run_id = create(client, key=key)
        assert create(client, key=key) == run_id
        workspace = app.state.controller.registry.load(run_id).request.workspace
        assert (
            client.post(
                "/api/v1/runs",
                json={
                    "idempotency_key": str(key),
                    "goal": "another",
                    "workspace": str(workspace),
                },
            ).status_code
            == 409
        )
        wait = start(client, run_id)
        state = tmp_path / "web/states" / run_id
        before = {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in state.iterdir()
            if p.is_file()
        }
        for _ in range(3):
            assert client.get(f"/api/v1/runs/{run_id}").json()["can_answer"]
        assert before == {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in state.iterdir()
            if p.is_file()
        }
        body = answer_body(wait)
        assert (
            client.post(f"/api/v1/runs/{run_id}/answers", json=body).status_code == 202
        )
        done = settled(client, run_id, status="completed")
        assert done["view"]["outcome"]["verification_scope"] == "execution_evidence"
        assert done["view"]["question"]["answer"] == "1 mm"
        assert len(calls) == 3
        assert client.post(f"/api/v1/runs/{run_id}/answers", json=body).json()[
            "replayed"
        ]
        assert len(calls) == 3
        assert (
            client.post(
                f"/api/v1/runs/{run_id}/answers", json={**body, "answer": "2 mm"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/v1/runs/{run_id}/continue", json={"command_id": str(uuid4())}
            ).status_code
            == 409
        )
        assert "run_command" in calls[0]
        assert "lookup_abaqus_api" not in calls[0]
        assert "discover_abaqus_api" not in calls[0]
        launch = json.loads((state / "launch.json").read_bytes())
        assert launch["api_catalog_mode"] == "automatic_verified_only"
        assert launch["schema_version"] == "coding-run-launch-v10"
        assert all(names == calls[0] for names in calls)


def test_persistent_wait_and_new_browser_after_server_restart(tmp_path):
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id = create(client)
        wait = start(client, run_id)
    assert len(calls) == 1
    app2, resumed = configured_app(tmp_path)
    with TestClient(app2, base_url="http://127.0.0.1") as client:
        login(client)
        restored = client.get(f"/api/v1/runs/{run_id}").json()
        assert restored["view"]["question"] == wait["view"]["question"]
        assert resumed == []
        client.post(f"/api/v1/runs/{run_id}/answers", json=answer_body(restored))
        done = settled(client, run_id, status="completed")
        assert done["view"]["usage"]["model_requests"] == 3
        assert len(resumed) == 2


def test_answer_survives_provider_failure_and_continue(tmp_path):
    app, _calls = configured_app(tmp_path, crash_once=[False])
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id = create(client)
        wait = start(client, run_id)
        body = answer_body(wait)
        client.post(f"/api/v1/runs/{run_id}/answers", json=body)
        interrupted = settled(client, run_id, status="interrupted")
        assert interrupted["view"]["question"]["answer"] == "1 mm"
        assert interrupted["can_continue"]
        assert client.post(f"/api/v1/runs/{run_id}/answers", json=body).json()[
            "answer_saved"
        ]
        client.post(
            f"/api/v1/runs/{run_id}/continue", json={"command_id": str(uuid4())}
        )
        done = settled(client, run_id, status="completed")
        assert (
            done["view"]["usage"]["model_requests"]
            >= interrupted["view"]["usage"]["model_requests"]
        )


def test_negative_verification_and_changed_artifact(tmp_path):
    app, _ = configured_app(tmp_path, fail=True)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id = create(client)
        wait = start(client, run_id)
        client.post(f"/api/v1/runs/{run_id}/answers", json=answer_body(wait))
        done = settled(client, run_id, status="failed")
        assert not done["view"]["verification"]["passed"]
        file_id = done["view"]["files"][0]["id"]
        url = f"/api/v1/runs/{run_id}/artifacts/{file_id}?preview=true"
        assert client.get(url).text == "result"
        assert not list((tmp_path / "web/cache").iterdir())
        workspace = app.state.controller.registry.load(run_id).request.workspace
        (workspace / "out.txt").write_text("changed", encoding="utf-8")
        assert client.get(url).status_code == 409


def test_boundaries_and_corrupt_ledger(tmp_path):
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/runs").status_code == 401
        login(client)
        assert (
            client.get("/api/v1/runs", headers={"host": "evil.example"}).status_code
            == 421
        )
        assert (
            client.get(
                "/api/v1/runs", headers={"origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/runs",
                json={
                    "idempotency_key": str(uuid4()),
                    "goal": "x",
                    "workspace": "C:/existing-project",
                },
                headers={"x-csrf-token": "wrong"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/runs", content="{}", headers={"content-type": "text/plain"}
            ).status_code
            == 415
        )
        assert (
            client.post(
                "/api/v1/runs",
                json={
                    "idempotency_key": str(uuid4()),
                    "goal": "x",
                    "workspace": "C:/existing-project",
                    "state_dir": "C:/",
                },
            ).status_code
            == 422
        )
        run_id = create(client)
        wait = start(client, run_id)
        (tmp_path / "web/states" / run_id / "user_questions.json").write_text(
            "{}", encoding="utf-8"
        )
        assert (
            client.get(f"/api/v1/runs/{run_id}").json()["view"]["status"]
            == "unavailable"
        )
        assert (
            client.post(
                f"/api/v1/runs/{run_id}/answers", json=answer_body(wait)
            ).status_code
            == 409
        )
        assert len(calls) == 1


def test_one_active_segment_and_duplicate_start(tmp_path):
    block = threading.Event()
    app, _ = configured_app(tmp_path, block=block)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            first, second = create(client), create(client)
            body = {"command_id": str(uuid4())}
            assert (
                client.post(f"/api/v1/runs/{first}/start", json=body).status_code == 202
            )
            assert client.get(f"/api/v1/runs/{first}").json()["active"]
            assert client.post(f"/api/v1/runs/{first}/start", json=body).json()[
                "replayed"
            ]
            assert (
                client.post(
                    f"/api/v1/runs/{second}/start", json={"command_id": str(uuid4())}
                ).status_code
                == 409
            )
            block.set()
            settled(client, first, status="waiting_for_user")
    finally:
        block.set()


@pytest.mark.asyncio
async def test_bounded_events_and_subscription_cleanup():
    events = ViewEvents()
    events.loop = asyncio.get_running_loop()
    async with events.subscribe("run") as queue:
        for _ in range(100):
            events.notify("run")
        await asyncio.sleep(0)
        assert queue.qsize() == 1
    assert events.subscribers == {}
