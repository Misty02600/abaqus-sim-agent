"""Web 测试共用的应用工厂与 HTTP 操作。"""

import time
from uuid import uuid4

from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.coding.model import DeepSeekGateway
from abaqus_sim_agent.coding.preferences import RunPreferences
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig


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
        overrides=RunPreferences(api_catalog_dir=api_catalog_dir(tmp_path)),
        knowledge_packs_root=tmp_path / "knowledge-packs",
        stream_model=False,
        **CodingRunBudget(
            max_model_requests=8, max_abaqus_calls=0, max_solver_calls=0
        ).as_request_controls(),
    )
    app = create_app(
        config,
        api_keys={DeepSeekGateway.OFFICIAL: SecretStr("fixture-key")},
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


def message_body(snapshot, text, command_id=None):
    return {
        "command_id": str(command_id or uuid4()),
        "text": text,
        "expected_task_revision": snapshot["view"]["task_revision"],
        "expected_amendment_sequence": snapshot["view"]["amendment_sequence"],
    }


def create_selected(client, workspace, *, key=None):
    return client.post(
        "/api/v1/runs",
        json={
            "idempotency_key": str(key or uuid4()),
            "goal": "Write out.txt after asking for thickness",
            "workspace": str(workspace),
        },
    )
