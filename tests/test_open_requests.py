from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from tests.test_web_workbench import (
    Client,
    NoProcess,
    login,
    settled,
)

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.application import prepare_new_run
from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.coding.launch import LaunchManifest
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig


def open_request_app(tmp_path, mode="response"):
    observed = []

    def factory(**kwargs):
        def respond(messages, info):
            observed.append([t.name for t in info.output_tools])
            wrote = any(
                isinstance(p, ToolReturnPart) and p.tool_name == "write_file"
                for m in messages
                for p in m.parts
            )
            if mode in {"script", "misuse"} and not wrote:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "write_file",
                            {
                                "path": "one_step.py",
                                "content": "# only the requested step\nx = 1\n",
                            },
                            tool_call_id="write",
                        )
                    ]
                )
            if mode == "script":
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "final_result_ReadyForVerificationDecision",
                            {
                                "status": "ready_for_verification",
                                "summary": "只交付所请求的脚本，未运行求解。",
                                "deliverables": [{"relative_path": "one_step.py"}],
                            },
                        )
                    ]
                )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "final_result_ResponseDecision",
                        {
                            "status": "response",
                            "summary": "CPS4R 是四节点双线性平面应力减缩积分单元。这个概念问题不需要你的板厚。",
                        },
                    )
                ]
            )

        return FunctionModel(respond), Client()

    config = ServerConfig(
        root=tmp_path / "web",
        stream_model=False,
        budget=CodingRunBudget(
            max_model_requests=5, max_abaqus_calls=0, max_solver_calls=0
        ),
    )
    return create_app(
        config,
        api_key=SecretStr("fixture"),
        model_factory=factory,
        runner_factory=lambda w, s, **kw: Abaqus2024Runner(
            w, s, process_strategy=NoProcess()
        ),
    ), observed


def new_request(client, goal):
    key = uuid4()
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
            "goal": goal,
            "workspace": str(workspace),
        },
    )
    assert response.status_code == 201
    rid = response.json()["view"]["run_id"]
    assert (
        client.post(
            f"/api/v1/runs/{rid}/start", json={"command_id": str(uuid4())}
        ).status_code
        == 202
    )
    return rid


def test_text_question_finishes_without_files_or_verification_and_restores(tmp_path):
    app, observed = open_request_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = new_request(client, "CPS4R 是什么？只解释，不创建文件。")
        done = settled(client, rid, status="completed")
        assert done["view"]["outcome"]["completion_kind"] == "response"
        assert done["view"]["outcome"]["verification_scope"] is None
        assert done["view"]["verification"] is None
        assert done["view"]["files"] == []
        assert done["view"]["question"] is None
        assert done["view"]["usage"]["abaqus_calls"] == 0
        assert len(observed) == 1
        launch = LaunchManifest.model_validate_json(
            (tmp_path / "web/states" / rid / "launch.json").read_bytes()
        )
        assert launch.schema_version == "coding-run-launch-v10"
        assert launch.capability_definitions
        assert launch.context_policy_version == "deepseek-1m-tiered-summary-v1"
    other, again = open_request_app(tmp_path)
    with TestClient(other, base_url="http://127.0.0.1") as client:
        login(client)
        restored = client.get(f"/api/v1/runs/{rid}").json()
        assert restored["view"] == done["view"]
        assert not restored["can_continue"]
        assert again == []


def test_one_script_needs_no_solver_or_full_model(tmp_path):
    app, _ = open_request_app(tmp_path, "script")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = new_request(client, "只写一个 Python 脚本，定义 x=1。不要建模或执行。")
        done = settled(client, rid, status="completed")
        assert (
            done["view"]["outcome"].get("completion_kind", "deliverables")
            == "deliverables"
        )
        assert done["view"]["verification"]["passed"]
        assert done["view"]["verification"]["execution_attempts"] == 0
        assert done["view"]["verification"]["declared_deliverable_paths"] == [
            "one_step.py"
        ]
        assert done["view"]["question"] is None


def test_text_completion_cannot_hide_file_changes(tmp_path):
    app, _ = open_request_app(tmp_path, "misuse")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = new_request(client, "创建文件。")
        done = settled(client, rid, status="failed")
        assert done["view"]["outcome"]["failure_stage"] == "response_scope"
        assert done["view"]["verification"] is None


@pytest.mark.asyncio
async def test_response_output_is_enabled_only_for_open_request_agent(tmp_path):
    config = ServerConfig(
        root=tmp_path / "host",
    )
    (tmp_path / "workspace").mkdir()
    prepared = prepare_new_run(
        run_id="compat",
        goal="explain",
        workspace=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        execution=config.execution,
        runtime=config.runtime,
    )
    observed = []

    def model(messages, info):
        observed.append(([t.name for t in info.output_tools], info.instructions))
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "final_result_FailedDecision",
                    {
                        "status": "failed",
                        "failure_stage": "fixture",
                        "summary": "fixture",
                    },
                )
            ]
        )

    for enabled in (False, True):
        agent = create_abaqus_coding_agent(
            FunctionModel(model),
            prepared.request,
            question_protocol="ask_user_v1",
            response_enabled=enabled,
            planning_enabled=False,
        )
        from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies

        await agent.run(
            "explain",
            deps=CodingAgentDependencies(
                request=prepared.request,
                runner=Abaqus2024Runner(
                    prepared.request.workspace,
                    prepared.request.state_dir,
                    process_strategy=NoProcess(),
                ),
            ),
        )
    assert "final_result_ResponseDecision" not in observed[0][0]
    assert "final_result_ResponseDecision" in observed[1][0]
    assert "CURRENT requested action" in observed[1][1]
    assert "creating only geometry needs no material or load" in observed[1][1]
    assert prepared.launch.schema_version == "coding-run-launch-v10"
