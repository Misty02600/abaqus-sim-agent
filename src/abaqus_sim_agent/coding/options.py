"""CLI/Web 共用的本次运行输入；不发现、读取或保存用户/项目偏好文件。"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..api_catalog.bundled import bundled_catalog_path
from ..execution.abaqus2024 import ABAQUS_2024_COMMAND
from ..skills import skill_library_path
from .api_lookup import DEFAULT_PRODUCT_API_CATALOG_MODE, ProductApiCatalogMode
from .contracts import RuntimeProfile
from .model import (
    DEFAULT_MODEL_HTTP_RETRIES,
    DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    DeepSeekGateway,
    ModelExecutionConfig,
    new_run_execution_config,
)
from .model_selection import (
    AvailableModel,
    available_models,
    require_model_selection,
    select_model,
)
from .static_preflight import managed_static_paths


class RunOptions(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    model_provider: DeepSeekGateway | None = None
    model_name: str | None = Field(default=None, min_length=1)
    max_tokens: int | None = Field(default=None, gt=0)
    http_retries: int | None = Field(default=None, ge=0)
    http_timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    reasoning_policy: Literal["provider_default", "enabled_high"] | None = None
    api_catalog_mode: ProductApiCatalogMode | None = None
    static_preflight_mode: Literal["off", "on"] | None = None
    knowledge_pack_ids: tuple[str, ...] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_supplied_values(self):
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("omit an unspecified option instead of setting null")
        if self.model_name is not None and self.model_name != self.model_name.strip():
            raise ValueError("model_name must be normalized")
        if self.knowledge_pack_ids is not None:
            if len(set(self.knowledge_pack_ids)) != len(self.knowledge_pack_ids) or any(
                re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", item) is None
                for item in self.knowledge_pack_ids
            ):
                raise ValueError("knowledge pack ids must be valid and unique")
        return self


@dataclass(frozen=True)
class ResolvedRunOptions:
    execution: ModelExecutionConfig
    runtime: RuntimeProfile
    skill_library: Path
    api_catalog_dir: Path
    api_catalog_mode: ProductApiCatalogMode
    static_preflight_mode: Literal["off", "on"]
    abqpy_static_wheel: Path | None
    static_preflight_cache_dir: Path | None
    knowledge_pack_ids: tuple[str, ...]


def merge_run_options(*overrides: RunOptions) -> RunOptions:
    """合并宿主本次启动选项与本次创建输入，不读取文件。

    Args:
        overrides: 从低到高优先级的显式输入；未提供的字段继续继承。

    Returns:
        严格验证后的合并输入；序列整项替换，空序列清空本次选择。

    Raises:
        ValueError: 合并输入不符合字段合同。
    """
    values: dict[str, object] = {}
    for source in overrides:
        values.update(source.model_dump(exclude_unset=True))
    return RunOptions.model_validate(values)


def model_execution(options: RunOptions) -> ModelExecutionConfig:
    effective = options
    from .model_connections import resolve_context_window
    from .model_output_settings import ModelOutputSettingsStore
    from .providers import SDK_DEFAULT_RETRY_PROVIDERS, connection_environment

    provider = effective.model_provider
    name = effective.model_name
    if provider is None or name is None:
        raise ValueError("请先从可用列表确定提供商与模型名称。")
    return new_run_execution_config(
        model_provider=provider,
        model_name=name,
        max_tokens=(
            effective.max_tokens
            if effective.max_tokens is not None
            else ModelOutputSettingsStore().get(provider, name).max_tokens
        ),
        context_window=resolve_context_window(provider, name),
        provider_environment=connection_environment(provider),
        http_retries=(
            effective.http_retries
            if effective.http_retries is not None
            else None
            if provider in SDK_DEFAULT_RETRY_PROVIDERS
            else DEFAULT_MODEL_HTTP_RETRIES
        ),
        http_timeout_seconds=(
            effective.http_timeout_seconds
            if effective.http_timeout_seconds is not None
            else DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS
        ),
        reasoning_policy=effective.reasoning_policy,
    )


def resolve_run_config(
    workspace: Path,
    *overrides: RunOptions,
    models: tuple[AvailableModel, ...] | None = None,
) -> ResolvedRunOptions:
    """补齐并验证新会话的公共配置，供 CLI/Web 使用同一组实际值。

    Args:
        workspace: 已选择的工作区，用于校验程序管理的缓存不会落入工程目录。
        overrides: 本次显式覆盖，从低到高排列。
        models: 宿主当前可用模型；省略时从环境和本机认证来源筛选。

    Returns:
        供装配生成 request/launch 的配置。静态检查关闭时不绑定闲置来源路径；
        资源是否存在、内容身份与工作区归属由后续共享资源准备进一步检查。

    Raises:
        ValueError: 本次输入非法、托管缓存落在工作区内，或模型策略不兼容。
    """
    options = merge_run_options(*overrides)
    require_model_selection(options.model_provider, options.model_name)
    if models is None:
        models = available_models()
    selected = select_model(
        models,
        options.model_provider,
        options.model_name,
    )
    options = options.model_copy(
        update={"model_provider": selected.provider, "model_name": selected.name}
    )
    mode = options.static_preflight_mode or "off"
    wheel, cache = managed_static_paths() if mode == "on" else (None, None)
    if cache is not None and cache.is_relative_to(workspace.resolve()):
        raise ValueError("managed static cache must be outside the workspace")
    return ResolvedRunOptions(
        execution=model_execution(options),
        runtime=RuntimeProfile(launcher_path=Path(ABAQUS_2024_COMMAND)),
        skill_library=skill_library_path().resolve(),
        api_catalog_dir=bundled_catalog_path().resolve(strict=True),
        api_catalog_mode=options.api_catalog_mode or DEFAULT_PRODUCT_API_CATALOG_MODE,
        static_preflight_mode=mode,
        abqpy_static_wheel=wheel,
        static_preflight_cache_dir=cache.resolve() if cache is not None else None,
        knowledge_pack_ids=options.knowledge_pack_ids or (),
    )
