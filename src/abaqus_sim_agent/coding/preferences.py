"""CLI/Web 共用的新会话偏好；与宿主进程设置、运行权限和恢复状态分开。"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from platformdirs import user_config_path
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..execution import DEFAULT_ABAQUS_2024_LAUNCHER
from ..skills import skill_library_path
from .api_lookup import DEFAULT_PRODUCT_API_CATALOG_MODE, ProductApiCatalogMode
from .contracts import RuntimeProfile
from .model import (
    DEEPSEEK_V4_FLASH,
    DEFAULT_MODEL_HTTP_RETRIES,
    DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    DeepSeekGateway,
    ModelExecutionConfig,
    new_run_execution_config,
)

PROJECT_SETTINGS_PATH = Path(".abaqus-sim-agent/settings.json")


class RunPreferences(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    model_provider: DeepSeekGateway | None = None
    model_name: str | None = Field(default=None, min_length=1)
    context_window: int | None = Field(default=None, ge=1024)
    model_base_url: str | None = None
    http_retries: int | None = Field(default=None, ge=0)
    http_timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    reasoning_policy: Literal["provider_default", "enabled_high"] | None = None
    launcher_path: Path | None = None
    skill_library: Path | None = None
    api_catalog_dir: Path | None = None
    api_catalog_mode: ProductApiCatalogMode | None = None
    static_preflight_mode: Literal["off", "on"] | None = None
    abqpy_static_wheel: Path | None = None
    static_preflight_cache_dir: Path | None = None
    knowledge_pack_ids: tuple[str, ...] | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_supplied_values(self):
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("omit an inherited preference instead of setting null")
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
class ResolvedRunPreferences:
    execution: ModelExecutionConfig
    runtime: RuntimeProfile
    skill_library: Path
    api_catalog_dir: Path
    api_catalog_mode: ProductApiCatalogMode
    static_preflight_mode: Literal["off", "on"]
    abqpy_static_wheel: Path | None
    static_preflight_cache_dir: Path | None
    knowledge_pack_ids: tuple[str, ...]


def user_settings_path() -> Path:
    return user_config_path("abaqus-sim-agent", appauthor=False) / "settings.json"


def read_preferences(path: Path) -> RunPreferences:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return RunPreferences()
    except OSError as exc:
        raise ValueError(
            f"Cannot read preferences: {path} ({type(exc).__name__})"
        ) from exc
    try:
        parsed = RunPreferences.model_validate_json(data)
    except ValidationError as exc:
        locations = ", ".join(
            ".".join(map(str, item["loc"])) or "JSON"
            for item in exc.errors(include_input=False)
        )
        raise ValueError(f"Invalid preferences in {path}: {locations}") from exc
    return resolve_preference_paths(parsed, path.resolve().parent)


def resolve_preference_paths(preferences: RunPreferences, base: Path) -> RunPreferences:
    values = preferences.model_dump(exclude_unset=True)
    for name, value in values.items():
        if isinstance(value, Path):
            values[name] = (base / value).resolve()
    return RunPreferences.model_validate(values)


def resolve_preferences(workspace: Path, *overrides: RunPreferences) -> RunPreferences:
    """按用户、工作区项目、显式输入顺序合并新会话偏好。

    Args:
        workspace: 新会话绑定的工作区，仅从该目录读取项目偏好，不向父目录搜索。
        overrides: 从低到高优先级的显式输入；未提供的字段继续继承。

    Returns:
        严格验证后的合并偏好；序列整项替换，空序列清空选择。文件内路径相对文件所在目录。

    Raises:
        ValueError: 偏好文件不可读或内容非法。

    Note:
        恢复已有会话不调用此函数。文件不存在表示未设置，空文件和显式 null 均拒绝。
    """
    values: dict[str, object] = {}
    for source in (
        read_preferences(user_settings_path()),
        read_preferences(workspace / PROJECT_SETTINGS_PATH),
        *overrides,
    ):
        values.update(source.model_dump(exclude_unset=True))
    return RunPreferences.model_validate(values)


def model_execution(preferences: RunPreferences) -> ModelExecutionConfig:
    effective = preferences
    from .model_connections import resolve_context_window
    from .providers import SDK_DEFAULT_RETRY_PROVIDERS, connection_environment

    provider = effective.model_provider or DeepSeekGateway.OFFICIAL
    name = effective.model_name
    if not name:
        if provider not in (
            DeepSeekGateway.OFFICIAL,
            DeepSeekGateway.OPENCODE_GO,
            DeepSeekGateway.OPENCODE_ZEN,
        ):
            raise ValueError("请选择或填写该提供商的模型名称。")
        name = DEEPSEEK_V4_FLASH
    return new_run_execution_config(
        model_provider=provider,
        model_name=name,
        context_window=resolve_context_window(provider, name, effective.context_window),
        model_base_url=effective.model_base_url,
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


def resolve_model_config(
    workspace: Path, *overrides: RunPreferences
) -> ModelExecutionConfig:
    return model_execution(resolve_preferences(workspace, *overrides))


def resolve_run_config(
    workspace: Path, *overrides: RunPreferences
) -> ResolvedRunPreferences:
    """补齐并验证新会话的公共配置，供 CLI/Web 使用同一组实际值。

    Args:
        workspace: 已选择的工作区，用于定位项目配置。
        overrides: 已由入口解析相对路径的显式覆盖，从低到高排列。

    Returns:
        供装配生成 request/launch 的配置。静态检查关闭时不绑定闲置来源路径；
        资源是否存在、内容身份与工作区归属由后续共享资源准备进一步检查。

    Raises:
        ValueError: 配置来源非法、缺少 Catalog、启用静态检查但来源不完整，或模型策略不兼容。
    """
    preferences = resolve_preferences(workspace, *overrides)
    if preferences.api_catalog_dir is None:
        raise ValueError(
            "API catalog directory is required (--api-catalog-dir or settings.json)"
        )
    mode = preferences.static_preflight_mode or "off"
    wheel = preferences.abqpy_static_wheel if mode == "on" else None
    cache = preferences.static_preflight_cache_dir if mode == "on" else None
    if mode == "on" and (wheel is None or cache is None):
        raise ValueError(
            "static preflight on requires abqpy_static_wheel and static_preflight_cache_dir"
        )
    return ResolvedRunPreferences(
        execution=model_execution(preferences),
        runtime=RuntimeProfile(
            launcher_path=(
                preferences.launcher_path or DEFAULT_ABAQUS_2024_LAUNCHER
            ).resolve()
        ),
        skill_library=(preferences.skill_library or skill_library_path()).resolve(),
        api_catalog_dir=preferences.api_catalog_dir.resolve(),
        api_catalog_mode=preferences.api_catalog_mode
        or DEFAULT_PRODUCT_API_CATALOG_MODE,
        static_preflight_mode=mode,
        abqpy_static_wheel=wheel.resolve() if wheel is not None else None,
        static_preflight_cache_dir=cache.resolve() if cache is not None else None,
        knowledge_pack_ids=preferences.knowledge_pack_ids or (),
    )
