"""CLI/Web 共用的本地模型可用性与稳定选择；不请求远端认证或模型。"""

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict, SecretStr
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import infer_model_profile

from .credential_store import CredentialStore
from .model import read_gateway_api_keys_from_environment
from .model_auth import local_authenticated_providers
from .model_catalog import model_metadata
from .model_connections import resolve_context_window
from .providers import (
    ModelProvider,
    ProviderUnavailable,
    credential_family,
    provider_catalog,
    require_provider_dependencies,
)


class AvailableModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: ModelProvider
    name: str
    context_window: int


def configured_providers(
    api_keys: Mapping[ModelProvider, SecretStr] | None = None,
    *,
    authenticated_providers: Iterable[ModelProvider] = (),
) -> frozenset[ModelProvider]:
    store = CredentialStore()
    keys = store.api_keys(
        read_gateway_api_keys_from_environment() if api_keys is None else api_keys
    )
    ready = (
        set(authenticated_providers)
        | set(local_authenticated_providers())
        | set(store.authenticated_providers())
    )
    families = {
        credential_family(provider)
        for provider, key in keys.items()
        if key.get_secret_value().strip()
    }
    ready.update(
        provider
        for provider in ModelProvider
        if credential_family(provider) in families
    )
    return frozenset(ready)


def check_model(provider: ModelProvider, name: str) -> AvailableModel:
    require_provider_dependencies(provider)
    metadata = model_metadata(provider, name)
    if metadata is not None and (not metadata.tools or not metadata.text):
        raise ProviderUnavailable("此模型不支持当前 Coder 所需的工具/文本能力。")
    window = resolve_context_window(provider, name)
    profile_provider = (
        "deepseek" if provider.value.startswith("opencode-") else provider
    )
    profile = infer_model_profile(f"{profile_provider}:{name}")
    if (
        window < 1024
        or not profile.get("supports_tools", True)
        or not profile.get("supports_text_output", True)
    ):
        raise ProviderUnavailable("此模型的窗口或工具/文本能力不满足当前 Coder 要求。")
    return AvailableModel(provider=provider, name=name, context_window=window)


def available_models(
    api_keys: Mapping[ModelProvider, SecretStr] | None = None,
    *,
    authenticated_providers: Iterable[ModelProvider] = (),
) -> tuple[AvailableModel, ...]:
    """按认证、SDK、窗口与工具能力筛选，按 provider/model ID 排序。

    Args:
        api_keys: 宿主已捕获的环境凭据；省略时读取当前环境，再合并本应用保存值，不返回密钥。
        authenticated_providers: 程序化调用方额外确认的认证状态；本应用登录从凭据文件发现。

    Returns:
        稳定排序的候选列表；不证明密钥、额度或每个模型已通过远端验收。
    """
    ready = configured_providers(
        api_keys, authenticated_providers=authenticated_providers
    )
    if not ready:
        return ()
    found: dict[tuple[str, str], AvailableModel] = {}
    for entry in provider_catalog():
        if entry.id not in ready:
            continue
        try:
            require_provider_dependencies(entry.id)
        except (ValueError, ImportError):
            continue
        for name in entry.models:
            try:
                found[(entry.id.value, name)] = check_model(entry.id, name)
            except (ValueError, ImportError, UserError):
                continue
    return tuple(found[key] for key in sorted(found))


def require_model_selection(provider: ModelProvider | None, name: str | None) -> None:
    if provider is None or not name:
        raise ProviderUnavailable(
            "请先选择模型：必须明确指定提供商和型号，不再自动选择默认模型。"
        )


def select_model(
    models: tuple[AvailableModel, ...],
    provider: ModelProvider | None,
    name: str | None,
) -> AvailableModel:
    require_model_selection(provider, name)
    candidates = [
        model for model in models if model.provider == provider and model.name == name
    ]
    if candidates:
        return candidates[0]
    raise ProviderUnavailable(
        "没有匹配的可选模型。请确认凭据、刷新目录，并从可选列表中选择；不支持目录外型号。"
    )
