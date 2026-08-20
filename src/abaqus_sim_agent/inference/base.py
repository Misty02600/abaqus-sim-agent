from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelMessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class StructuredOutputMethod(StrEnum):
    JSON_SCHEMA = "json_schema"
    FUNCTION_CALLING = "function_calling"
    JSON_MODE = "json_mode"


class ModelFailureKind(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_REQUEST = "invalid_request"
    REFUSAL = "refusal"
    OUTPUT_TRUNCATED = "output_truncated"
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    UNKNOWN = "unknown"


class ModelMessage(_FrozenModel):
    role: ModelMessageRole
    content: str = Field(min_length=1)


class ModelProfile(_FrozenModel):
    profile_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    api_family: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    connection_revision: str = Field(min_length=1)
    settings_revision: str = Field(min_length=1)
    structured_output_method: StructuredOutputMethod
    strict_structured_output: bool | None


class ModelUsage(_FrozenModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class StructuredModelRequest[OutputT: BaseModel]:
    """一次运行期结构化模型请求，不属于可持久化的 Graph State。"""

    messages: tuple[ModelMessage, ...]
    output_model: type[OutputT]

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("a structured model request requires at least one message")


@dataclass(frozen=True, slots=True)
class StructuredModelResult[OutputT: BaseModel]:
    output: OutputT
    profile: ModelProfile
    usage: ModelUsage | None = None
    provider_response_id: str | None = None
    resolved_model: str | None = None


class ModelInvocationError(RuntimeError):
    """模型边界的归一化失败；是否重试仍由上层 Agent 子图决定。"""

    def __init__(
        self,
        message: str,
        *,
        kind: ModelFailureKind,
        retryable: bool,
        profile: ModelProfile,
        usage: ModelUsage | None = None,
        provider_response_id: str | None = None,
        resolved_model: str | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.profile = profile
        self.usage = usage
        self.provider_response_id = provider_response_id
        self.resolved_model = resolved_model


class StructuredModelPort(Protocol):
    """专业 Agent 使用的窄模型端口，不拥有工具副作用或重试循环。"""

    @property
    def profile(self) -> ModelProfile: ...

    async def generate_structured[OutputT: BaseModel](
        self,
        request: StructuredModelRequest[OutputT],
    ) -> StructuredModelResult[OutputT]: ...
