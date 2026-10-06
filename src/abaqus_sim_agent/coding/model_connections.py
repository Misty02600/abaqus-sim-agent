"""原生模型装配与宿主凭据；不写入用户偏好或 CodingRun 文件。"""

import inspect
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import SecretStr
from pydantic_ai.models import Model, infer_model, infer_model_profile

from .model import (
    DEFAULT_MODEL_HTTP_RETRIES,
    DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    ModelExecutionConfig,
    create_deepseek_chat_model,
)
from .model_catalog import model_metadata
from .providers import (
    LOCAL_AUTH_PROVIDERS,
    ModelProvider,
    ProviderUnavailable,
    api_key_environments,
    connection_environment,
    credential_family,
    require_provider_dependencies,
)


class ModelClient(Protocol):
    async def close(self) -> None: ...


class ProviderClient:
    def __init__(self, provider: Any, transports: list[Any]):
        self.provider = provider
        self.transports = transports

    async def close(self) -> None:
        async with self.provider:
            pass
        client = self.provider.client
        if self.provider.name in {"google", "google-cloud"}:
            await client.aio.aclose()
        closer = getattr(client, "aclose", None) or getattr(client, "close", None)
        if closer is not None:
            result = closer()
            if inspect.isawaitable(result):
                await result
        for transport in self.transports:
            await transport.aclose()


def resolve_context_window(provider: ModelProvider, model_name: str) -> int:
    """解析新会话的窗口；恢复直接读取快照，不调用本函数。

    Raises:
        ProviderUnavailable: 所选提供商的 SDK 缺失或不兼容。
        ValueError: 框架与适配器都无法确定窗口，此模型尚不能用于新会话。
    """
    require_provider_dependencies(provider)
    metadata = model_metadata(provider, model_name)
    if provider in (
        ModelProvider.OPENCODE_GO,
        ModelProvider.OPENCODE_ZEN,
    ) and model_name in {
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "deepseek-v4.1-flash",
        "deepseek-v4-flash-vision-exp",
    }:
        return 1_000_000
    if provider is ModelProvider.OFFICIAL and model_name == "deepseek-flash":
        return 1_000_000
    value = infer_model_profile(f"{provider.value}:{model_name}").get("context_window")
    if metadata is not None:
        # Provider catalogs can describe narrower serving limits than SDK family metadata.
        return (
            min(value, metadata.context_window)
            if value is not None
            else metadata.context_window
        )
    if value is None:
        raise ValueError("此模型的上下文窗口元数据尚未就绪，暂不支持创建会话。")
    return value


