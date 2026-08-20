import warnings
from collections.abc import Mapping, Sequence
from math import isfinite
from urllib.parse import urlsplit

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr, ValidationError

from abaqus_sim_agent.inference.base import (
    ModelFailureKind,
    ModelInvocationError,
    ModelMessage,
    ModelMessageRole,
    ModelProfile,
    ModelUsage,
    StructuredModelRequest,
    StructuredModelResult,
)

OPENAI_CHAT_COMPLETIONS_API_FAMILY = "openai_chat_completions"


class LangChainStructuredModelAdapter:
    """把 LangChain chat model 收窄为项目的一次性结构化调用端口。"""

    def __init__(self, model: BaseChatModel, profile: ModelProfile) -> None:
        configured_strategy = getattr(type(model), "with_structured_output", None)
        uses_base_strategy = configured_strategy is BaseChatModel.with_structured_output
        if uses_base_strategy and (
            profile.structured_output_method.value != "function_calling"
            or profile.strict_structured_output is not None
        ):
            raise ValueError(
                "the chat model does not implement the configured structured-output "
                "method and strictness"
            )
        self._model = model
        self._profile = profile

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    async def generate_structured[OutputT: BaseModel](
        self,
        request: StructuredModelRequest[OutputT],
    ) -> StructuredModelResult[OutputT]:
        messages = _to_langchain_messages(request.messages)
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "error",
                    message=r".*Overriding to method=.*function_calling.*",
                    category=UserWarning,
                )
                runnable = self._model.with_structured_output(
                    request.output_model,
                    method=self.profile.structured_output_method.value,
                    include_raw=True,
                    strict=self.profile.strict_structured_output,
                )
        except (NotImplementedError, UserWarning, ValueError) as exc:
            raise ModelInvocationError(
                "the configured model profile cannot honor this structured request",
                kind=ModelFailureKind.INVALID_REQUEST,
                retryable=False,
                profile=self.profile,
            ) from exc

        try:
            response = await runnable.ainvoke(messages)
        except (OutputParserException, ValidationError) as exc:
            raise ModelInvocationError(
                "the provider returned invalid structured output",
                kind=ModelFailureKind.INVALID_STRUCTURED_OUTPUT,
                retryable=True,
                profile=self.profile,
            ) from exc
        except Exception as exc:
            kind, retryable, message = _classify_provider_error(exc)
            usage, response_id, resolved_model = _extract_provider_error_evidence(exc)
            raise ModelInvocationError(
                message,
                kind=kind,
                retryable=retryable,
                profile=self.profile,
                usage=usage,
                provider_response_id=response_id,
                resolved_model=resolved_model,
            ) from exc

        parsed, raw, parsing_error = _split_structured_response(response)
        usage = _extract_usage(raw)
        response_id = _extract_provider_response_id(raw)
        resolved_model = _extract_resolved_model(raw)

        raw_failure = _classify_raw_response(raw)
        if raw_failure is not None:
            kind, retryable, message = raw_failure
            failure = ModelInvocationError(
                message,
                kind=kind,
                retryable=retryable,
                profile=self.profile,
                usage=usage,
                provider_response_id=response_id,
                resolved_model=resolved_model,
            )
            if isinstance(parsing_error, BaseException):
                raise failure from parsing_error
            raise failure

        if _is_model_refusal(parsing_error):
            failure = ModelInvocationError(
                "the model refused the structured request",
                kind=ModelFailureKind.REFUSAL,
                retryable=False,
                profile=self.profile,
                usage=usage,
                provider_response_id=response_id,
                resolved_model=resolved_model,
            )
            if isinstance(parsing_error, BaseException):
                raise failure from parsing_error
            raise failure

        if parsing_error is not None or parsed is None:
            failure = ModelInvocationError(
                "the provider returned invalid structured output",
                kind=ModelFailureKind.INVALID_STRUCTURED_OUTPUT,
                retryable=True,
                profile=self.profile,
                usage=usage,
                provider_response_id=response_id,
                resolved_model=resolved_model,
            )
            if isinstance(parsing_error, BaseException):
                raise failure from parsing_error
            raise failure

        try:
            output = _validate_output(request.output_model, parsed)
        except ValidationError as exc:
            raise ModelInvocationError(
                "the provider returned invalid structured output",
                kind=ModelFailureKind.INVALID_STRUCTURED_OUTPUT,
                retryable=True,
                profile=self.profile,
                usage=usage,
                provider_response_id=response_id,
                resolved_model=resolved_model,
            ) from exc

        return StructuredModelResult(
            output=output,
            profile=self.profile,
            usage=usage,
            provider_response_id=response_id,
            resolved_model=resolved_model,
        )


