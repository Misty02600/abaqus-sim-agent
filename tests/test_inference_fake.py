import pytest
from pydantic import BaseModel

from abaqus_sim_agent.inference import (
    ModelFailureKind,
    ModelInvocationError,
    ModelMessage,
    ModelMessageRole,
    ModelProfile,
    ModelUsage,
    ScriptedModelResponse,
    ScriptedStructuredModel,
    StructuredModelRequest,
    StructuredOutputMethod,
)


class Answer(BaseModel):
    value: str


def make_profile() -> ModelProfile:
    return ModelProfile(
        profile_id="default",
        provider="fake",
        model="scripted",
        api_family="in_process",
        endpoint_id="local-test",
        connection_revision="connection-v1",
        settings_revision="settings-v1",
        structured_output_method=StructuredOutputMethod.JSON_SCHEMA,
        strict_structured_output=True,
    )


def make_request() -> StructuredModelRequest[Answer]:
    return StructuredModelRequest(
        messages=(
            ModelMessage(role=ModelMessageRole.SYSTEM, content="Return an answer."),
            ModelMessage(role=ModelMessageRole.USER, content="Say ready."),
        ),
        output_model=Answer,
    )


def test_structured_request_requires_at_least_one_message() -> None:
    with pytest.raises(ValueError, match="at least one message"):
        StructuredModelRequest(messages=(), output_model=Answer)


@pytest.mark.asyncio
async def test_scripted_model_returns_typed_response_and_call_metadata() -> None:
    usage = ModelUsage(input_tokens=8, output_tokens=2, total_tokens=10)
    model = ScriptedStructuredModel(
        make_profile(),
        [
            ScriptedModelResponse(
                output=Answer(value="ready"),
                usage=usage,
                provider_response_id="response-1",
                resolved_model="scripted-v1",
            )
        ],
    )

    result = await model.generate_structured(make_request())

    assert result.output == Answer(value="ready")
    assert result.profile == model.profile
    assert result.usage == usage
    assert result.provider_response_id == "response-1"
    assert result.resolved_model == "scripted-v1"
    assert model.remaining == 0
    assert model.calls[0].output_model_name == "Answer"
    assert model.calls[0].messages == make_request().messages


@pytest.mark.asyncio
async def test_scripted_model_does_not_fallback_after_failure() -> None:
    profile = make_profile()
    failure = ModelInvocationError(
        "rate limited",
        kind=ModelFailureKind.RATE_LIMIT,
        retryable=True,
        profile=profile,
    )
    model = ScriptedStructuredModel(
        profile,
        [failure, ScriptedModelResponse(output=Answer(value="unused"))],
    )

    with pytest.raises(ModelInvocationError) as exc_info:
        await model.generate_structured(make_request())

    assert exc_info.value is failure
    assert model.remaining == 1
    assert len(model.calls) == 1
