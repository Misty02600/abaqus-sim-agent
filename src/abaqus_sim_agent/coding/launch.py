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

if TYPE_CHECKING:
    from .amendments import CodingRunAmendmentLedger


class LaunchManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal["coding-run-launch-v7"] = "coding-run-launch-v7"
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
    context_policy_version: Literal["deepseek-1m-tiered-summary-v1"]
    model_provider: DeepSeekGateway
    model_name: str
    http_retries: int = Field(ge=0)
    skill_library: Path
    api_catalog_dir: Path | None = None
    api_catalog_mode: ApiCatalogMode = "off"
    concept_index_dir: Path | None = None
    concept_retrieval_mode: ConceptRetrievalMode = "off"
    diagnostic_retrieval_mode: DiagnosticRetrievalMode = "off"
    static_preflight_mode: Literal["off", "advisory", "enforced_verified"] = "off"
    abqpy_static_wheel: Path | None = None
    static_preflight_cache_dir: Path | None = None
    reasoning_policy: Literal["provider_default", "enabled_high"]
    http_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    planning_enabled: bool
    interaction_policy: Literal["legacy", "workspace_facts_first"]

    @model_validator(mode="after")
    def valid_version(self):
        if self.response_enabled and self.question_protocol != "ask_user_v1":
            raise ValueError("response-enabled launch requires ask_user_v1")
        if not self.capability_definitions:
            raise ValueError("launch requires capability definition bindings")
        ids = [item.capability_id for item in self.capability_definitions]
        if len(ids) != len(set(ids)):
            raise ValueError("capability definition ids must be unique")
        if self.context_policy_version != CONTEXT_POLICY_VERSION:
            raise ValueError("launch requires the current context policy")
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
        )


def apply_launch_amendments(
    launch: LaunchManifest, ledger: CodingRunAmendmentLedger
) -> LaunchManifest:
    values = launch.model_dump(mode="python")
    for amendment in ledger.amendments:
        config = amendment.config
        if config is None:
            continue
        for field in ("model_provider", "model_name", "http_retries"):
            value = getattr(config, field)
            if value is not None:
                values[field] = value
    return LaunchManifest.model_validate(values)
