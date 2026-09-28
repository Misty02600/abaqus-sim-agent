"""力学案例的评测说明；不拥有物理判据或是否计分的决定。"""

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

NonEmptyText = Annotated[str, Field(min_length=1)]
CapabilityTag = Literal[
    "axial-load",
    "distributed-load",
    "bending",
    "torsion",
    "pressure",
    "thermal-strain",
    "section-assignment",
    "section-orientation",
    "reaction-balance",
    "displacement",
    "stress-concentration",
    "local-stress",
    "shell-section-output",
    "coordinate-components",
    "load-case-isolation",
    "mesh-refinement",
    "symmetry",
    "junction-constraint",
]


class _MetadataModel(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", frozen=True, str_strip_whitespace=True
    )


class SourceRef(_MetadataModel):
    kind: Literal["project_analytic", "ansys", "nafems"]
    reference: NonEmptyText
    url: HttpUrl | None = None
    document_version: NonEmptyText | None = None

    @model_validator(mode="after")
    def require_external_link(self) -> Self:
        if self.kind != "project_analytic" and self.url is None:
            raise ValueError("external sources require a URL")
        return self


class CaseMetadata(_MetadataModel):
    description: NonEmptyText
    tags: Annotated[tuple[CapabilityTag, ...], Field(min_length=1)]
    source: SourceRef

    @field_validator("tags")
    @classmethod
    def reject_duplicate_tags(
        cls, tags: tuple[CapabilityTag, ...]
    ) -> tuple[CapabilityTag, ...]:
        if len(set(tags)) != len(tags):
            raise ValueError("duplicate metadata tags")
        return tags
