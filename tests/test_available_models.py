import json
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials

from abaqus_sim_agent.coding import model_selection
from abaqus_sim_agent.coding.cli import run_cli
from abaqus_sim_agent.coding.model_connections import provider_key
from abaqus_sim_agent.coding.options import RunOptions, resolve_run_config
from abaqus_sim_agent.coding.providers import ModelProvider, ProviderOption
from abaqus_sim_agent.web.contracts import WebError
from tests.support.web import Client, configured_app, login


@pytest.fixture
def selection_catalog(monkeypatch):
    def entry(provider, names):
        return ProviderOption(
            id=provider,
            label=provider.value,
            models=names,
            api_key_environment=None,
            local_auth=False,
        )

    monkeypatch.setattr(
        model_selection,
        "provider_catalog",
        lambda: (
            entry(
                ModelProvider.OPENAI,
                ("no-tools", "no-text", "unknown", "gpt-4.1"),
            ),
            entry(ModelProvider.ANTHROPIC, ("claude-sonnet-4-5",)),
            entry(ModelProvider.OPENAI_CODEX, ("codex-fixture",)),
        ),
    )
    monkeypatch.setattr(
        model_selection, "require_provider_dependencies", lambda _: None
    )

    def window(provider, name):
        if name == "unknown":
            raise ValueError("unknown window")
        return 200_000

    monkeypatch.setattr(model_selection, "resolve_context_window", window)
    monkeypatch.setattr(
        model_selection,
        "infer_model_profile",
        lambda name: {
            "supports_tools": not name.endswith(":no-tools"),
            "supports_text_output": not name.endswith(":no-text"),
        },
    )


def test_available_models_filter_auth_metadata_tools_and_sdk(
    selection_catalog, monkeypatch
):
    assert model_selection.available_models({}) == ()
    assert (
        model_selection.available_models({ModelProvider.OPENAI: SecretStr(" ")}) == ()
    )
    keys = {
        ModelProvider.OPENAI: SecretStr("secret"),
        ModelProvider.ANTHROPIC: SecretStr("secret"),
    }
    available = model_selection.available_models(keys)
    assert [(m.provider, m.name) for m in available] == [
        (ModelProvider.ANTHROPIC, "claude-sonnet-4-5"),
        (ModelProvider.OPENAI, "gpt-4.1"),
    ]
    assert "secret" not in json.dumps([m.model_dump(mode="json") for m in available])

    def dependencies(provider):
        if provider is ModelProvider.ANTHROPIC:
            raise ValueError("SDK unavailable")

    monkeypatch.setattr(model_selection, "require_provider_dependencies", dependencies)
    assert [m.provider for m in model_selection.available_models(keys)] == [
        ModelProvider.OPENAI
    ]


def test_oauth_ready_state_can_supply_models_without_an_api_key(selection_catalog):
    available = model_selection.available_models(
        {}, authenticated_providers=(ModelProvider.OPENAI_CODEX,)
    )
    assert [m.provider for m in available] == [ModelProvider.OPENAI_CODEX]


def test_protocol_alias_uses_the_same_key_as_credential_discovery():
    key = SecretStr("fixture")
    keys = {ModelProvider.OPENAI: key}
    assert ModelProvider.OPENAI_RESPONSES in model_selection.configured_providers(keys)
    assert provider_key(ModelProvider.OPENAI_RESPONSES, keys) == key


@pytest.mark.parametrize(
    ("provider", "name"),
    [
        (ModelProvider.OPENAI_CODEX, "gpt-5.5"),
        (ModelProvider.GITHUB_COPILOT, "gpt-5.4"),
        (ModelProvider.GOOGLE_CLOUD, "gemini-2.5-flash"),
    ],
)
def test_explicit_choice_cannot_bypass_the_available_list(tmp_path, provider, name):
    with pytest.raises(ValueError, match="不支持目录外型号"):
        resolve_run_config(
            tmp_path,
            RunOptions(model_provider=provider, model_name=name),
            models=(),
        )


@pytest.mark.parametrize(("name", "expected_exit"), [("gpt-5.4", 0), ("gpt-5.5", 1)])
def test_cli_accepts_only_available_copilot_models(
    tmp_path, monkeypatch, name, expected_exit
):
    monkeypatch.setenv("GITHUB_COPILOT_API_KEY", "fixture")
    seen = []

    def factory(**kwargs):
        seen.append(kwargs)

        def respond(messages, info):
            output = next(
                t.name for t in info.output_tools if t.name.endswith("ResponseDecision")
            )
            return ModelResponse(
                parts=[ToolCallPart(output, {"status": "response", "summary": "Done"})]
            )

        return FunctionModel(respond), Client()

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.create_deepseek_chat_model", factory
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert (
        run_cli(
            [
                "--goal",
                "Explain",
                "--workspace",
                str(workspace),
                "--state-dir",
                str(tmp_path / "state"),
                "--model-provider",
                "github-copilot",
                "--model",
                name,
            ]
        )
        == expected_exit
    )
    if expected_exit:
        assert seen == []
        return
    assert seen[0]["gateway"] is ModelProvider.GITHUB_COPILOT
    assert seen[0]["model_name"] == name


