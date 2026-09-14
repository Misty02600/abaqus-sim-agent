import asyncio
import hashlib

import pytest
from pydantic import SecretStr
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters

from abaqus_sim_agent.coding.model import (
    APPLICATION_USER_AGENT,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_V4_FLASH,
    DEFAULT_MODEL_HTTP_RETRIES,
    DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    OPENCODE_GO_BASE_URL,
    OPENCODE_SESSION_HEADER,
    OPENCODE_SESSION_VERSION,
    OPENCODE_ZEN_BASE_URL,
    DeepSeekGateway,
    OpenCodeGoChatModel,
    create_deepseek_chat_model,
    create_deepseek_v4_flash_model,
    gateway_api_key_env,
    take_gateway_api_key_from_environment,
)


def test_create_deepseek_model_has_pinned_identity_and_explicit_retries() -> None:
    model, client = create_deepseek_v4_flash_model(
        api_key=SecretStr("test-key"),
    )
    try:
        assert model.model_name == DEEPSEEK_V4_FLASH
        assert str(client.base_url).rstrip("/") == DEEPSEEK_BASE_URL
        assert client.max_retries == DEFAULT_MODEL_HTTP_RETRIES
        assert client.timeout == DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS
    finally:
        asyncio.run(client.close())


def test_create_deepseek_model_rejects_empty_key() -> None:
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY must not be empty"):
        create_deepseek_v4_flash_model(api_key=SecretStr(""))


@pytest.mark.parametrize(
    (
        "gateway",
        "model_name",
        "expected_base_url",
        "expected_key_env",
        "expected_system",
    ),
    [
        (
            DeepSeekGateway.OFFICIAL,
            DEEPSEEK_V4_FLASH,
            DEEPSEEK_BASE_URL,
            "DEEPSEEK_API_KEY",
            "deepseek",
        ),
        (
            DeepSeekGateway.OPENCODE_ZEN,
            "deepseek-v4-flash-free",
            OPENCODE_ZEN_BASE_URL,
            "OPENCODE_API_KEY",
            "opencode-zen",
        ),
        (
            DeepSeekGateway.OPENCODE_GO,
            DEEPSEEK_V4_FLASH,
            OPENCODE_GO_BASE_URL,
            "OPENCODE_API_KEY",
            "opencode-go",
        ),
    ],
)
def test_create_deepseek_chat_model_uses_explicit_gateway(
    gateway: DeepSeekGateway,
    model_name: str,
    expected_base_url: str,
    expected_key_env: str,
    expected_system: str,
) -> None:
    model, client = create_deepseek_chat_model(
        gateway=gateway,
        model_name=model_name,
        api_key=SecretStr("test-key"),
        conversation_id="gateway-test"
        if gateway is DeepSeekGateway.OPENCODE_GO
        else None,
    )
    try:
        assert model.model_name == model_name
        assert str(client.base_url).rstrip("/") == expected_base_url
        assert str(model.base_url).rstrip("/") == expected_base_url
        assert model.system == expected_system
        assert client.max_retries == DEFAULT_MODEL_HTTP_RETRIES
        assert client.timeout == DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS
        assert gateway_api_key_env(gateway) == expected_key_env
        assert model.profile["supports_thinking"] is True
        assert model.profile["openai_chat_thinking_field"] == "reasoning_content"
        expected_thinking_replay = (
            False if gateway is DeepSeekGateway.OPENCODE_GO else "field"
        )
        assert (
            model.profile["openai_chat_send_back_thinking_parts"]
            == expected_thinking_replay
        )
        assert model.profile["openai_supports_tool_choice_required"] is False
    finally:
        asyncio.run(client.close())


def test_take_gateway_key_removes_both_model_credentials() -> None:
    environ = {
        "DEEPSEEK_API_KEY": "direct-secret",
        "OPENCODE_API_KEY": "zen-secret",
        "UNRELATED": "keep",
    }

    captured = take_gateway_api_key_from_environment(
        DeepSeekGateway.OPENCODE_ZEN,
        environ,
    )

    assert captured.get_secret_value() == "zen-secret"
    assert "DEEPSEEK_API_KEY" not in environ
    assert "OPENCODE_API_KEY" not in environ
    assert environ["UNRELATED"] == "keep"


def test_deepseek_profile_rejects_other_model_families() -> None:
    with pytest.raises(ValueError, match="must start with `deepseek-`"):
        create_deepseek_chat_model(
            gateway=DeepSeekGateway.OPENCODE_ZEN,
            model_name="minimax-m3",
            api_key=SecretStr("test-key"),
        )


