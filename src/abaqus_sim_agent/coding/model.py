from __future__ import annotations

import os
from collections.abc import MutableMapping
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings

from .providers import ModelProvider as DeepSeekGateway
from .providers import api_key_environments

if TYPE_CHECKING:
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings

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


class ModelExecutionConfig(BaseModel):
    """宿主拥有的有效配置；不含凭据和任务预算，旧run显式使用provider_default。"""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    model_provider: DeepSeekGateway
    model_name: str = Field(min_length=1)
    http_retries: int | None = Field(default=DEFAULT_MODEL_HTTP_RETRIES, ge=0)
    http_timeout_seconds: float = Field(
        default=DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS, gt=0, allow_inf_nan=False
    )
    reasoning_policy: Literal["provider_default", "enabled_high"] = "provider_default"
    max_tokens: int | None = Field(default=None, gt=0, exclude_if=lambda v: v is None)
    output_truncation_recovery: bool = False
    planning_enabled: bool = False
    interaction_policy: Literal["legacy", "workspace_facts_first"] = "legacy"
    context_window: int | None = Field(
        default=None, ge=1024, exclude_if=lambda v: v is None
    )
    provider_environment: dict[str, str] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def compatible_policy(self):
        from .providers import SDK_DEFAULT_RETRY_PROVIDERS, connection_environment

        if (
            self.model_provider not in SDK_DEFAULT_RETRY_PROVIDERS
            and self.http_retries is None
        ):
            raise ValueError("此提供商需要显式的 http_retries 数值。")

        if (
            self.provider_environment is not None
            and self.provider_environment.keys()
            != connection_environment(self.model_provider).keys()
        ):
            raise ValueError("provider_environment 不符合所选提供商的非凭据字段合同。")

        if (
            self.model_provider in SDK_DEFAULT_RETRY_PROVIDERS
            and self.http_retries is not None
        ):
            raise ValueError(
                f"{self.model_provider.value} 暂使用 SDK 默认重试策略，请省略 http_retries。"
            )
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
        settings: OpenAIChatModelSettings = {"timeout": self.http_timeout_seconds}
        if self.max_tokens is not None:
            settings["max_tokens"] = self.max_tokens
        if self.reasoning_policy == "enabled_high":
            settings.update(
                extra_body={"thinking": {"type": "enabled"}},
                openai_reasoning_effort="high",
            )
        return settings

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
            k in extra
            for k in ("max_tokens", "max_completion_tokens", "max_output_tokens")
        ) or any(k in supplied for k in ("max_completion_tokens", "max_output_tokens")):
            raise ValueError(
                "use the saved max_tokens setting, not a provider-specific override"
            )
        if "thinking" in supplied:
            raise ValueError(
                "unified thinking override conflicts with fixed reasoning policy"
            )
        for key in ("timeout", "openai_reasoning_effort", "max_tokens"):
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
    http_retries: int | None = None,
    http_timeout_seconds: float = DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    reasoning_policy: Literal["provider_default", "enabled_high"] | None = None,
    planning_enabled: bool = False,
    context_window: int | None = None,
    provider_environment: dict[str, str] | None = None,
    max_tokens: int | None = None,
) -> ModelExecutionConfig:
    from .providers import SDK_DEFAULT_RETRY_PROVIDERS

    if http_retries is None and model_provider not in SDK_DEFAULT_RETRY_PROVIDERS:
        http_retries = DEFAULT_MODEL_HTTP_RETRIES
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
        context_window=context_window,
        provider_environment=provider_environment,
        max_tokens=max_tokens,
        output_truncation_recovery=True,
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


def gateway_api_key_env(gateway: DeepSeekGateway) -> str:
    names = api_key_environments(gateway)
    return names[0] if names else ""


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
    keys = read_gateway_api_keys_from_environment(target)
    for variable_name in {
        name for gateway in DeepSeekGateway for name in api_key_environments(gateway)
    }:
        target.pop(variable_name, None)
    return keys


def read_gateway_api_keys_from_environment(
    environ: MutableMapping[str, str] | None = None,
) -> dict[DeepSeekGateway, SecretStr]:
    target = os.environ if environ is None else environ
    return {
        gateway: SecretStr(
            next(
                (
                    target[name]
                    for name in api_key_environments(gateway)
                    if target.get(name)
                ),
                "",
            )
        )
        for gateway in DeepSeekGateway
    }


def create_deepseek_chat_model(**kwargs):
    # Frozen evaluators keep their original DeepSeek-only factory contract.
    from .deepseek_gateway import create_deepseek_chat_model as create

    return create(**kwargs)


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


def __getattr__(name: str):
    if name in {
        "OpenCodeGoChatModel",
        "OpenCodeGoDeepSeekProvider",
        "OpenCodeZenDeepSeekProvider",
    }:
        from . import deepseek_gateway

        return getattr(deepseek_gateway, name)
    raise AttributeError(name)
