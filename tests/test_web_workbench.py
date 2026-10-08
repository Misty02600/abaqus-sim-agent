import asyncio
import hashlib
import json
import threading
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from abaqus_sim_agent.web.events import ViewEvents
from tests.support.web import (
    answer_body,
    configured_app,
    create,
    login,
    settled,
    start,
)


def test_create_persists_tool_selection_and_rejects_unavailable_tools(tmp_path):
    app, calls = configured_app(tmp_path)
    workspace = tmp_path / "tools-workspace"
    workspace.mkdir()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        base = {
            "goal": "Inspect API evidence",
            "workspace": str(workspace),
        }
        model = {"model_provider": "deepseek", "model_name": "deepseek-v4-flash"}
        for selected in (["unknown"], ["discover_abaqus_api"], ["knowledge_read_file"]):
            response = client.post(
                "/api/v1/runs",
                json={
                    **base,
                    "idempotency_key": str(uuid4()),
                    "options": {**model, "tools": selected},
                },
            )
            assert response.status_code == 422, response.text
        response = client.post(
            "/api/v1/runs",
            json={
                **base,
                "idempotency_key": str(uuid4()),
                "options": {
                    **model,
                    "tools": ["read_file", "lookup_abaqus_api", "discover_abaqus_api"],
                    "exclude_tools": ["read_file"],
                },
            },
        )
        assert response.status_code == 201, response.text
        run_id = response.json()["view"]["run_id"]
        launch = app.state.controller.registry.load(run_id).launch.model_dump(
            mode="json"
        )
        assert set(launch["tools"]) == {
            "lookup_abaqus_api",
            "discover_abaqus_api",
            "ask_user",
            "load_capability",
            "read_tool_result",
        }
        assert "api_catalog_mode" not in launch
        assert "exclude_tools" not in launch
        assert calls == []


def test_create_requires_a_selected_workspace(tmp_path) -> None:
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        response = client.post(
            "/api/v1/runs",
            json={
                "options": {
                    "model_provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                },
                "idempotency_key": str(uuid4()),
                "goal": "Create a run",
            },
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
                "options": {
                    "model_provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                },
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


def test_commands_questions_and_read_only_state(tmp_path):
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        key = uuid4()
        run_id = create(client, key=key)
        assert create(client, key=key) == run_id
        workspace = app.state.controller.registry.load(run_id).request.workspace
        assert (
            client.post(
                "/api/v1/runs",
                json={
                    "options": {
                        "model_provider": "deepseek",
                        "model_name": "deepseek-v4-flash",
                    },
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
        assert "lookup_abaqus_api" in calls[0]
        assert "discover_abaqus_api" not in calls[0]
        launch = json.loads((state / "launch.json").read_bytes())
        assert "api_catalog_mode" not in launch
        assert set(launch["tools"]) == set(calls[0])
        assert launch["schema_version"] == "coding-run-launch-v11"
        assert all(names == calls[0] for names in calls)


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
                    "options": {
                        "model_provider": "deepseek",
                        "model_name": "deepseek-v4-flash",
                    },
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
                    "options": {
                        "model_provider": "deepseek",
                        "model_name": "deepseek-v4-flash",
                    },
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
