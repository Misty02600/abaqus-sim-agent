import json
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.messages import (
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.coding.cli import run_cli
from abaqus_sim_agent.coding.model import DeepSeekGateway
from abaqus_sim_agent.coding.run_lock import CodingRunFileLock
from abaqus_sim_agent.coding.workspace import default_run_root
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.contracts import WebError
from abaqus_sim_agent.web.controller import ServerConfig
from abaqus_sim_agent.web.storage import RunRegistry
from tests.support.web import Client, login, message_body, settled


@pytest.fixture
def shared_sessions(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
    calls = []

    def factory(**kwargs):
        def model(messages, info):
            calls.append(messages)
            prompts = [
                str(part.content)
                for message in messages
                for part in message.parts
                if isinstance(part, UserPromptPart)
            ]
            answered = any(
                isinstance(part, ToolReturnPart) and part.tool_name == "ask_user"
                for message in messages
                for part in message.parts
            )
            if any("ASK" in prompt for prompt in prompts) and not answered:
                part = ToolCallPart("ask_user", {"question": "Which result?"})
            else:
                output = next(
                    t.name
                    for t in info.output_tools
                    if t.name.endswith("ResponseDecision")
                )
                part = ToolCallPart(
                    output,
                    {
                        "summary": "WEB reply"
                        if any("FOLLOWUP" in p for p in prompts)
                        else "CLI reply"
                    },
                )
            return ModelResponse(parts=[part])

        return FunctionModel(model), Client()

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.create_deepseek_chat_model", factory
    )
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.take_gateway_api_key_from_environment",
        lambda _: SecretStr("fixture"),
    )
    config = ServerConfig(
        root=tmp_path / "web", state_root=default_run_root(), stream_model=False
    )
    app = create_app(
        config,
        api_keys={DeepSeekGateway.OFFICIAL: SecretStr("fixture")},
        model_factory=factory,
    )
    return app, calls, config


def test_cli_session_is_discovered_and_continued_in_web(tmp_path, shared_sessions):
    app, calls, config = shared_sessions
    workspace = tmp_path / "cli-workspace"
    workspace.mkdir()
    marker = workspace / "keep.txt"
    marker.write_text("user file")
    rid = "run-cli-shared"
    assert (
        run_cli(
            [
                "--model-provider",
                "deepseek",
                "--model",
                "deepseek-v4-flash",
                "--workspace",
                str(workspace),
                "--run-id",
                rid,
                "--goal",
                "CLI task",
            ]
        )
        == 0
    )
    state = default_run_root() / rid
    original_request = (state / "request.json").read_bytes()
    original_launch = (state / "launch.json").read_bytes()

    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        listed = client.get("/api/v1/runs")
        assert listed.status_code == 200, listed.text
        assert [r["view"]["run_id"] for r in listed.json()["runs"]] == [rid]
        assert not (config.root / "registry" / rid / "registration.json").exists()
        first = client.get(f"/api/v1/runs/{rid}").json()
        assert first["can_message"]
        assert first["view"]["messages"][-1]["text"] == "CLI reply"
        sent = client.post(
            f"/api/v1/runs/{rid}/messages",
            json=message_body(first, "FOLLOWUP from Web"),
        )
        assert sent.status_code == 202, sent.text
        second = settled(client, rid, status="completed")
        assert second["view"]["messages"][-1]["text"] == "WEB reply"
        assert second["view"]["usage"]["model_requests"] == 2
        assert second["view"]["task_revision"] == 0
        assert (state / "request.json").read_bytes() == original_request
        assert (state / "launch.json").read_bytes() == original_launch

        with CodingRunFileLock(state):
            busy = client.get(f"/api/v1/runs/{rid}").json()
            assert busy["view"]["phase"] == "external_execution"
            assert not busy["can_message"]
            assert not busy["can_stop"]
        count = len(calls)
        # CLI 的纯 resume 不会重跑已完成的会话；显式 goal 仍是 task revision。
        assert run_cli(["--resume", str(state)]) == 1
        assert len(calls) == count
        assert run_cli(["--resume", str(state), "--goal", "CLI next task"]) == 0
        revised = client.get(f"/api/v1/runs/{rid}").json()
        assert revised["view"]["task_revision"] == 1
        assert revised["view"]["usage"]["model_requests"] == 3
        deleted = client.request(
            "DELETE", f"/api/v1/runs/{rid}", json={"command_id": str(uuid4())}
        )
        assert deleted.status_code == 200, deleted.text
        assert not state.exists()
        assert marker.read_text() == "user file"


def test_web_session_can_answer_in_cli_then_reload_in_web(tmp_path, shared_sessions):
    app, _, _ = shared_sessions
    workspace = tmp_path / "web-workspace"
    workspace.mkdir()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        created = client.post(
            "/api/v1/runs",
            json={
                "idempotency_key": str(uuid4()),
                "goal": "ASK before answering",
                "workspace": str(workspace),
                "options": {
                    "model_provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                },
            },
        )
        assert created.status_code == 201, created.text
        rid = created.json()["view"]["run_id"]
        client.post(f"/api/v1/runs/{rid}/start", json={"command_id": str(uuid4())})
        waiting = settled(client, rid, status="waiting_for_user")
        state = default_run_root() / rid
        answer = tmp_path / "answer.json"
        answer.write_text(
            json.dumps(
                {
                    "question_id": waiting["view"]["question"]["question_id"],
                    "answer": "Existing result",
                }
            )
        )
        assert run_cli(["--resume", str(state), "--answer-file", str(answer)]) == 0
        completed = client.get(f"/api/v1/runs/{rid}").json()
        assert completed["view"]["status"] == "completed"
        assert completed["view"]["usage"]["model_requests"] == 2
        assert completed["view"]["question"]["answer"] == "Existing result"


def test_registry_preserves_legacy_location_and_rejects_duplicate_state(tmp_path):
    registry = RunRegistry(tmp_path / "web", state_root=tmp_path / "runs")
    registry.initialize()
    rid = "web-" + uuid4().hex
    previous = registry.root / "states" / rid
    previous.mkdir(parents=True)
    assert registry.state_dir(rid) == previous
    (registry.state_root / rid).mkdir()
    with pytest.raises(WebError, match="两份状态"):
        registry.state_dir(rid)