def test_web_rejects_unlisted_codex_model_even_after_account_connection(tmp_path):
    app, _ = configured_app(tmp_path)
    controller = app.state.controller
    controller.api_keys = {}
    controller.credential_store.save_codex(
        OpenAICodexCredentials(
            access_token="fixture",
            refresh_token="refresh-fixture",
            account_id="fixture-account",
        )
    )
    controller.config = replace(controller.config, overrides=RunOptions())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        assert "openai-codex" in client.get("/api/v1/model-connections").json()
        assert not [
            m
            for m in client.get("/api/v1/models").json()
            if m["provider"] == "openai-codex"
        ]
        response = client.post(
            "/api/v1/runs",
            json={
                "idempotency_key": str(uuid4()),
                "goal": "Explain",
                "workspace": str(workspace),
                "options": {"model_provider": "openai-codex", "model_name": "gpt-5.5"},
            },
        )
        assert response.status_code == 422, response.text
        assert "不支持目录外型号" in response.json()["message"]


@pytest.mark.parametrize(
    ("provider", "name"),
    [(None, None), (ModelProvider.OPENAI, None), (None, "gpt-4.1")],
)
def test_model_selection_requires_both_fields_even_with_available_models(
    selection_catalog, tmp_path, provider, name
):
    models = model_selection.available_models({ModelProvider.OPENAI: SecretStr("key")})
    assert models
    with pytest.raises(ValueError, match="请先选择模型"):
        resolve_run_config(
            tmp_path,
            RunOptions(
                **{
                    k: v
                    for k, v in {"model_provider": provider, "model_name": name}.items()
                    if v is not None
                }
            ),
            models=models,
        )


def test_cli_and_web_require_selection_and_resolve_the_same_choice(
    selection_catalog, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-not-echoed")
    assert run_cli(["--list-models"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [m["provider"] for m in listed] == ["openai"]

    def factory(**kwargs):
        def respond(messages, info):
            output = next(
                t.name for t in info.output_tools if t.name.endswith("ResponseDecision")
            )
            return ModelResponse(
                parts=[ToolCallPart(output, {"status": "response", "summary": "Done"})]
            )

        return FunctionModel(respond), Client()

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.create_deepseek_chat_model", factory
    )
    workspace = tmp_path / "cli-workspace"
    workspace.mkdir()
    state = tmp_path / "cli-state"
    assert (
        run_cli(
            [
                "--goal",
                "Explain",
                "--workspace",
                str(workspace),
                "--state-dir",
                str(state),
            ]
        )
        == 1
    )
    assert "请先选择模型" in capsys.readouterr().out
    assert not (state / "launch.json").exists()
    assert (
        run_cli(
            [
                "--model-provider",
                "openai",
                "--model",
                "gpt-4.1",
                "--goal",
                "Explain",
                "--workspace",
                str(workspace),
                "--state-dir",
                str(state),
            ]
        )
        == 0
    )
    cli_launch = json.loads((state / "launch.json").read_text())
    assert (cli_launch["model_provider"], cli_launch["model_name"]) == (
        "openai",
        "gpt-4.1",
    )
    assert "fixture-not-echoed" not in capsys.readouterr().out

    app, _ = configured_app(tmp_path / "server")
    controller = app.state.controller
    controller.config = replace(controller.config, overrides=RunOptions())
    controller.api_keys = {ModelProvider.OPENAI: SecretStr("fixture-not-echoed")}
    workspace = tmp_path / "web-workspace"
    workspace.mkdir()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        assert client.get("/api/v1/models").json() == listed
        missing = client.post(
            "/api/v1/runs",
            json={
                "idempotency_key": str(uuid4()),
                "goal": "Explain",
                "workspace": str(workspace),
            },
        )
        assert missing.status_code == 422
        assert "请先选择模型" in missing.json()["message"]
        response = client.post(
            "/api/v1/runs",
            json={
                "options": {"model_provider": "openai", "model_name": "gpt-4.1"},
                "idempotency_key": str(uuid4()),
                "goal": "Explain",
                "workspace": str(workspace),
            },
        )
        assert response.status_code == 201, response.text
        run_id = response.json()["view"]["run_id"]
        saved = controller.registry.load(run_id).launch
        assert (saved.model_provider.value, saved.model_name) == (
            cli_launch["model_provider"],
            cli_launch["model_name"],
        )
        response = client.post(
            "/api/v1/model-connections",
            json={"provider": "anthropic", "api_key": "new-key"},
        )
        assert response.status_code == 200
        assert client.get("/api/v1/models").json()[0]["provider"] == "anthropic"
        assert controller._prepared(controller.registry.load(run_id)).launch == saved
        controller.api_keys.pop(ModelProvider.OPENAI)
        with pytest.raises(WebError, match="API Key"):
            controller._model_key(saved.model_provider)
        assert controller.registry.load(run_id).launch == saved


def test_empty_web_inventory_does_not_create_a_deepseek_session(
    selection_catalog, tmp_path
):
    app, _ = configured_app(tmp_path)
    app.state.controller.api_keys = {}
    app.state.controller.config = replace(
        app.state.controller.config, overrides=RunOptions()
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        assert client.get("/api/v1/models").json() == []
        assert client.get("/api/v1/model-providers").json()
        response = client.post(
            "/api/v1/runs",
            json={
                "idempotency_key": str(uuid4()),
                "goal": "Explain",
                "workspace": str(workspace),
            },
        )
        assert response.status_code == 422
        assert "请先选择模型" in response.json()["message"]
