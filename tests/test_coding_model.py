import asyncio
import hashlib
import json

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

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
    new_run_execution_config,
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
        assert model.profile["openai_chat_send_back_thinking_parts"] == "field"
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


def test_go_mapping_preserves_historical_thinking_and_tool_history() -> None:
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
    assert go_assistant["reasoning_content"] == "private reasoning"
    assert go_assistant["tool_calls"][0]["id"] == call_id
    assert [message["role"] for message in go_messages] == [
        "assistant",
        "tool",
        "user",
    ]
    assert zen_assistant["reasoning_content"] == "private reasoning"


@pytest.mark.asyncio
async def test_go_http_replays_thinking_after_tool_return_and_json_history_restore(
    tmp_path, monkeypatch
):
    bodies = []

    def response(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "high"
        assert body["tools"]
        assert "max_tokens" not in body
        assert request.headers[OPENCODE_SESSION_HEADER]
        assert request.headers["User-Agent"] == APPLICATION_USER_AGENT
        assistants = [m for m in body["messages"] if m["role"] == "assistant"]
        expected_reasoning = [
            f"fixture reasoning {index}" for index in range(1, len(bodies))
        ]
        if [m.get("reasoning_content") for m in assistants] != expected_reasoning:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "type": "invalid_request_error",
                        "message": "The reasoning_content in the thinking mode must be passed back to the API.",
                    }
                },
            )
        if len(bodies) > 1:
            assert assistants[0]["tool_calls"][0]["id"] == "tool-1"
            returned = [m for m in body["messages"] if m["role"] == "tool"]
            assert returned[0]["tool_call_id"] == "tool-1"
            assert returned[0]["content"] == "fixture file contents"
        message = {
            "role": "assistant",
            "content": "fixture answer",
            "reasoning_content": f"fixture reasoning {len(bodies)}",
        }
        if len(bodies) == 1:
            message.update(
                content=None,
                tool_calls=[
                    {
                        "id": "tool-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"input.txt"}',
                        },
                    }
                ],
            )
        return httpx.Response(
            200,
            json={
                "id": f"fixture-{len(bodies)}",
                "object": "chat.completion",
                "created": 0,
                "model": DEEPSEEK_V4_FLASH,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if len(bodies) == 1 else "stop",
                        "message": message,
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 5,
                    "total_tokens": 25,
                },
            },
        )

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.model.AsyncOpenAI",
        lambda **kwargs: AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)),
        ),
    )
    config = new_run_execution_config(
        model_provider=DeepSeekGateway.OPENCODE_GO, model_name=DEEPSEEK_V4_FLASH
    )
    model, client = create_deepseek_chat_model(
        gateway=DeepSeekGateway.OPENCODE_GO,
        model_name=DEEPSEEK_V4_FLASH,
        api_key=SecretStr("fixture-key"),
        conversation_id="persisted-thinking-fixture",
        execution_config=config,
    )
    parameters = ModelRequestParameters(
        function_tools=[
            ToolDefinition(
                name="read_file",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            )
        ]
    )
    try:
        history = [ModelRequest(parts=[UserPromptPart("Read input.txt")])]
        first = await model.request(history, None, parameters)
        assert any(isinstance(p, ThinkingPart) for p in first.parts)
        history.extend(
            [
                first,
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            "read_file", "fixture file contents", tool_call_id="tool-1"
                        )
                    ]
                ),
            ]
        )
        file = tmp_path / "history.json"
        file.write_bytes(ModelMessagesTypeAdapter.dump_json(history))
        restored = ModelMessagesTypeAdapter.validate_json(file.read_bytes())
        second = await model.request(restored, None, parameters)
        restored.extend(
            [
                second,
                ModelRequest(parts=[UserPromptPart("Continue with the next task")]),
            ]
        )
        # Include thinking from a response without a tool call in subsequent tool-enabled requests too.
        await model.request(restored, None, parameters)
    finally:
        await client.close()
    assert len(bodies) == 3


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
