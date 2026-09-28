"""CLI与amendment共用的启动快照合同；模块导入不读写状态。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .api_lookup import ApiCatalogMode
from .concept_lookup import ConceptRetrievalMode
from .context_policy import CONTEXT_POLICY_VERSION
from .contracts import CapabilityDefinitionBinding
from .diagnostic_retrieval import DiagnosticRetrievalMode
from .document_policy_binding import DocumentPolicyBinding
from .domain_knowledge import DomainKnowledgeBundleBinding
from .model import DeepSeekGateway, ModelExecutionConfig
from .text_encoding import TEXT_ENCODING_POLICY_VERSION

if TYPE_CHECKING:
    from .amendments import CodingRunAmendmentLedger


class LaunchManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["coding-run-launch-v9", "coding-run-launch-v10"]
    question_protocol: Literal["ask_user_v1"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    document_policy: DocumentPolicyBinding | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    response_enabled: bool
    domain_knowledge: DomainKnowledgeBundleBinding | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    capability_definitions: tuple[CapabilityDefinitionBinding, ...]
    context_policy_version: Literal[
        "deepseek-1m-tiered-summary-v1", "model-window-tiered-summary-v1"
    ]
    context_window: int | None = Field(
        default=None, ge=1024, exclude_if=lambda v: v is None
    )
    model_base_url: str | None = Field(default=None, exclude_if=lambda v: v is None)
    provider_environment: dict[str, str] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    filesystem_policy_version: Literal["utf8-cp936-preserve-v1"] = (
        TEXT_ENCODING_POLICY_VERSION
    )
    model_provider: DeepSeekGateway
    model_name: str
    http_retries: int | None = Field(ge=0)
    skill_library: Path
    api_catalog_dir: Path | None = None
    api_catalog_mode: ApiCatalogMode = "off"
    concept_index_dir: Path | None = None
    concept_retrieval_mode: ConceptRetrievalMode = "off"
    diagnostic_retrieval_mode: DiagnosticRetrievalMode = "off"
    static_preflight_mode: Literal["off", "on"] = "off"
    abqpy_static_wheel: Path | None = None
    static_preflight_cache_dir: Path | None = None
    reasoning_policy: Literal["provider_default", "enabled_high"]
    http_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    planning_enabled: bool
    interaction_policy: Literal["legacy", "workspace_facts_first"]

    @model_validator(mode="after")
    def valid_version(self):
        if (
            self.context_policy_version == "deepseek-1m-tiered-summary-v1"
            and self.model_provider
            not in (
                DeepSeekGateway.OFFICIAL,
                DeepSeekGateway.OPENCODE_GO,
                DeepSeekGateway.OPENCODE_ZEN,
            )
        ):
            raise ValueError("旧固定 1M 会话不能切换到其他提供商，请新建会话。")
        if self.response_enabled and self.question_protocol != "ask_user_v1":
            raise ValueError("response-enabled launch requires ask_user_v1")
        if not self.capability_definitions:
            raise ValueError("launch requires capability definition bindings")
        ids = [item.capability_id for item in self.capability_definitions]
        if len(ids) != len(set(ids)):
            raise ValueError("capability definition ids must be unique")
        if self.context_policy_version not in (
            CONTEXT_POLICY_VERSION,
            "model-window-tiered-summary-v1",
        ):
            raise ValueError("launch requires the current context policy")
        if (
            self.context_policy_version == "model-window-tiered-summary-v1"
            and self.context_window is None
        ):
            raise ValueError("model-window policy requires a saved context_window")
        if self.schema_version == "coding-run-launch-v10":
            if self.api_catalog_dir is None or self.api_catalog_mode == "off":
                raise ValueError("launch v10 requires an enabled API catalog")
            if not self.api_catalog_dir.is_absolute():
                raise ValueError("launch API catalog directory must be absolute")
            if self.api_catalog_dir.resolve() != self.api_catalog_dir:
                raise ValueError("launch API catalog directory must be resolved")
        if self.document_policy is not None and self.concept_retrieval_mode != "tool":
            raise ValueError("bounded documents require concept tool mode")
        self.effective_execution()
        return self

    def allows_response(self) -> bool:
        return self.response_enabled

    def effective_execution(self, *, pure_edit: bool = False) -> ModelExecutionConfig:
        """构造当前run绑定的有效模型执行配置。"""
        if pure_edit and self.planning_enabled:
            raise ValueError("pure-edit launch must disable Planning")
        return ModelExecutionConfig(
            model_provider=self.model_provider,
            model_name=self.model_name,
            http_retries=self.http_retries,
            http_timeout_seconds=self.http_timeout_seconds,
            reasoning_policy=self.reasoning_policy,
            planning_enabled=self.planning_enabled,
            interaction_policy=self.interaction_policy,
            context_window=self.context_window,
            model_base_url=self.model_base_url,
            provider_environment=self.provider_environment,
        )


def apply_launch_amendments(
    launch: LaunchManifest, ledger: CodingRunAmendmentLedger
) -> LaunchManifest:
    values = launch.model_dump(mode="python")
    for amendment in ledger.amendments:
        config = amendment.config
        if config is None:
            continue
        if (
            values.get("context_window") is not None
            and (config.model_provider is not None or config.model_name is not None)
            and config.context_window is None
        ):
            raise ValueError("修改模型必须同时保存新模型的 context_window。")
        if (
            values.get("model_base_url")
            and config.model_provider is not None
            and config.model_provider != values["model_provider"]
        ):
            raise ValueError("使用自定义接口的会话切换提供商时请新建会话。")
        if (
            values.get("provider_environment") is not None
            and config.model_provider is not None
            and config.model_provider != values["model_provider"]
            and config.provider_environment is None
        ):
            raise ValueError("切换提供商必须同时保存新的连接环境绑定。")
        for field in (
            "model_provider",
            "model_name",
            "http_retries",
            "context_window",
            "provider_environment",
        ):
            value = getattr(config, field)
            if value is not None:
                values[field] = value
    return LaunchManifest.model_validate(values)
