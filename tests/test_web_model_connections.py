import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from abaqus_sim_agent.coding import providers
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


def test_credentials_stay_in_host_memory_and_selection_is_saved(tmp_path):
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
            app.state.controller.api_keys[ModelProvider.OPENAI].get_secret_value()
            == "private-fixture-value"
        )
        for path in tmp_path.rglob("*.json"):
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


def test_custom_endpoint_does_not_inherit_default_provider_key(tmp_path):
    from pydantic import SecretStr

    from abaqus_sim_agent.web.contracts import WebError

    app, _ = configured_app(tmp_path)
    controller = app.state.controller
    controller.configure_model_key(ModelProvider.OPENAI, SecretStr("official-secret"))
    with pytest.raises(WebError, match="自定义 API"):
        controller._model_key(ModelProvider.OPENAI, "https://other.invalid/v1")
    controller.configure_model_key(
        ModelProvider.OPENAI, SecretStr("custom-secret"), "https://other.invalid/v1"
    )
    assert (
        controller._model_key(ModelProvider.OPENAI).get_secret_value()
        == "official-secret"
    )
    assert (
        controller._model_key(
            ModelProvider.OPENAI, "https://other.invalid/v1"
        ).get_secret_value()
        == "custom-secret"
    )
    with pytest.raises(WebError, match="自定义 API"):
        controller._model_key(ModelProvider.OPENAI, "https://third.invalid/v1")


@pytest.mark.asyncio
async def test_codex_login_uses_framework_callback_and_keeps_refreshable_memory_credentials(
    tmp_path, monkeypatch
):
    from pydantic_ai.providers import openai_codex

    app, _ = configured_app(tmp_path)
    expected = object()

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
    source = app.state.controller.model_credentials[ModelProvider.OPENAI_CODEX]
    assert await source.load() is expected
    refreshed = object()
    await source.save(refreshed)
    assert await source.load() is refreshed
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
    assert not app.state.controller.model_credentials
