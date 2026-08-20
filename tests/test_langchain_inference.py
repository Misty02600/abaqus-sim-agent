from collections.abc import Sequence
from typing import cast
from unittest.mock import MagicMock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langchain_openai.chat_models.base import OpenAIRefusalError
from openai import LengthFinishReasonError
from openai.types.chat import ChatCompletion
from openai.types.completion_usage import CompletionUsage
from pydantic import BaseModel, SecretStr

from abaqus_sim_agent.inference import (
    LangChainStructuredModelAdapter,
    ModelFailureKind,
    ModelInvocationError,
    ModelMessage,
    ModelMessageRole,
    ModelProfile,
    StructuredModelRequest,
    StructuredOutputMethod,
    create_chat_openai_model,
)


class Answer(BaseModel):
    value: str


def make_profile(*, api_family: str = "openai_chat_completions") -> ModelProfile:
    return ModelProfile(
        profile_id="default",
        provider="openai-compatible",
        model="requested-model",
        api_family=api_family,
        endpoint_id="test-endpoint",
        connection_revision="connection-v1",
        settings_revision="settings-v1",
        structured_output_method=StructuredOutputMethod.JSON_SCHEMA,
        strict_structured_output=True,
    )


def make_request() -> StructuredModelRequest[Answer]:
    return StructuredModelRequest(
        messages=(
            ModelMessage(role=ModelMessageRole.SYSTEM, content="Return JSON."),
            ModelMessage(role=ModelMessageRole.ASSISTANT, content="Previous draft."),
            ModelMessage(role=ModelMessageRole.USER, content="Say ready."),
        ),
        output_model=Answer,
    )


def make_adapter(
    invoke: object,
    *,
    profile: ModelProfile | None = None,
) -> tuple[LangChainStructuredModelAdapter, MagicMock]:
    model = MagicMock(spec=BaseChatModel)
    model.with_structured_output.return_value = RunnableLambda(invoke)
    adapter = LangChainStructuredModelAdapter(
        cast(BaseChatModel, model),
        profile or make_profile(),
    )
    return adapter, model


@pytest.mark.asyncio
async def test_langchain_adapter_preserves_typed_output_and_usage() -> None:
    captured: list[Sequence[BaseMessage]] = []
    raw = AIMessage(
        content="",
        id="langchain-message-1",
        response_metadata={
            "id": "response-1",
            "model_name": "resolved-model",
        },
        usage_metadata={
            "input_tokens": 12,
            "output_tokens": 3,
            "total_tokens": 15,
        },
    )

    def invoke(messages: Sequence[BaseMessage]) -> dict[str, object]:
        captured.append(messages)
        return {
            "raw": raw,
            "parsed": Answer(value="ready"),
            "parsing_error": None,
        }

    adapter, model = make_adapter(invoke)

    result = await adapter.generate_structured(make_request())

    assert result.output == Answer(value="ready")
    assert result.provider_response_id == "response-1"
    assert result.resolved_model == "resolved-model"
    assert result.usage is not None
    assert result.usage.total_tokens == 15
    model.with_structured_output.assert_called_once_with(
        Answer,
        method="json_schema",
        include_raw=True,
        strict=True,
    )
    assert isinstance(captured[0][0], SystemMessage)
    assert isinstance(captured[0][1], AIMessage)
    assert isinstance(captured[0][2], HumanMessage)


@pytest.mark.asyncio
async def test_langchain_adapter_keeps_response_evidence_on_parse_failure() -> None:
    raw = AIMessage(
        content="not-json",
        id="langchain-message-invalid",
        response_metadata={
            "id": "response-invalid",
            "model_name": "resolved-model",
        },
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 2,
            "total_tokens": 12,
        },
    )

    def invoke(_: Sequence[BaseMessage]) -> dict[str, object]:
        return {
            "raw": raw,
            "parsed": None,
            "parsing_error": ValueError("invalid JSON"),
        }

    adapter, _ = make_adapter(invoke)

    with pytest.raises(ModelInvocationError) as exc_info:
        await adapter.generate_structured(make_request())

    failure = exc_info.value
    assert failure.kind is ModelFailureKind.INVALID_STRUCTURED_OUTPUT
    assert failure.retryable is True
    assert failure.provider_response_id == "response-invalid"
    assert failure.resolved_model == "resolved-model"
    assert failure.usage is not None
    assert failure.usage.total_tokens == 12


@pytest.mark.asyncio
async def test_langchain_adapter_does_not_retry_model_refusal() -> None:
    refusal = OpenAIRefusalError("cannot comply")
    raw = AIMessage(
        content="",
        response_metadata={
            "id": "response-refusal",
            "model_name": "resolved-model",
        },
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 1,
            "total_tokens": 11,
        },
    )

    def invoke(_: Sequence[BaseMessage]) -> dict[str, object]:
        return {"raw": raw, "parsed": None, "parsing_error": refusal}

    adapter, _ = make_adapter(invoke)

    with pytest.raises(ModelInvocationError) as exc_info:
        await adapter.generate_structured(make_request())

    failure = exc_info.value
    assert failure.kind is ModelFailureKind.REFUSAL
    assert failure.retryable is False
    assert failure.provider_response_id == "response-refusal"
    assert failure.resolved_model == "resolved-model"
    assert failure.__cause__ is refusal


