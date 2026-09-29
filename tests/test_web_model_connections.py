import asyncio
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from abaqus_sim_agent.coding import providers
from abaqus_sim_agent.coding.cli import run_cli
from abaqus_sim_agent.coding.credential_store import (
    CredentialStore,
    CredentialStoreError,
)
from abaqus_sim_agent.coding.providers import ModelProvider
from abaqus_sim_agent.web.model_login import LoginRequest, ModelLogins
from tests.support.web import configured_app, login


def test_provider_list_and_missing_dependencies_are_separate(tmp_path, monkeypatch):
    app, _ = configured_app(tmp_path)
    monkeypatch.setattr(providers, "find_spec", lambda _: None)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        response = client.get("/api/v1/model-providers")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()}
        assert {"anthropic", "openai", "opencode-go"} <= ids
        assert "xai" not in ids
        response = client.get(
            "/api/v1/model-provider-dependencies", params={"provider": "anthropic"}
        )
        assert response.status_code == 422
        assert response.json()["message"] == "缺少依赖：anthropic，请先安装。"
        response = client.post(
            "/api/v1/model-connections",
            json={"provider": "anthropic", "api_key": "must-not-echo"},
        )
        assert response.status_code == 422
        assert "must-not-echo" not in response.text
        assert not app.state.controller.api_keys.get(ModelProvider.ANTHROPIC)