def test_http_retries_can_be_disabled_and_cannot_be_negative() -> None:
    _model, client = create_deepseek_chat_model(
        gateway=DeepSeekGateway.OPENCODE_GO,
        model_name=DEEPSEEK_V4_FLASH,
        api_key=SecretStr("test-key"),
        http_retries=0,
        conversation_id="retry-test",
    )
    try:
        assert client.max_retries == 0
    finally:
        asyncio.run(client.close())

    with pytest.raises(ValueError, match="greater than or equal to zero"):
        create_deepseek_chat_model(
            gateway=DeepSeekGateway.OPENCODE_GO,
            model_name=DEEPSEEK_V4_FLASH,
            api_key=SecretStr("test-key"),
            http_retries=-1,
        )


def test_go_mapping_omits_historical_thinking_but_preserves_tool_history() -> None:
    call_id = "call-clarification"
    history = [
        ModelResponse(
            parts=[
                ThinkingPart("private reasoning"),
                ToolCallPart(
                    "final_result_NeedsInputDecision",
                    {"status": "needs_input"},
                    tool_call_id=call_id,
                ),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    "final_result_NeedsInputDecision",
                    "The user supplied plate_thickness: 1 mm",
                    tool_call_id=call_id,
                ),
                UserPromptPart("Continue the same coding run."),
            ]
        ),
    ]
    parameters = ModelRequestParameters()
    go_model, go_client = create_deepseek_chat_model(
        gateway=DeepSeekGateway.OPENCODE_GO,
        model_name=DEEPSEEK_V4_FLASH,
        api_key=SecretStr("test-key"),
        conversation_id="history-test",
    )
    zen_model, zen_client = create_deepseek_chat_model(
        gateway=DeepSeekGateway.OPENCODE_ZEN,
        model_name=DEEPSEEK_V4_FLASH,
        api_key=SecretStr("test-key"),
    )
    try:
        go_messages = asyncio.run(go_model._map_messages(history, parameters))
        zen_messages = asyncio.run(zen_model._map_messages(history, parameters))
    finally:
        asyncio.run(go_client.close())
        asyncio.run(zen_client.close())

    go_assistant = next(
        message for message in go_messages if message["role"] == "assistant"
    )
    zen_assistant = next(
        message for message in zen_messages if message["role"] == "assistant"
    )
    assert "reasoning_content" not in go_assistant
    assert go_assistant["tool_calls"][0]["id"] == call_id
    assert [message["role"] for message in go_messages] == [
        "assistant",
        "tool",
        "user",
    ]
    assert zen_assistant["reasoning_content"] == "private reasoning"


def test_go_routing_headers_are_stable_host_owned_and_conversation_scoped() -> None:
    first, first_client = create_deepseek_chat_model(
        gateway=DeepSeekGateway.OPENCODE_GO,
        model_name=DEEPSEEK_V4_FLASH,
        api_key=SecretStr("test-key"),
        conversation_id="run-one",
    )
    resumed, resumed_client = create_deepseek_chat_model(
        gateway=DeepSeekGateway.OPENCODE_GO,
        model_name=DEEPSEEK_V4_FLASH,
        api_key=SecretStr("test-key"),
        conversation_id="run-one",
    )
    other, other_client = create_deepseek_chat_model(
        gateway=DeepSeekGateway.OPENCODE_GO,
        model_name=DEEPSEEK_V4_FLASH,
        api_key=SecretStr("test-key"),
        conversation_id="run-two",
    )
    try:
        assert isinstance(first, OpenCodeGoChatModel)
        expected = hashlib.sha256(
            f"{OPENCODE_SESSION_VERSION}:run-one".encode()
        ).hexdigest()
        assert first.opencode_session_id == resumed.opencode_session_id == expected
        assert other.opencode_session_id != expected
        settings = first.routing_settings({"max_tokens": 100})
        assert settings["extra_headers"] == {
            OPENCODE_SESSION_HEADER: expected,
            "User-Agent": APPLICATION_USER_AGENT,
        }
        assert settings["max_tokens"] == 100
        with pytest.raises(ValueError, match="host-owned"):
            first.routing_settings(
                {"extra_headers": {"X-OpenCode-Session": "override"}}
            )
        with pytest.raises(ValueError, match="host-owned"):
            first.routing_settings(
                {"extra_headers": {"user-agent": "impersonated-client"}}
            )
    finally:
        for client in (first_client, resumed_client, other_client):
            asyncio.run(client.close())


def test_go_requires_explicit_conversation_and_other_gateways_reject_it() -> None:
    with pytest.raises(ValueError, match="stable conversation_id"):
        create_deepseek_chat_model(
            gateway=DeepSeekGateway.OPENCODE_GO,
            model_name=DEEPSEEK_V4_FLASH,
            api_key=SecretStr("test-key"),
        )
    with pytest.raises(ValueError, match="only valid"):
        create_deepseek_chat_model(
            gateway=DeepSeekGateway.OPENCODE_ZEN,
            model_name=DEEPSEEK_V4_FLASH,
            api_key=SecretStr("test-key"),
            conversation_id="not-for-zen",
        )
