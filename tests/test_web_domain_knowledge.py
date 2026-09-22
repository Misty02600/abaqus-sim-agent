import json
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel
from tests.test_web_workbench import Client, NoProcess, login, settled

from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.contracts import Registration
from abaqus_sim_agent.web.controller import ServerConfig


def _write_pack(root: Path) -> None:
    pack = root / "flexible-electronics-fea"
    pack.mkdir(parents=True)
    (pack / "README.md").write_text("# Private reference\n", encoding="utf-8")
    (pack / "pack.json").write_text(
        json.dumps(
            {
                "schema_version": "domain-knowledge-pack-v1",
                "pack_id": "flexible-electronics-fea",
                "release": "2026.09.1",
                "summary": "柔性电子参考",
                "entrypoints": ["README.md"],
                "compatibility": ["Abaqus 2024"],
                "resources": [
                    {
                        "path": "README.md",
                        "kind": "guide",
                        "title": None,
                        "source": {
                            "kind": "original",
                            "title": "本机资料",
                            "reference": None,
                            "version": None,
                            "license": None,
                            "notes": None,
                        },
                        "validation": {
                            "content_review": "unreviewed",
                            "runtime": "not_run",
                            "physics": "not_checked",
                            "task_benefit": "not_evaluated",
                            "evidence_ids": [],
                            "notes": None,
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_web_lists_and_binds_explicit_private_knowledge_pack(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _write_pack(packs)
    app = create_app(
        ServerConfig(root=tmp_path / "web", knowledge_packs_root=packs),
        bootstrap_token="bootstrap",
    )

    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        listed = client.get("/api/v1/knowledge-packs")
        assert listed.status_code == 200
        assert [item["pack_id"] for item in listed.json()["packs"]] == [
            "flexible-electronics-fea"
        ]

        created = client.post(
            "/api/v1/runs",
            json={
                "idempotency_key": str(uuid4()),
                "goal": "Use the private reference.",
                "knowledge_pack_ids": ["flexible-electronics-fea"],
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["knowledge_packs"][0]["pack_id"] == ("flexible-electronics-fea")
        run_id = body["view"]["run_id"]
        launch = Registration.model_validate_json(
            (tmp_path / "web/registry" / run_id / "registration.json").read_bytes()
        ).launch
        assert launch.domain_knowledge is not None


def test_selected_pack_is_readable_through_product_agent_tools(tmp_path: Path) -> None:
    packs = tmp_path / "packs"
    _write_pack(packs)
    observed: dict[str, object] = {}

    def factory(**_kwargs):
        def respond(messages, info):
            observed["tools"] = {tool.name for tool in info.function_tools}
            returned = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
                and part.tool_name == "knowledge_read_file"
            ]
            if not returned:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "knowledge_read_file",
                            {"path": "flexible-electronics-fea/README.md"},
                            tool_call_id="read-private-pack",
                        )
                    ]
                )
            observed["content"] = returned[-1].content
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "final_result_ResponseDecision",
                        {
                            "status": "response",
                            "summary": "已按需读取选中的本机私有参考资料。",
                        },
                    )
                ]
            )

        return FunctionModel(respond), Client()

    app = create_app(
        ServerConfig(
            root=tmp_path / "web",
            knowledge_packs_root=packs,
            stream_model=False,
        ),
        api_key=SecretStr("fixture"),
        bootstrap_token="bootstrap",
        model_factory=factory,
        runner_factory=lambda workspace, state, **_kwargs: Abaqus2024Runner(
            workspace, state, process_strategy=NoProcess()
        ),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        created = client.post(
            "/api/v1/runs",
            json={
                "idempotency_key": str(uuid4()),
                "goal": "Read the selected reference and answer.",
                "knowledge_pack_ids": ["flexible-electronics-fea"],
            },
        )
        run_id = created.json()["view"]["run_id"]
        assert (
            client.post(
                f"/api/v1/runs/{run_id}/start",
                json={"command_id": str(uuid4())},
            ).status_code
            == 202
        )
        done = settled(client, run_id, status="completed")

    assert done["view"]["outcome"]["completion_kind"] == "response"
    assert {
        "knowledge_list_directory",
        "knowledge_read_file",
    } <= observed["tools"]
    assert "Private reference" in str(observed["content"])