def create_model(
    *,
    gateway: ModelProvider,
    model_name: str,
    api_key: SecretStr,
    timeout_seconds: float = DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    http_retries: int | None = DEFAULT_MODEL_HTTP_RETRIES,
    conversation_id: str | None = None,
    execution_config: ModelExecutionConfig | None = None,
    connection_options: Mapping[str, Any] | None = None,
    http_client: Any | None = None,
) -> tuple[Model, ModelClient]:
    """创建指定协议的模型和由宿主关闭的客户端。

    Args:
        gateway: 原生提供商或 OpenCode 接入标识。
        api_key: 宿主提供的密钥；本机凭据或账号登录可以为空。
        execution_config: 保存的有效配置，包括窗口与非凭据环境绑定。
        connection_options: 宿主内存中的原生认证参数，不进入偏好或会话文件。
        http_client: 可选测试/观测传输；返回的客户端负责释放它。

    Returns:
        使用原生 profile 的模型，以及需要在所有退出路径关闭的资源句柄。

    Raises:
        ValueError: 缺少凭据、连接环境改变或接口参数不被提供商支持。
    """
    require_provider_dependencies(gateway)
    if (
        execution_config
        and execution_config.provider_environment is not None
        and execution_config.provider_environment != connection_environment(gateway)
    ):
        raise ValueError("提供商的接口/区域环境配置已改变，请恢复原配置或新建会话。")
    if gateway in (ModelProvider.OPENCODE_GO, ModelProvider.OPENCODE_ZEN):
        return create_deepseek_chat_model(
            gateway=gateway,
            model_name=model_name,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            http_retries=http_retries,
            conversation_id=conversation_id,
            execution_config=execution_config,
            http_client=http_client,
        )
    from pydantic_ai.providers import infer_provider_class

    options: dict[str, Any] = dict(connection_options or {})
    if gateway is ModelProvider.OPENAI_CODEX and not options:
        from .credential_store import CredentialStore

        source = CredentialStore().codex_source()
        if source is not None:
            options["credential_source"] = source
    transports: list[Any] = [http_client] if http_client is not None else []
    key = api_key.get_secret_value()
    if key:
        options["token" if gateway is ModelProvider.SNOWFLAKE else "api_key"] = key
    elif gateway not in LOCAL_AUTH_PROVIDERS:
        names = api_key_environments(gateway)
        raise ValueError(
            f"请填写 {gateway.value} 的 API Key"
            + (f"，或设置 {names[0]}。" if names else "。")
        )
    if http_client is not None:
        options["http_client"] = http_client
    if gateway.startswith("gateway/"):
        from pydantic_ai.providers.gateway import gateway_provider

        provider = gateway_provider(gateway.removeprefix("gateway/"), **options)
    else:
        provider_class = infer_provider_class(gateway)
        if gateway is ModelProvider.OFFICIAL and model_name == "deepseek-flash":
            original_class = provider_class
            provider_class = type(
                "DeepSeekFlashProvider",
                (original_class,),
                {
                    "model_profile": staticmethod(
                        lambda _: original_class.model_profile("deepseek-v4-flash")
                    )
                },
            )
        if gateway in (ModelProvider.GOOGLE, ModelProvider.GOOGLE_CLOUD):
            from google.genai.types import HttpRetryOptions

            options["retry_options"] = HttpRetryOptions(
                attempts=(
                    http_retries
                    if http_retries is not None
                    else DEFAULT_MODEL_HTTP_RETRIES
                )
                + 1
            )
        if gateway is ModelProvider.COHERE:
            from cohere import AsyncClientV2

            if http_client is None:
                import httpx

                http_client = httpx.AsyncClient(timeout=timeout_seconds)
                transports.append(http_client)

            options = {
                "cohere_client": AsyncClientV2(
                    api_key=key,
                    timeout=timeout_seconds,
                    max_retries=http_retries,
                    httpx_client=http_client,
                )
            }
        provider = provider_class(**options)
    client = provider.client
    if http_retries is not None and hasattr(client, "max_retries"):
        client.max_retries = http_retries
    if hasattr(client, "timeout"):
        client.timeout = timeout_seconds
    if gateway is ModelProvider.OFFICIAL:
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.profiles.openai import OpenAIModelProfile

        model = OpenAIChatModel(
            model_name,
            provider=provider,
            profile=OpenAIModelProfile(
                openai_chat_supports_max_completion_tokens=False
            ),
        )
    else:
        model = infer_model(
            f"{gateway.value}:{model_name}", provider_factory=lambda _: provider
        )
    if execution_config and execution_config.context_window is not None:
        # Bind the saved window while retaining the native wire-capability profile.
        from pydantic_ai.models.wrapper import WrapperModel

        class BoundWindowModel(WrapperModel):
            @property
            def context_window(self) -> int:
                assert execution_config is not None
                assert execution_config.context_window is not None
                return execution_config.context_window

        model = BoundWindowModel(model)
    return model, ProviderClient(provider, transports)


def provider_key(
    provider: ModelProvider, keys: Mapping[ModelProvider, SecretStr]
) -> SecretStr:
    require_provider_dependencies(provider)
    key = keys.get(provider, SecretStr(""))
    if not key.get_secret_value().strip():
        key = next(
            (
                value
                for candidate, value in keys.items()
                if credential_family(candidate) == credential_family(provider)
                and value.get_secret_value().strip()
            ),
            SecretStr(""),
        )
    if not key.get_secret_value().strip() and provider not in LOCAL_AUTH_PROVIDERS:
        raise ProviderUnavailable(f"请填写 {provider.value} 的 API Key。")
    return key
