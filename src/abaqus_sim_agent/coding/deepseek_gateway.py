from __future__ import annotations

import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.settings import ModelSettings

from .model import (
    APPLICATION_USER_AGENT,
    DEEPSEEK_V4_FLASH_CONTEXT_WINDOW,
    DEFAULT_MODEL_HTTP_RETRIES,
    DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    OPENCODE_GO_BASE_URL,
    OPENCODE_SESSION_HEADER,
    OPENCODE_SESSION_VERSION,
    OPENCODE_ZEN_BASE_URL,
    DeepSeekGateway,
    ModelExecutionConfig,
    gateway_api_key_env,
    gateway_base_url,
)


def _opencode_profile(model_name: str) -> ModelProfile | None:
    return DeepSeekProvider.model_profile(
        "deepseek-v4-flash" if model_name == "deepseek-v4.1-flash" else model_name
    )


class OpenCodeZenDeepSeekProvider(DeepSeekProvider):
    model_profile = staticmethod(_opencode_profile)

    @property
    def name(self) -> str:
        return DeepSeekGateway.OPENCODE_ZEN.value

    @property
    def base_url(self) -> str:
        return OPENCODE_ZEN_BASE_URL


class OpenCodeGoDeepSeekProvider(DeepSeekProvider):
    model_profile = staticmethod(_opencode_profile)

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
    profile = ModelProfile(
        context_window=execution_config.context_window
        if execution_config and execution_config.context_window is not None
        else DEEPSEEK_V4_FLASH_CONTEXT_WINDOW
    )
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