def create_chat_openai_model(
    profile: ModelProfile,
    *,
    api_key: SecretStr,
    base_url: str,
    timeout_seconds: float = 60.0,
    max_output_tokens: int | None = None,
) -> ChatOpenAI:
    """创建显式使用 Chat Completions、且关闭 SDK 隐藏重试的模型。

    Args:
        profile: 非秘密模型身份与调用策略。
        api_key: 由 Composition Root 从环境或 secret provider 注入的凭据。
        base_url: Composition Root 已解析的 OpenAI-compatible endpoint；不写入模型 Profile。
        timeout_seconds: 单次 provider 请求超时。
        max_output_tokens: provider 侧单次最大输出 token，`None` 表示不额外设置。

    Returns:
        尚未发起网络请求的 LangChain `ChatOpenAI` 实例。

    Raises:
        ValueError: Profile 的 API family 不匹配，或数值配置无效。
    """
    if profile.api_family != OPENAI_CHAT_COMPLETIONS_API_FAMILY:
        raise ValueError("ChatOpenAI requires the openai_chat_completions API family")
    resolved_base_url = base_url.strip()
    if not resolved_base_url:
        raise ValueError("base_url must not be empty")
    try:
        parsed_base_url = urlsplit(resolved_base_url)
        has_hostname = parsed_base_url.hostname is not None
        _ = parsed_base_url.port
    except ValueError as exc:
        raise ValueError("base_url must be an absolute HTTP(S) URL") from exc
    if parsed_base_url.scheme not in {"http", "https"} or not has_hostname:
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    if parsed_base_url.username is not None or parsed_base_url.password is not None:
        raise ValueError("base_url must not include credentials")
    if not isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and greater than zero")
    if max_output_tokens is not None and max_output_tokens <= 0:
        raise ValueError("max_output_tokens must be greater than zero")
    if not api_key.get_secret_value():
        raise ValueError("api_key must not be empty")

    return ChatOpenAI(
        model=profile.model,
        api_key=api_key,
        base_url=resolved_base_url,
        timeout=timeout_seconds,
        max_retries=0,
        max_completion_tokens=max_output_tokens,
        use_responses_api=False,
    )


def _to_langchain_messages(messages: Sequence[ModelMessage]) -> list[BaseMessage]:
    converted: list[BaseMessage] = []
    for message in messages:
        if message.role is ModelMessageRole.SYSTEM:
            converted.append(SystemMessage(content=message.content))
        elif message.role is ModelMessageRole.USER:
            converted.append(HumanMessage(content=message.content))
        else:
            converted.append(AIMessage(content=message.content))
    return converted


def _split_structured_response(
    response: object,
) -> tuple[object | None, AIMessage | None, object | None]:
    if isinstance(response, BaseModel):
        return response, None, None
    if not isinstance(response, Mapping):
        return None, None, TypeError("unexpected structured response container")

    raw_value = response.get("raw")
    raw = raw_value if isinstance(raw_value, AIMessage) else None
    return response.get("parsed"), raw, response.get("parsing_error")


def _validate_output[OutputT: BaseModel](
    output_model: type[OutputT],
    parsed: object,
) -> OutputT:
    if isinstance(parsed, BaseModel):
        return output_model.model_validate(parsed.model_dump(mode="python"))
    return output_model.model_validate(parsed)


def _extract_usage(message: AIMessage | None) -> ModelUsage | None:
    if message is None or message.usage_metadata is None:
        return None
    metadata = message.usage_metadata
    try:
        return ModelUsage(
            input_tokens=metadata["input_tokens"],
            output_tokens=metadata["output_tokens"],
            total_tokens=metadata["total_tokens"],
        )
    except (KeyError, TypeError, ValidationError):
        return None


