from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from pydantic_ai.messages import (
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.coding.model import DeepSeekGateway
from abaqus_sim_agent.coding.options import RunOptions
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig
from tests.support.abaqus import FakeAbaqusProcess
from tests.support.web import Client, create_selected, login, message_body, settled


class ImageProcess(FakeAbaqusProcess):
    async def run(self, command, *, cwd, timeout_seconds):
        result = await super().run(command, cwd=cwd, timeout_seconds=timeout_seconds)
        (cwd / "plots").mkdir(exist_ok=True)
        Image.new("RGB", (12, 8), "red").save(cwd / "plots/displacement.png")
        return result


def image_app(tmp_path):
    def factory(**kwargs):
        def model(messages, info):
            prompts = [
                p.content
                for m in messages
                for p in m.parts
                if isinstance(p, UserPromptPart)
            ]
            if "Explain" in str(prompts[-1]):
                part = ToolCallPart(
                    "final_result_ResponseDecision",
                    {"summary": "原图片仍可查看。![位移图](plots/displacement.png)"},
                )
            else:
                returned = {
                    p.tool_name
                    for m in messages
                    for p in m.parts
                    if isinstance(p, ToolReturnPart)
                }
                if "write_file" not in returned:
                    part = ToolCallPart(
                        "write_file", {"path": "preview.py", "content": "x = 1\n"}
                    )
                elif "run_cae_script" not in returned:
                    part = ToolCallPart("run_cae_script", {"script_path": "preview.py"})
                else:
                    part = ToolCallPart(
                        "final_result_ReadyForVerificationDecision",
                        {
                            "summary": "离线图片展示夹具完成。",
                            "deliverables": [
                                {"relative_path": "plots/displacement.png"}
                            ],
                        },
                    )
            return ModelResponse(parts=[part])

        return FunctionModel(model), Client()

    return create_app(
        ServerConfig(
            root=tmp_path / "web",
            overrides=RunOptions(),
            stream_model=False,
            **CodingRunBudget(
                max_model_requests=8, max_abaqus_calls=2, max_solver_calls=0
            ).as_request_controls(),
        ),
        api_keys={DeepSeekGateway.OFFICIAL: SecretStr("fixture")},
        model_factory=factory,
        runner_factory=lambda w, s, **kw: Abaqus2024Runner(
            w, s, process_strategy=ImageProcess("probe")
        ),
    )


def test_workspace_image_reference_survives_restart_and_continuation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    path = "plots/displacement.png"
    with TestClient(image_app(tmp_path), base_url="http://127.0.0.1") as client:
        login(client)
        created = create_selected(client, workspace)
        assert created.status_code == 201, created.text
        rid = created.json()["view"]["run_id"]
        client.post(f"/api/v1/runs/{rid}/start", json={"command_id": str(uuid4())})
        first = settled(client, rid, status="completed")
        image = next(f for f in first["view"]["files"] if f["relative_path"] == path)
        assert image["fresh"]
        url = f"/api/v1/runs/{rid}/workspace/file"
        original = client.get(url, params={"path": path})
        assert original.status_code == 200
        assert original.headers["cache-control"] == "no-store"

    with TestClient(image_app(tmp_path), base_url="http://127.0.0.1") as client:
        login(client)
        restarted = client.get(f"/api/v1/runs/{rid}").json()
        assert restarted["view"]["messages"] == first["view"]["messages"]
        assert client.get(url, params={"path": path}).content == original.content
        response = client.post(
            f"/api/v1/runs/{rid}/messages",
            json=message_body(restarted, "Explain the existing image"),
        )
        assert response.status_code == 202, response.text
        continued = settled(client, rid, status="completed")
        image = next(
            f for f in continued["view"]["files"] if f["relative_path"] == path
        )
        assert not image["fresh"]
        tool = next(
            m
            for m in continued["view"]["messages"]
            if m["tool_name"] == "run_cae_script"
        )
        assert path in tool["tool_summary"]["changed_paths"]
        assert path in continued["view"]["messages"][-1]["text"]
        assert client.get(url, params={"path": path}).content == original.content

        Image.new("RGB", (12, 8), "blue").save(workspace / path)
        replaced = client.get(url, params={"path": path})
        assert replaced.status_code == 200
        assert replaced.content != original.content
        assert (
            client.get(
                f"/api/v1/runs/{rid}/artifacts/{image['id']}?preview=true"
            ).status_code
            == 409
        )
        (workspace / path).unlink()
        assert client.get(url, params={"path": path}).status_code == 404