def test_credentials_stay_outside_session_and_selection_is_saved(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        response = client.post(
            "/api/v1/model-connections",
            json={"provider": "openai", "api_key": "private-fixture-value"},
        )
        assert response.status_code == 200
        assert response.json() == {"configured": True}
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        response = client.post(
            "/api/v1/runs",
            json={
                "idempotency_key": str(uuid4()),
                "goal": "Explain the model",
                "workspace": str(workspace),
                "options": {"model_provider": "openai", "model_name": "gpt-4.1"},
            },
        )
        assert response.status_code == 201, response.text
        run_id = response.json()["view"]["run_id"]
        saved = app.state.controller.registry.load(run_id)
        assert saved.launch.model_provider is ModelProvider.OPENAI
        assert saved.launch.context_window == 1_000_000
        assert saved.launch.context_policy_version == "model-window-tiered-summary-v1"
        assert (
            app.state.controller._model_key(ModelProvider.OPENAI).get_secret_value()
            == "private-fixture-value"
        )
        for path in tmp_path.rglob("*.json"):
            if path == app.state.controller.credential_store.path:
                continue
            assert "private-fixture-value" not in path.read_text(encoding="utf-8")


def test_connection_write_requires_csrf(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/v1/model-connections",
            json={"provider": "openai", "api_key": "private"},
        )
        assert response.status_code == 401
        client.get("/api/v1/session")
        response = client.post(
            "/api/v1/model-connections",
            json={"provider": "openai", "api_key": "private"},
        )
        assert response.status_code == 403


@pytest.mark.parametrize(
    ("field", "value"),
    [("context_window", 128_000), ("model_base_url", "https://custom.invalid/v1")],
)
def test_web_rejects_removed_model_inputs(tmp_path, field, value):
    app, _ = configured_app(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        response = client.post(
            "/api/v1/runs",
            json={
                "idempotency_key": str(uuid4()),
                "goal": "test",
                "workspace": str(workspace),
                "options": {
                    "model_provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                    field: value,
                },
            },
        )
        assert response.status_code == 422
        response = client.post(
            "/api/v1/model-connections",
            json={"provider": "openai", "api_key": "private-fixture", field: value},
        )
        assert response.status_code == 422
        assert not app.state.controller.api_keys.get(ModelProvider.OPENAI)


def test_saved_custom_endpoint_is_rejected_without_migration(tmp_path):
    from abaqus_sim_agent.coding.launch import LaunchManifest
    from abaqus_sim_agent.web.contracts import CreateRun

    app, _ = configured_app(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    command = CreateRun.model_validate_json(
        json.dumps(
            {
                "options": {
                    "model_provider": "deepseek",
                    "model_name": "deepseek-v4-flash",
                },
                "idempotency_key": str(uuid4()),
                "goal": "test",
                "workspace": str(workspace),
            }
        )
    )
    controller = app.state.controller
    controller.registry.initialize()
    snapshot = controller.create(command)
    registration = controller.registry.load(snapshot.view.run_id)
    saved = registration.launch.model_dump(mode="json")
    saved["model_base_url"] = "https://custom.invalid/v1"
    with pytest.raises(ValueError, match="自定义 API 地址会话已不再支持"):
        LaunchManifest.model_validate_json(json.dumps(saved))


@pytest.mark.asyncio
async def test_codex_login_uses_framework_callback_and_persists_refreshable_credentials(
    tmp_path, monkeypatch
):
    from pydantic_ai.providers import openai_codex

    app, _ = configured_app(tmp_path)
    expected = openai_codex.OpenAICodexCredentials(
        access_token="access-fixture",
        refresh_token="refresh-fixture",
        account_id="account-fixture",
    )

    class Flow:
        def authorization_url(self):
            return "https://auth.example.invalid/authorize?state=fixture"

        async def exchange_code_from_callback(self):
            return expected

    monkeypatch.setattr(openai_codex, "OpenAICodexOAuthFlow", Flow)
    manager = ModelLogins(app.state.controller)
    status = await manager.begin(LoginRequest(provider=ModelProvider.OPENAI_CODEX))
    await manager.tasks[status.id]
    assert manager.statuses[status.id].state == "completed"
    source = app.state.controller.credential_store.codex_source()
    assert await source.load() == expected
    refreshed = openai_codex.OpenAICodexCredentials(
        access_token="access-new",
        refresh_token="refresh-new",
        account_id="account-fixture",
    )
    await source.save(refreshed)
    assert await source.load() == refreshed
    assert "credentials" not in manager.statuses[status.id].model_dump()
    await manager.close()


@pytest.mark.asyncio
async def test_login_failure_does_not_echo_token_and_shutdown_cancels_pending_flow(
    tmp_path, monkeypatch
):
    from pydantic_ai.providers import openai_codex

    app, _ = configured_app(tmp_path)
    cancelled = asyncio.Event()

    class Flow:
        def authorization_url(self):
            return "https://auth.example.invalid/"

        async def exchange_code_from_callback(self):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    monkeypatch.setattr(openai_codex, "OpenAICodexOAuthFlow", Flow)
    manager = ModelLogins(app.state.controller)
    await manager.begin(LoginRequest(provider=ModelProvider.OPENAI_CODEX))
    await asyncio.sleep(0)
    with pytest.raises(ValueError, match="已有登录"):
        await manager.begin(LoginRequest(provider=ModelProvider.OPENAI_CODEX))
    await manager.close()
    assert cancelled.is_set()
    assert not app.state.controller.credential_store.authenticated_providers()


def test_web_key_survives_restart_is_visible_to_cli_and_can_be_removed(
    tmp_path, capsys
):
    app, _ = configured_app(tmp_path)
    app.state.controller.api_keys = {}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        assert client.get("/api/v1/models").json() == []
        response = client.post(
            "/api/v1/model-connections",
            json={"provider": "openrouter", "api_key": "private-restart-fixture"},
        )
        assert response.status_code == 200
        inventory = client.get("/api/v1/models").json()
        assert inventory
        assert all(m["provider"] == "openrouter" for m in inventory)
    restarted, _ = configured_app(tmp_path)
    restarted.state.controller.api_keys = {}
    with TestClient(restarted, base_url="http://127.0.0.1") as client:
        login(client)
        assert client.get("/api/v1/models").json() == inventory
        assert (
            restarted.state.controller._model_key(
                ModelProvider.OPENROUTER
            ).get_secret_value()
            == "private-restart-fixture"
        )
        assert run_cli(["--list-models"]) == 0
        assert json.loads(capsys.readouterr().out) == inventory
        response = client.request(
            "DELETE", "/api/v1/model-connections/openrouter", json={}
        )
        assert response.status_code == 200
        assert client.get("/api/v1/models").json() == []
    assert run_cli(["--list-models"]) == 0
    assert json.loads(capsys.readouterr().out) == []


@pytest.mark.asyncio
async def test_web_codex_login_survives_restart_without_inventing_candidate_models(
    tmp_path, monkeypatch
):
    from pydantic_ai.providers import openai_codex

    class Flow:
        def authorization_url(self):
            return "https://auth.example.invalid/"

        async def exchange_code_from_callback(self):
            return openai_codex.OpenAICodexCredentials(
                access_token="private-access",
                refresh_token="private-refresh",
                account_id="fixture-account",
            )

    monkeypatch.setattr(openai_codex, "OpenAICodexOAuthFlow", Flow)
    app, _ = configured_app(tmp_path)
    app.state.controller.api_keys = {}
    manager = ModelLogins(app.state.controller)
    status = await manager.begin(LoginRequest(provider=ModelProvider.OPENAI_CODEX))
    await manager.tasks[status.id]
    assert manager.statuses[status.id].state == "completed"
    await manager.close()
    restarted, _ = configured_app(tmp_path)
    restarted.state.controller.api_keys = {}
    with TestClient(restarted, base_url="http://127.0.0.1") as client:
        login(client)
        models = client.get("/api/v1/models").json()
        assert models == []
        assert "openai-codex" in client.get("/api/v1/model-connections").json()
        assert "private-access" not in json.dumps(models)


@pytest.mark.asyncio
async def test_login_save_failure_is_reported_as_failure_without_credentials(
    tmp_path, monkeypatch
):
    from pydantic_ai.providers import openai_codex

    class Flow:
        def authorization_url(self):
            return "https://auth.example.invalid/"

        async def exchange_code_from_callback(self):
            return openai_codex.OpenAICodexCredentials(
                access_token="private-access",
                refresh_token="private-refresh",
                account_id="fixture-account",
            )

    def fail(*args, **kwargs):
        raise CredentialStoreError("本机凭据未能保存")

    monkeypatch.setattr(openai_codex, "OpenAICodexOAuthFlow", Flow)
    monkeypatch.setattr(CredentialStore, "save_codex", fail)
    app, _ = configured_app(tmp_path)
    manager = ModelLogins(app.state.controller)
    status = await manager.begin(LoginRequest(provider=ModelProvider.OPENAI_CODEX))
    await manager.tasks[status.id]
    failed = manager.statuses[status.id]
    assert failed.state == "failed"
    assert "保存" in failed.error
    assert "private" not in failed.model_dump_json()
    assert not app.state.controller.credential_store.read()
    await manager.close()


def test_credential_delete_requires_csrf_and_corruption_is_reported(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert (
            client.request(
                "DELETE", "/api/v1/model-connections/openai", json={}
            ).status_code
            == 401
        )
        login(client)
        path = app.state.controller.credential_store.path
        path.parent.mkdir(parents=True)
        path.write_text("private-malformed", encoding="utf-8")
        response = client.get("/api/v1/models")
        assert response.status_code == 503
        assert "private-malformed" not in response.text


def test_gateway_credential_family_can_be_removed_via_url(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        response = client.post(
            "/api/v1/model-connections",
            json={"provider": "gateway/openai", "api_key": "gateway-fixture"},
        )
        assert response.status_code == 200
        assert "gateway/anthropic" in client.get("/api/v1/model-connections").json()
        response = client.request(
            "DELETE", "/api/v1/model-connections/gateway%2Fopenai", json={}
        )
        assert response.status_code == 200
        assert "gateway/openai" not in client.get("/api/v1/model-connections").json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_expiry", "refresh_token"),
    [(None, None), (3600, None), (3600, "private-unused-refresh")],
)
async def test_copilot_login_saves_token_and_supplies_models(
    tmp_path, monkeypatch, token_expiry, refresh_token
):
    from pydantic_ai.providers import github_copilot

    class Flow:
        def __init__(self, *, client_id):
            assert client_id == "fixture-own-client"

        async def start(self):
            return github_copilot.GitHubCopilotDeviceAuthorization(
                device_code="private-device",
                user_code="fixture-code",
                verification_uri="https://github.com/login/device",
                expires_in=60,
            )

        async def wait_for_authorization(self):
            return github_copilot.GitHubCopilotCredentials(
                access_token="private-copilot",
                token_type="bearer",
                scope="",
                expires_in=token_expiry,
                refresh_token=refresh_token,
            )

    monkeypatch.setattr(github_copilot, "GitHubCopilotOAuthFlow", Flow)
    app, _ = configured_app(tmp_path)
    app.state.controller.api_keys = {}
    manager = ModelLogins(app.state.controller)
    status = await manager.begin(
        LoginRequest(
            provider=ModelProvider.GITHUB_COPILOT, client_id="fixture-own-client"
        )
    )
    await manager.tasks[status.id]
    assert manager.statuses[status.id].state == "completed"
    assert "private-copilot" not in manager.statuses[status.id].model_dump_json()
    assert (
        CredentialStore().api_keys({})[ModelProvider.GITHUB_COPILOT].get_secret_value()
        == "private-copilot"
    )
    assert app.state.controller.available_models()
    from abaqus_sim_agent.coding.model_connections import create_model

    _, connection = create_model(
        gateway=ModelProvider.GITHUB_COPILOT,
        model_name="gpt-5.4",
        api_key=app.state.controller._model_key(ModelProvider.GITHUB_COPILOT),
    )
    try:
        assert connection.provider.client.api_key == "private-copilot"
    finally:
        await connection.close()
    await manager.close()
