from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ApiEntityKind = Literal["method", "constructor", "command"]
ApiParameterKind = Literal[
    "unknown",
    "positional_only",
    "positional_or_keyword",
    "var_positional",
    "keyword_only",
    "var_keyword",
]
ApiReleaseAlignment = Literal["matched", "mismatch", "unknown"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ApiSourceRef(_StrictModel):
    kind: Literal["official", "abqpy", "runtime"]
    release: str = Field(min_length=1)
    version: str | None = Field(default=None, min_length=1)
    source_path: str = Field(min_length=1)
    source_url: str | None = Field(default=None, min_length=1)
    document_id: str | None = Field(default=None, min_length=1)
    section_id: str | None = Field(default=None, min_length=1)


class ApiParameter(_StrictModel):
    name: str = Field(min_length=1)
    kind: ApiParameterKind = "unknown"
    annotation: str | None = Field(default=None, min_length=1)
    default: str | None = None
    required: bool
    description: str = ""


class ApiSignature(_StrictModel):
    text: str = Field(min_length=1)
    parameters: tuple[ApiParameter, ...] = ()
    return_annotation: str | None = Field(default=None, min_length=1)
    overload: bool = False


class ApiClassNode(_StrictModel):
    qualified_name: str = Field(min_length=1)
    class_name: str = Field(min_length=1)
    bases: tuple[str, ...] = ()
    public_names: tuple[str, ...] = ()


class ApiReferenceText(_StrictModel):
    kind: Literal["object_description", "table_data"]
    text: str = Field(min_length=1)
    raw_xhtml: str = Field(min_length=1)
    source: ApiSourceRef
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_warnings: tuple[str, ...] = ()


class OfficialApiRecord(_StrictModel):
    record_id: str = Field(min_length=1)
    entity_kind: ApiEntityKind
    owner_name: str = Field(min_length=1)
    callable_name: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    public_names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    summary: str = ""
    access: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    required_parameters: tuple[ApiParameter, ...] = ()
    optional_parameters: tuple[ApiParameter, ...] = ()
    return_description: str = ""
    exceptions: tuple[str, ...] = ()
    source: ApiSourceRef
    context: tuple[ApiReferenceText, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class ApiDocstring(_StrictModel):
    raw: str | None
    summary: str = ""
    parameters: dict[str, str] = Field(default_factory=dict)
    parse_status: Literal["absent", "summary_only", "parameters_parsed", "partial"]
    source_line: int = Field(ge=1)
    source_end_line: int = Field(ge=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    docstring_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def check_content(self):
        if self.source_end_line < self.source_line:
            raise ValueError("docstring source range reversed")
        expected = (
            hashlib.sha256(self.raw.encode("utf-8")).hexdigest()
            if self.raw is not None
            else None
        )
        if expected != self.docstring_sha256:
            raise ValueError("docstring checksum mismatch")
        if (self.raw is None) != (self.parse_status == "absent"):
            raise ValueError("absent docstring state mismatch")
        if self.raw is None and (self.summary or self.parameters):
            raise ValueError("absent docstring cannot contain parsed text")
        return self


class AbqpyApiRecord(_StrictModel):
    record_id: str = Field(min_length=1)
    entity_kind: ApiEntityKind
    declaring_name: str = Field(min_length=1)
    class_name: str = Field(min_length=1)
    method_name: str = Field(min_length=1)
    public_names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    signatures: tuple[ApiSignature, ...] = Field(min_length=1)
    bases: tuple[str, ...] = ()
    class_graph: tuple[ApiClassNode, ...] = ()
    source: ApiSourceRef
    documentation: tuple[ApiDocstring, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def documentation_matches_signatures(self):
        if self.documentation is not None and len(self.documentation) != len(
            self.signatures
        ):
            raise ValueError("documentation must align one-to-one with signatures")
        return self


class ApiSourceConflict(_StrictModel):
    field_name: str = Field(min_length=1)
    official_value: str = Field(min_length=1)
    abqpy_value: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ApiRuntimeNote(_StrictModel):
    note_id: str = Field(min_length=1)
    qualified_name: str = Field(min_length=1)
    runtime_release: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    guidance: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)
    error_fingerprint: str | None = Field(default=None, min_length=1)
    applies_to_parameters: tuple[str, ...] = ()
    verified: bool = False
    source: ApiSourceRef

    @model_validator(mode="after")
    def check_runtime_source(self) -> "ApiRuntimeNote":
        if self.source.kind != "runtime":
            raise ValueError("runtime note source kind must be `runtime`")
        if self.source.release != self.runtime_release:
            raise ValueError("runtime note release must match its source release")
        return self


class ApiMethodCard(_StrictModel):
    card_id: str = Field(min_length=1)
    entity_kind: ApiEntityKind
    canonical_name: str = Field(min_length=1)
    declaring_name: str = Field(min_length=1)
    class_name: str = Field(min_length=1)
    method_name: str = Field(min_length=1)
    public_names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    signatures: tuple[ApiSignature, ...] = ()
    official_records: tuple[OfficialApiRecord, ...] = ()
    abqpy_records: tuple[AbqpyApiRecord, ...] = ()
    runtime_notes: tuple[ApiRuntimeNote, ...] = ()
    conflicts: tuple[ApiSourceConflict, ...] = ()
    release_alignment: ApiReleaseAlignment = "unknown"

    def all_names(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    self.canonical_name,
                    self.declaring_name,
                    *self.public_names,
                    *self.aliases,
                )
            )
        )


class ApiMemberCard(_StrictModel):
    card_id: str = Field(min_length=1)
    entity_kind: Literal["member"] = "member"
    canonical_name: str = Field(min_length=1)
    declaring_name: str = Field(min_length=1)
    member_name: str = Field(min_length=1)
    public_names: tuple[str, ...] = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    annotation: str = Field(min_length=1)
    description: str = ""
    source: ApiSourceRef
    source_line: int = Field(ge=1)
    source_end_line: int = Field(ge=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    distribution_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_kind: Literal["abqpy_static_declaration"] = "abqpy_static_declaration"
    release_alignment: ApiReleaseAlignment = "unknown"

    @model_validator(mode="after")
    def check_source(self) -> "ApiMemberCard":
        if self.source.kind != "abqpy" or self.source.version is None:
            raise ValueError("member cards require a versioned abqpy source")
        if self.source_end_line < self.source_line:
            raise ValueError("member source range is reversed")
        return self

    def all_names(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                (
                    self.canonical_name,
                    self.declaring_name,
                    *self.public_names,
                    *self.aliases,
                )
            )
        )


class ApiLookupResult(_StrictModel):
    status: Literal["found", "ambiguous", "not_found"]
    query: str = Field(min_length=1)
    cards: tuple[ApiMethodCard, ...] = ()
    members: tuple[ApiMemberCard, ...] = ()
    parameter_name: str | None = Field(default=None, min_length=1)
    suggestions: tuple[str, ...] = ()
    documentation_requested: bool = Field(
        default=False, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def check_status_payload(self) -> "ApiLookupResult":
        count = len(self.cards) + len(self.members)
        if self.status == "found" and count != 1:
            raise ValueError("found lookup must contain exactly one card")
        if self.status == "ambiguous" and count < 2:
            raise ValueError("ambiguous lookup must contain at least two cards")
        if self.status == "not_found" and count:
            raise ValueError("not_found lookup cannot contain cards")
        if self.parameter_name is not None and (
            self.status != "found" or len(self.cards) != 1
        ):
            raise ValueError("parameter lookup must resolve to exactly one card")
        return self


class ApiCatalogManifest(_StrictModel):
    schema_version: str = "1"
    built_at: datetime
    runtime_release: str = Field(min_length=1)
    official_release: str | None = Field(default=None, min_length=1)
    abqpy_version: str | None = Field(default=None, min_length=1)
    cards_file: str = "methods.jsonl"
    card_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    cards_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    member_count: int = Field(default=0, ge=0)
    members_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_digests: dict[str, str] = Field(default_factory=dict)
    sources: tuple[ApiSourceRef, ...] = ()

    @model_validator(mode="after")
    def check_source_digests(self) -> "ApiCatalogManifest":
        if self.schema_version not in {"1", "2"}:
            raise ValueError("unsupported API catalog schema version")
        if self.schema_version == "1" and (self.member_count or self.members_sha256):
            raise ValueError("member sidecars require API catalog schema version 2")
        if self.schema_version == "2" and self.members_sha256 is None:
            raise ValueError("schema version 2 requires a member sidecar digest")
        if any(
            not name or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for name, digest in self.source_digests.items()
        ):
            raise ValueError("source digests must use non-empty names and SHA-256")
        return self
