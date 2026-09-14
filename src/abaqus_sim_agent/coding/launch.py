"""CLI与amendment共用的启动快照合同；模块导入不读写状态。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .api_lookup import ApiCatalogMode
from .concept_lookup import ConceptRetrievalMode
from .diagnostic_retrieval import DiagnosticRetrievalMode
from .document_policy_binding import DocumentPolicyBinding
from .model import DeepSeekGateway, ModelExecutionConfig

if TYPE_CHECKING:
    from .amendments import CodingRunAmendmentLedger


class LaunchManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[
        "coding-run-launch-v1",
        "coding-run-launch-v2",
        "coding-run-launch-v3",
        "coding-run-launch-v4",
    ] = "coding-run-launch-v1"
    question_protocol: Literal["ask_user_v1"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    document_policy: DocumentPolicyBinding | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
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
    reasoning_policy: Literal["provider_default", "enabled_high"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    http_timeout_seconds: float | None = Field(
        default=None, gt=0, allow_inf_nan=False, exclude_if=lambda v: v is None
    )
    planning_enabled: bool | None = Field(default=None, exclude_if=lambda v: v is None)
    interaction_policy: Literal["legacy", "workspace_facts_first"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def valid_version(self):
        if self.schema_version == "coding-run-launch-v4":
            if self.question_protocol != "ask_user_v1":
                raise ValueError("launch v4 requires ask_user_v1")
        elif "question_protocol" in self.model_fields_set:
            raise ValueError("legacy launch cannot change its question protocol")
        if self.document_policy is not None and (
            self.schema_version not in ("coding-run-launch-v2", "coding-run-launch-v3")
            or self.concept_retrieval_mode != "tool"
        ):
            raise ValueError(
                "bounded documents require launch v2/v3 and concept tool mode"
            )
        fields = {
            "reasoning_policy",
            "http_timeout_seconds",
            "planning_enabled",
            "interaction_policy",
        }
        if self.schema_version in ("coding-run-launch-v3", "coding-run-launch-v4"):
            if any(getattr(self, field) is None for field in fields):
                raise ValueError(
                    "launch v3 requires complete effective execution settings"
                )
            self.effective_execution()
        elif fields & self.model_fields_set:
            raise ValueError("legacy launch cannot contain v3 execution settings")
        return self

    def effective_execution(self, *, pure_edit: bool = False) -> ModelExecutionConfig:
        """构造兼容视图；不把新run默认值写回旧文件。"""
        legacy = self.schema_version not in (
            "coding-run-launch-v3",
            "coding-run-launch-v4",
        )
        if legacy:
            return ModelExecutionConfig(
                model_provider=self.model_provider,
                model_name=self.model_name,
                http_retries=self.http_retries,
                http_timeout_seconds=60.0,
                reasoning_policy="provider_default",
                planning_enabled=not pure_edit,
                interaction_policy="legacy",
            )
        assert self.http_timeout_seconds is not None
        assert self.reasoning_policy is not None
        assert self.planning_enabled is not None
        assert self.interaction_policy is not None
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