def _extract_resolved_model(message: AIMessage | None) -> str | None:
    if message is None:
        return None
    for key in ("model_name", "model"):
        value = message.response_metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_provider_response_id(message: AIMessage | None) -> str | None:
    if message is None:
        return None
    value = message.response_metadata.get("id")
    return value if isinstance(value, str) and value else None


def _classify_raw_response(
    message: AIMessage | None,
) -> tuple[ModelFailureKind, bool, str] | None:
    if message is None:
        return None
    if message.additional_kwargs.get("refusal"):
        return ModelFailureKind.REFUSAL, False, "the model refused the request"

    finish_reason_value = message.response_metadata.get("finish_reason")
    finish_reason = (
        finish_reason_value.lower() if isinstance(finish_reason_value, str) else None
    )
    if finish_reason == "content_filter":
        return ModelFailureKind.REFUSAL, False, "the model refused the request"
    if finish_reason == "length":
        return (
            ModelFailureKind.OUTPUT_TRUNCATED,
            True,
            "the model output reached its configured length limit",
        )
    return None


def _classify_provider_error(
    error: Exception,
) -> tuple[ModelFailureKind, bool, str]:
    status_code = _status_code(error)
    error_name = type(error).__name__.lower()

    if _is_model_refusal(error):
        return ModelFailureKind.REFUSAL, False, "the model refused the request"
    if error_name == "lengthfinishreasonerror":
        return (
            ModelFailureKind.OUTPUT_TRUNCATED,
            True,
            "the model output reached its configured length limit",
        )
    if status_code in {401, 403} or "authentication" in error_name:
        return ModelFailureKind.AUTHENTICATION, False, "model authentication failed"
    if status_code == 429 or "ratelimit" in error_name:
        return ModelFailureKind.RATE_LIMIT, True, "model provider rate limit reached"
    if status_code in {408, 504} or "timeout" in error_name:
        return ModelFailureKind.TIMEOUT, True, "model provider request timed out"
    if status_code is not None and status_code >= 500:
        return (
            ModelFailureKind.PROVIDER_UNAVAILABLE,
            True,
            "model provider is temporarily unavailable",
        )
    if "connection" in error_name:
        return (
            ModelFailureKind.PROVIDER_UNAVAILABLE,
            True,
            "model provider is temporarily unavailable",
        )
    if status_code is not None and 400 <= status_code < 500:
        return (
            ModelFailureKind.INVALID_REQUEST,
            False,
            "model provider rejected request",
        )
    return ModelFailureKind.UNKNOWN, False, "model provider request failed"


def _is_model_refusal(error: object | None) -> bool:
    return error is not None and type(error).__name__ in {
        "ContentFilterFinishReasonError",
        "OpenAIRefusalError",
    }


def _extract_provider_error_evidence(
    error: Exception,
) -> tuple[ModelUsage | None, str | None, str | None]:
    completion = getattr(error, "completion", None)
    if completion is None:
        return None, None, None

    usage_value = _read_field(completion, "usage")
    usage: ModelUsage | None = None
    if usage_value is not None:
        try:
            usage = ModelUsage.model_validate(
                {
                    "input_tokens": _read_field(usage_value, "prompt_tokens"),
                    "output_tokens": _read_field(usage_value, "completion_tokens"),
                    "total_tokens": _read_field(usage_value, "total_tokens"),
                }
            )
        except ValidationError:
            pass

    response_id_value = _read_field(completion, "id")
    resolved_model_value = _read_field(completion, "model")
    response_id = (
        response_id_value
        if isinstance(response_id_value, str) and response_id_value
        else None
    )
    resolved_model = (
        resolved_model_value
        if isinstance(resolved_model_value, str) and resolved_model_value
        else None
    )
    return usage, response_id, resolved_model


def _read_field(value: object, field: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(field)
    return getattr(value, field, None)


def _status_code(error: Exception) -> int | None:
    value = getattr(error, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(error, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None
