import json
from typing import get_args

import httpx2
import pytest
from pydantic import SecretStr
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import (
    ModelRequestParameters,
    OpenAIChatCompatibleProvider,
    known_model_names,
)

from abaqus_sim_agent.coding import providers
from abaqus_sim_agent.coding.model import take_gateway_api_keys_from_environment
from abaqus_sim_agent.coding.model_connections import create_model
from abaqus_sim_agent.coding.preferences import RunPreferences, model_execution
from abaqus_sim_agent.coding.providers import ModelProvider, ProviderUnavailable


def test_catalog_does_not_depend_on_sdk_or_credentials(monkeypatch):
    monkeypatch.setattr(providers, "find_spec", lambda _: None)
    entries = providers.provider_catalog()
    ids = {p.id for p in entries}
    expected = {
        name.split(":", 1)[0] for name in known_model_names() if ":" in name
    } - {"xai"}
    assert expected <= ids
    assert set(get_args(OpenAIChatCompatibleProvider.__value__)) <= ids
    assert {
        "openrouter",
        "ollama",
        "openai-codex",
        "github-copilot",
        "opencode-go",
        "opencode-zen",
    } <= ids
    assert "xai" not in ids
    with pytest.raises(ProviderUnavailable, match="缺少依赖：anthropic"):
        providers.require_provider_dependencies(ModelProvider.ANTHROPIC)


def test_model_selection_rejects_missing_sdk_before_credentials(monkeypatch):
    monkeypatch.setattr(providers, "find_spec", lambda _: None)
    with pytest.raises(ProviderUnavailable, match="缺少依赖"):
        model_execution(
            RunPreferences(
                model_provider=ModelProvider.ANTHROPIC, model_name="claude-sonnet-4-5"
            )
        )


def test_model_selection_preserves_native_window_and_explicit_unknown_window():
    native = model_execution(
        RunPreferences(
            model_provider=ModelProvider.ANTHROPIC, model_name="claude-sonnet-4-5"
        )
    )
    assert native.context_window == 200_000
    unknown = RunPreferences(
        model_provider=ModelProvider.OPENAI, model_name="my-deployment"
    )
    with pytest.raises(ValueError, match="context_window"):
        model_execution(unknown)
    configured = model_execution(unknown.model_copy(update={"context_window": 128_000}))
    assert configured.context_window == 128_000
    with pytest.raises(ValueError, match="模型名称"):
        model_execution(RunPreferences(model_provider=ModelProvider.ANTHROPIC))


def test_native_retry_defaults_are_explicit_and_cannot_silently_ignore_overrides():
    configuration = model_execution(
        RunPreferences(
            model_provider=ModelProvider.MISTRAL,
            model_name="custom",
            context_window=32_000,
        )
    )
    assert configuration.http_retries is None
    with pytest.raises(ValueError, match="SDK 默认重试"):
        model_execution(
            RunPreferences(
                model_provider=ModelProvider.MISTRAL,
                model_name="custom",
                context_window=32_000,
                http_retries=2,
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "name"),
    [
        (ModelProvider.OFFICIAL, "deepseek-flash"),
        (ModelProvider.OPENCODE_GO, "deepseek-v4.1-flash"),
    ],
)
async def test_current_deepseek_aliases_keep_the_native_thinking_profile(
    provider, name
):
    configuration = model_execution(
        RunPreferences(model_provider=provider, model_name=name)
    )
    model, client = create_model(
        gateway=provider,
        model_name=name,
        api_key=SecretStr("fixture"),
        execution_config=configuration,
        conversation_id="fixture" if provider is ModelProvider.OPENCODE_GO else None,
    )
    try:
        assert model.context_window == 1_000_000
        assert model.profile["openai_reasoning_enabled_by_default"] is True
        assert model.profile["openai_chat_send_back_thinking_parts"] == "field"
    finally:
        await client.close()


def test_all_api_credentials_are_captured_and_removed():
    environment = {
        "OPENAI_API_KEY": "openai-secret",
        "ANTHROPIC_API_KEY": "anthropic-secret",
        "GEMINI_API_KEY": "google-secret",
        "HF_TOKEN": "hf-secret",
        "PATH": "retained",
    }
    keys = take_gateway_api_keys_from_environment(environment)
    assert keys[ModelProvider.OPENAI].get_secret_value() == "openai-secret"
    assert keys[ModelProvider.OPENAI_CHAT] == keys[ModelProvider.OPENAI]
    assert keys[ModelProvider.GOOGLE].get_secret_value() == "google-secret"
    assert environment == {"PATH": "retained"}