@pytest.mark.asyncio
async def test_langchain_adapter_preserves_truncated_completion_evidence() -> None:
    completion = ChatCompletion(
        id="response-truncated",
        choices=[],
        created=0,
        model="resolved-model",
        object="chat.completion",
        usage=CompletionUsage(
            prompt_tokens=10,
            completion_tokens=4,
            total_tokens=14,
        ),
    )
    provider_error = LengthFinishReasonError(completion=completion)

    def invoke(_: Sequence[BaseMessage]) -> dict[str, object]:
        raise provider_error

    adapter, _ = make_adapter(invoke)

    with pytest.raises(ModelInvocationError) as exc_info:
        await adapter.generate_structured(make_request())

    failure = exc_info.value
    assert failure.kind is ModelFailureKind.OUTPUT_TRUNCATED
    assert failure.retryable is True
    assert failure.provider_response_id == "response-truncated"
    assert failure.resolved_model == "resolved-model"
    assert failure.usage is not None
    assert failure.usage.total_tokens == 14
    assert failure.__cause__ is provider_error


@pytest.mark.asyncio
async def test_function_calling_treats_content_filter_as_refusal() -> None:
    raw = AIMessage(
        content="",
        response_metadata={
        "id": "response-terminal",
        "model_name": "resolved-model",
            "finish_reason": "content_filter",
        },
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 1,
            "total_tokens": 11,
        },
    )

    def invoke(_: Sequence[BaseMessage]) -> dict[str, object]:
        return {"raw": raw, "parsed": None, "parsing_error": None}

    profile = make_profile().model_copy(
        update={
            "structured_output_method": StructuredOutputMethod.FUNCTION_CALLING,
        }
    )
    adapter, _ = make_adapter(invoke, profile=profile)

    with pytest.raises(ModelInvocationError) as exc_info:
        await adapter.generate_structured(make_request())

    failure = exc_info.value
    assert failure.kind is ModelFailureKind.REFUSAL
    assert failure.retryable is False
    assert failure.provider_response_id == "response-terminal"
    assert failure.resolved_model == "resolved-model"
    assert failure.usage is not None
    assert failure.usage.total_tokens == 11


class ProviderStatusError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"provider status {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize(
    ("provider_error", "expected_kind", "expected_retryable"),
    [
        (ProviderStatusError(401), ModelFailureKind.AUTHENTICATION, False),
        (ProviderStatusError(400), ModelFailureKind.INVALID_REQUEST, False),
        (ProviderStatusError(429), ModelFailureKind.RATE_LIMIT, True),
        (ProviderStatusError(503), ModelFailureKind.PROVIDER_UNAVAILABLE, True),
        (TimeoutError(), ModelFailureKind.TIMEOUT, True),
    ],
)
@pytest.mark.asyncio
async def test_langchain_adapter_classifies_failure_without_retrying(
    provider_error: Exception,
    expected_kind: ModelFailureKind,
    expected_retryable: bool,
) -> None:
    calls = 0

    def invoke(_: Sequence[BaseMessage]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise provider_error

    adapter, _ = make_adapter(invoke)

    with pytest.raises(ModelInvocationError) as exc_info:
        await adapter.generate_structured(make_request())

    assert exc_info.value.kind is expected_kind
    assert exc_info.value.retryable is expected_retryable
    assert calls == 1


def test_chat_openai_factory_disables_implicit_transport_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OPENAI_API_BASE",
        "https://implicit.example.invalid/v1",
    )
    model = create_chat_openai_model(
        make_profile(),
        api_key=SecretStr("not-a-real-key"),
        base_url="https://example.invalid/v1",
        timeout_seconds=12.5,
        max_output_tokens=321,
    )

    assert model.model_name == "requested-model"
    assert model.openai_api_base == "https://example.invalid/v1"
    assert model.max_retries == 0
    assert model.use_responses_api is False
    assert model.request_timeout == 12.5
    assert model.max_tokens == 321
    assert "not-a-real-key" not in repr(model)


def test_chat_openai_factory_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="openai_chat_completions"):
        create_chat_openai_model(
            make_profile(api_family="anthropic_messages"),
            api_key=SecretStr("not-a-real-key"),
            base_url="https://example.invalid/v1",
        )

    with pytest.raises(ValueError, match=r"absolute HTTP\(S\) URL"):
        create_chat_openai_model(
            make_profile(),
            api_key=SecretStr("not-a-real-key"),
            base_url="file:///tmp/model",
        )


def test_adapter_rejects_unimplemented_structured_output_strategy() -> None:
    model = FakeListChatModel(responses=["unused"])

    with pytest.raises(ValueError, match="structured-output"):
        LangChainStructuredModelAdapter(model, make_profile())
