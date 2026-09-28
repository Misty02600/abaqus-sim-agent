from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncGenerator, MutableMapping
from contextlib import asynccontextmanager
from enum import StrEnum
from importlib.metadata import version
from typing import Any, Literal, cast

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.settings import ModelSettings

DEEPSEEK_V4_FLASH = "deepseek-v4-flash"
DEEPSEEK_V4_FLASH_CONTEXT_WINDOW = 1_000_000
DEFAULT_MODEL_HTTP_RETRIES = 4
DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS = 300.0
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"
OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
_MODEL_API_KEY_ENV_VARS = ("DEEPSEEK_API_KEY", "OPENCODE_API_KEY")
OPENCODE_SESSION_HEADER = "x-opencode-session"
OPENCODE_SESSION_VERSION = "abaqus-sim-agent-opencode-session-v1"
APPLICATION_USER_AGENT = f"abaqus-sim-agent/{version('abaqus-sim-agent')}"


class DeepSeekGateway(StrEnum):
    OFFICIAL = "deepseek"
    OPENCODE_ZEN = "opencode-zen"
    OPENCODE_GO = "opencode-go"


class ModelExecutionConfig(BaseModel):
    """宿主拥有的有效配置；不含凭据和任务预算，旧run显式使用provider_default。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    model_provider: DeepSeekGateway
    model_name: str = Field(min_length=1)
    http_retries: int = Field(default=DEFAULT_MODEL_HTTP_RETRIES, ge=0)
    http_timeout_seconds: float = Field(
        default=DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS, gt=0, allow_inf_nan=False
    )
    reasoning_policy: Literal["provider_default", "enabled_high"] = "provider_default"
    planning_enabled: bool = False
    interaction_policy: Literal["legacy", "workspace_facts_first"] = "legacy"

    @model_validator(mode="after")
    def compatible_policy(self):
        if not self.model_name.strip() or self.model_name != self.model_name.strip():
            raise ValueError("model_name must be nonblank and normalized")
        if self.reasoning_policy == "enabled_high" and (
            self.model_provider is not DeepSeekGateway.OPENCODE_GO
            or self.model_name != DEEPSEEK_V4_FLASH
        ):
            raise ValueError(
                "enabled_high is supported only for the selected Go/deepseek-v4-flash profile; start another run to change policy"
            )
        return self

    def model_settings(self) -> OpenAIChatModelSettings:
        if self.reasoning_policy == "provider_default":
            return {"timeout": self.http_timeout_seconds}
        return {
            "timeout": self.http_timeout_seconds,
            "extra_body": {"thinking": {"type": "enabled"}},
            "openai_reasoning_effort": "high",
        }

    def checked_settings(self, settings: ModelSettings | None) -> ModelSettings:
        """拒绝与run快照冲突的下游覆盖，再填充快照字段。

        Raises:
            ValueError: 推理、超时或输出限制覆盖与快照不符。
        """
        desired = self.model_settings()
        supplied = dict(settings or {})
        raw_extra = supplied.get("extra_body")
        if raw_extra is not None and not isinstance(raw_extra, dict):
            raise ValueError("extra_body override must be an object")
        extra: dict[str, Any] = dict(raw_extra or {})
        if any(
            k in supplied or k in extra for k in ("max_tokens", "max_completion_tokens")
        ):
            raise ValueError("run profile does not permit an extra response token cap")
        if "thinking" in supplied:
            raise ValueError(
                "unified thinking override conflicts with fixed reasoning policy"
            )
        for key in ("timeout", "openai_reasoning_effort"):
            if key in supplied and supplied[key] != desired.get(key):
                raise ValueError(f"{key} differs from the run configuration")
        expected_thinking = (
            {"type": "enabled"} if self.reasoning_policy == "enabled_high" else None
        )
        if "thinking" in extra and extra["thinking"] != expected_thinking:
            raise ValueError("thinking override differs from the run configuration")
        if "reasoning_effort" in extra and extra["reasoning_effort"] != desired.get(
            "openai_reasoning_effort"
        ):
            raise ValueError(
                "reasoning_effort override differs from the run configuration"
            )
        if self.reasoning_policy == "enabled_high":
            extra["thinking"] = expected_thinking
        supplied.update({k: v for k, v in desired.items() if k != "extra_body"})
        if extra:
            supplied["extra_body"] = extra
        return cast(ModelSettings, supplied)


def new_run_execution_config(
    *,
    model_provider: DeepSeekGateway,
    model_name: str,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
    http_timeout_seconds: float = DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    reasoning_policy: Literal["provider_default", "enabled_high"] | None = None,
    planning_enabled: bool = False,
) -> ModelExecutionConfig:
    policy = reasoning_policy or (
        "enabled_high"
        if model_provider is DeepSeekGateway.OPENCODE_GO
        and model_name == DEEPSEEK_V4_FLASH
        else "provider_default"
    )
    return ModelExecutionConfig(
        model_provider=model_provider,
        model_name=model_name,
        http_retries=http_retries,
        http_timeout_seconds=http_timeout_seconds,
        reasoning_policy=policy,
        planning_enabled=planning_enabled,
        interaction_policy="workspace_facts_first",
    )


class ConfiguredModel(WrapperModel):
    def __init__(self, model: Model, configuration: ModelExecutionConfig):
        super().__init__(model)
        self.configuration = configuration

    def prepare_request(self, model_settings, model_request_parameters):
        return self.wrapped.prepare_request(
            self.configuration.checked_settings(model_settings),
            model_request_parameters,
        )

    async def request(self, messages, model_settings, model_request_parameters):
        return await self.wrapped.request(
            messages,
            self.configuration.checked_settings(model_settings),
            model_request_parameters,
        )

    @asynccontextmanager
    async def request_stream(
        self, messages, model_settings, model_request_parameters, run_context=None
    ):
        async with self.wrapped.request_stream(
            messages,
            self.configuration.checked_settings(model_settings),
            model_request_parameters,
            run_context,
        ) as response:
            yield response


class OpenCodeZenDeepSeekProvider(DeepSeekProvider):
    @property
    def name(self) -> str:
        return DeepSeekGateway.OPENCODE_ZEN.value

    @property
    def base_url(self) -> str:
        return OPENCODE_ZEN_BASE_URL


class OpenCodeGoDeepSeekProvider(DeepSeekProvider):
    # Inherit DeepSeek's reasoning_content round-trip for tool-enabled requests.
    # https://api-docs.deepseek.com/guides/thinking_mode/
    @property
    def name(self) -> str:
        return DeepSeekGateway.OPENCODE_GO.value

    @property
    def base_url(self) -> str:
        return OPENCODE_GO_BASE_URL


class OpenCodeGoChatModel(OpenAIChatModel):
    def __init__(
        self,
        model_name: str,
        *,
        provider: DeepSeekProvider,
        conversation_id: str,
        profile: ModelProfile | None = None,
        settings: ModelSettings | None = None,
    ):
        super().__init__(
            model_name,
            provider=provider,
            profile=profile,
            settings=settings,
        )
        normalized = conversation_id.strip()
        if (
            not normalized
            or len(normalized) > 512
            or any(ord(char) < 32 for char in normalized)
        ):
            raise ValueError(
                "OpenCode Go conversation_id must be nonempty, bounded text"
            )
        self.opencode_session_id = hashlib.sha256(
            f"{OPENCODE_SESSION_VERSION}:{normalized}".encode()
        ).hexdigest()

    def routing_settings(self, model_settings: ModelSettings | None) -> ModelSettings:
        settings = cast(ModelSettings, dict(model_settings or {}))
        headers = dict(settings.get("extra_headers", {}))
        protected = {
            OPENCODE_SESSION_HEADER: self.opencode_session_id,
            "User-Agent": APPLICATION_USER_AGENT,
        }
        for name, value in protected.items():
            for existing_name, existing_value in headers.items():
                if (
                    existing_name.casefold() == name.casefold()
                    and existing_value != value
                ):
                    raise ValueError(f"OpenCode Go routing header {name} is host-owned")
            headers[name] = value
        settings["extra_headers"] = headers
        return settings

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        return await super().request(
            messages, self.routing_settings(model_settings), model_request_parameters
        )

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any | None = None,
    ) -> AsyncGenerator[StreamedResponse]:
        async with super().request_stream(
            messages,
            self.routing_settings(model_settings),
            model_request_parameters,
            run_context,
        ) as stream:
            yield stream


def gateway_api_key_env(gateway: DeepSeekGateway) -> str:
    return {
        DeepSeekGateway.OFFICIAL: "DEEPSEEK_API_KEY",
        DeepSeekGateway.OPENCODE_ZEN: "OPENCODE_API_KEY",
        DeepSeekGateway.OPENCODE_GO: "OPENCODE_API_KEY",
    }[gateway]


def gateway_base_url(gateway: DeepSeekGateway) -> str:
    return {
        DeepSeekGateway.OFFICIAL: DEEPSEEK_BASE_URL,
        DeepSeekGateway.OPENCODE_ZEN: OPENCODE_ZEN_BASE_URL,
        DeepSeekGateway.OPENCODE_GO: OPENCODE_GO_BASE_URL,
    }[gateway]


def take_gateway_api_key_from_environment(
    gateway: DeepSeekGateway,
    environ: MutableMapping[str, str] | None = None,
) -> SecretStr:
    """取得所选网关密钥，并从后续子进程环境中移除模型凭据。

    Args:
        gateway: 决定要读取的密钥变量名。
        environ: 要读取和清理的环境映射；默认使用当前进程环境。

    Returns:
        捕获后的密钥。即使变量缺失也返回空 `SecretStr`，由模型工厂统一报错。

    Note:
        官方 DeepSeek 与 OpenCode Zen 两个变量都会被删除，避免 Harness Shell 或
        Abaqus vendor subprocess 继承未选中网关的凭据。
    """
    return take_gateway_api_keys_from_environment(environ)[gateway]


def take_gateway_api_keys_from_environment(
    environ: MutableMapping[str, str] | None = None,
) -> dict[DeepSeekGateway, SecretStr]:
    """捕获各网关凭据后移除环境变量，供多会话宿主按保存的网关独立选取。"""
    target = os.environ if environ is None else environ
    keys = {
        gateway: SecretStr(target.get(gateway_api_key_env(gateway), ""))
        for gateway in DeepSeekGateway
    }
    for variable_name in _MODEL_API_KEY_ENV_VARS:
        target.pop(variable_name, None)
    return keys


def create_deepseek_chat_model(
    *,
    gateway: DeepSeekGateway,
    model_name: str,
    api_key: SecretStr,
    timeout_seconds: float = DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
    conversation_id: str | None = None,
    execution_config: ModelExecutionConfig | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> tuple[OpenAIChatModel, AsyncOpenAI]:
    """创建显式配置 HTTP retry policy 的 DeepSeek Chat Completions 模型。

    Args:
        gateway: 实际承载模型请求的官方 DeepSeek 或 OpenCode Zen 网关。
        model_name: 发送给网关的 DeepSeek 模型 ID。
        api_key: 对应网关的 API 密钥，只由 Composition Root 从环境注入。
        timeout_seconds: 单次 HTTP 请求超时秒数。
        http_retries: OpenAI SDK 对连接、限流和可重试 HTTP 状态的最大重试次数。
        conversation_id: OpenCode Go必需的稳定会话标识；宿主传run/conversation ID，
            适配器只发送其带版本域的SHA-256，不发送原文。其他网关必须省略。
        execution_config: 可选的宿主有效配置；身份、重试和超时必须与显式参数一致。
        http_client: 可选的宿主 HTTP 客户端，用于评测传输观测；返回的 SDK 客户端负责关闭它。

    Returns:
        模型与其底层客户端。调用方拥有客户端生命周期并负责关闭。

    Raises:
        ValueError: 模型名或密钥为空、超时不是正数，或重试次数为负数。
    """
    normalized_model_name = model_name.strip()
    if not normalized_model_name:
        raise ValueError("DeepSeek model name must not be empty")
    if not normalized_model_name.startswith("deepseek-"):
        raise ValueError("DeepSeek model name must start with `deepseek-`")
    if not api_key.get_secret_value():
        raise ValueError(f"{gateway_api_key_env(gateway)} must not be empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")
    if http_retries < 0:
        raise ValueError("http_retries must be greater than or equal to zero")
    if gateway is DeepSeekGateway.OPENCODE_GO and conversation_id is None:
        raise ValueError("OpenCode Go requires a stable conversation_id")
    if gateway is not DeepSeekGateway.OPENCODE_GO and conversation_id is not None:
        raise ValueError("conversation_id is only valid for OpenCode Go")
    if execution_config is not None and (
        execution_config.model_provider != gateway
        or execution_config.model_name != normalized_model_name
        or execution_config.http_retries != http_retries
        or execution_config.http_timeout_seconds != timeout_seconds
    ):
        raise ValueError(
            "model factory arguments differ from effective execution config"
        )
    settings = (
        execution_config.model_settings() if execution_config is not None else None
    )

    # SDK normalizes legacy httpx clients as well as its current transport type.
    client_options: dict[str, Any] = (
        {"http_client": http_client} if http_client is not None else {}
    )
    client = AsyncOpenAI(
        api_key=api_key.get_secret_value(),
        base_url=gateway_base_url(gateway),
        timeout=timeout_seconds,
        max_retries=http_retries,
        **client_options,
    )
    provider_type = {
        DeepSeekGateway.OFFICIAL: DeepSeekProvider,
        DeepSeekGateway.OPENCODE_ZEN: OpenCodeZenDeepSeekProvider,
        DeepSeekGateway.OPENCODE_GO: OpenCodeGoDeepSeekProvider,
    }[gateway]
    provider = provider_type(openai_client=client)
    profile = ModelProfile(context_window=DEEPSEEK_V4_FLASH_CONTEXT_WINDOW)
    model = (
        OpenCodeGoChatModel(
            normalized_model_name,
            provider=provider,
            conversation_id=conversation_id or "",
            profile=profile,
            settings=settings,
        )
        if gateway is DeepSeekGateway.OPENCODE_GO
        else OpenAIChatModel(
            normalized_model_name,
            provider=provider,
            profile=profile,
            settings=settings,
        )
    )
    return model, client


def create_deepseek_v4_flash_model(
    *,
    api_key: SecretStr,
    timeout_seconds: float = DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
) -> tuple[OpenAIChatModel, AsyncOpenAI]:
    """创建显式配置 HTTP retry policy 的 DeepSeek V4-Flash Chat 模型。

    Args:
        api_key: DeepSeek API 密钥，只由 Composition Root 从环境注入。
        timeout_seconds: 单次 HTTP 请求超时秒数。
        http_retries: OpenAI SDK 最大重试次数。

    Returns:
        模型与其底层客户端。调用方拥有客户端生命周期并负责关闭。

    Raises:
        ValueError: 密钥为空、超时不是正数，或重试次数为负数。
    """
    return create_deepseek_chat_model(
        gateway=DeepSeekGateway.OFFICIAL,
        model_name=DEEPSEEK_V4_FLASH,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        http_retries=http_retries,
    )