def test_saved_window_is_not_reinferred_when_metadata_changes(monkeypatch):
    from abaqus_sim_agent.coding import model_connections
    from abaqus_sim_agent.coding.context_policy import build_context_policy
    from abaqus_sim_agent.coding.model import ModelExecutionConfig

    configuration = model_execution(
        RunPreferences(
            model_provider=ModelProvider.ANTHROPIC, model_name="claude-sonnet-4-5"
        )
    )
    encoded = configuration.model_dump_json()
    monkeypatch.setattr(
        model_connections,
        "infer_model_profile",
        lambda _: {"context_window": 1_000_000},
    )
    restored = ModelExecutionConfig.model_validate_json(encoded)
    assert restored.context_window == 200_000
    policy = build_context_policy(context_window=restored.context_window)
    assert policy.context_window == 200_000
    assert policy.target_fraction == 0.9
    assert policy.target_tokens is None


def test_changed_host_endpoint_is_rejected_before_model_creation(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://original.invalid/v1")
    configuration = model_execution(
        RunPreferences(model_provider=ModelProvider.OPENAI, model_name="gpt-4.1")
    )
    monkeypatch.setenv("OPENAI_BASE_URL", "https://changed.invalid/v1")
    with pytest.raises(ValueError, match="环境配置已改变"):
        create_model(
            gateway=ModelProvider.OPENAI,
            model_name="gpt-4.1",
            api_key=SecretStr("fixture"),
            execution_config=configuration,
        )


def test_runtime_subprocess_does_not_inherit_cloud_credentials(monkeypatch):
    from abaqus_sim_agent.execution.environment import subprocess_environment

    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "token")
    monkeypatch.setenv("CO_API_KEY", "key")
    monkeypatch.setenv("SIMULIA_LICENSE_FILE", "license-location")
    env = subprocess_environment()
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "AWS_SESSION_TOKEN" not in env
    assert "CO_API_KEY" not in env
    assert env["SIMULIA_LICENSE_FILE"] == "license-location"


@pytest.mark.asyncio
async def test_cohere_client_closes_its_explicit_transport():
    import httpx

    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )
    _, client = create_model(
        gateway=ModelProvider.COHERE,
        model_name="command-r",
        api_key=SecretStr("fixture"),
        http_client=transport,
    )
    await client.close()
    assert transport.is_closed


@pytest.mark.parametrize(
    ("provider", "model_name", "path", "response"),
    [
        (
            ModelProvider.OPENAI_CHAT,
            "gpt-4.1",
            "/v1/chat/completions",
            {
                "id": "chat-1",
                "object": "chat.completion",
                "created": 0,
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                        "index": 0,
                    }
                ],
                "model": "gpt-4.1",
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 1,
                    "total_tokens": 5,
                },
            },
        ),
        (
            ModelProvider.OPENAI,
            "gpt-4.1",
            "/v1/responses",
            {
                "id": "resp-1",
                "created_at": 0,
                "object": "response",
                "status": "completed",
                "model": "gpt-4.1",
                "output": [
                    {
                        "type": "message",
                        "id": "msg-1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {"type": "output_text", "text": "ok", "annotations": []}
                        ],
                    }
                ],
                "usage": {"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
            },
        ),
        (
            ModelProvider.ANTHROPIC,
            "claude-sonnet-4-5",
            "/v1/messages",
            {
                "id": "msg-1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-5",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 1},
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_native_factory_uses_native_protocol_and_releases_transport(
    provider, model_name, path, response, mock_model_requests
):
    bodies = []

    def handle(request):
        assert request.url.path == path
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json=response)

    transport = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    configuration = model_execution(
        RunPreferences(
            model_provider=provider,
            model_name=model_name,
            model_base_url="https://model.invalid"
            if provider is ModelProvider.ANTHROPIC
            else "https://model.invalid/v1",
        )
    )
    model, client = create_model(
        gateway=provider,
        model_name=model_name,
        api_key=SecretStr("fixture"),
        execution_config=configuration,
        http_client=transport,
    )
    try:
        result = await model.request(
            [ModelRequest(parts=[UserPromptPart("hello")])],
            None,
            ModelRequestParameters(),
        )
        assert result.text == "ok"
        assert model.context_window == configuration.context_window
        assert "thinking" not in bodies[0]
        assert "reasoning_content" not in json.dumps(bodies[0])
    finally:
        await client.close()
    assert transport.is_closed
