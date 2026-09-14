"""宿主文档策略的显式配置和不可变run绑定；不依赖evaluation实现。"""

import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import Field, model_validator

from abaqus_sim_agent.retrieval.evidence_contracts import Binding, Contract, Digest

from .document_policy_contracts import DocumentScope, HostNeed


class DeclaredDocumentNeed(HostNeed):
    clarification_field_id: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]*$"
    )

    @model_validator(mode="after")
    def input_identity(self):
        if (self.kind == "physical_input") != (self.clarification_field_id is not None):
            raise ValueError(
                "physical input needs require a clarification field id; knowledge needs must omit it"
            )
        return self

    def host_need(self) -> HostNeed:
        return HostNeed.model_validate(
            self.model_dump(exclude={"clarification_field_id"})
        )


class DeclaredIndependentOperation(Contract):
    kind: Literal["cae", "abaqus_python", "solver_input"]
    relative_path: str = Field(min_length=1, max_length=240)
    sha256: Digest
    independent_of_conditions: tuple[str, ...] = Field(min_length=1, max_length=8)
    host_reference: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def safe_path(self):
        value = self.relative_path.replace("\\", "/")
        path, windows = PurePosixPath(value), PureWindowsPath(value)
        suffix = ".inp" if self.kind == "solver_input" else ".py"
        if (
            path.is_absolute()
            or windows.drive
            or ".." in path.parts
            or path.suffix != suffix
        ):
            raise ValueError(
                "independent operation requires a relative production path with the matching suffix"
            )
        object.__setattr__(self, "relative_path", path.as_posix())
        return self

    @property
    def operation_id(self) -> str:
        return (
            "operation-"
            + hashlib.sha256(
                (
                    self.kind + ":" + self.relative_path.casefold() + ":" + self.sha256
                ).encode()
            ).hexdigest()
        )


class DocumentPolicySpec(Contract):
    schema_version: Literal["document-policy-spec-v1"] = "document-policy-spec-v1"
    candidate: Path
    symbol_index: Path
    scope_rules: Path
    ranker_directory: Path
    scope: DocumentScope
    host_needs: tuple[DeclaredDocumentNeed, ...] = Field(default=(), max_length=8)
    independent_operations: tuple[DeclaredIndependentOperation, ...] = Field(
        default=(), max_length=8
    )

    @model_validator(mode="after")
    def unique_needs(self):
        conditions = [" ".join(n.condition.split()).casefold() for n in self.host_needs]
        fields = [
            n.clarification_field_id
            for n in self.host_needs
            if n.clarification_field_id
        ]
        if len(set(conditions)) != len(conditions) or len(set(fields)) != len(fields):
            raise ValueError("duplicate host need or clarification field")
        for operation in self.independent_operations:
            if not {
                " ".join(c.split()).casefold()
                for c in operation.independent_of_conditions
            } <= set(conditions):
                raise ValueError("independence must refer to declared host needs")
        if len({op.operation_id for op in self.independent_operations}) != len(
            self.independent_operations
        ):
            raise ValueError("duplicate independent operation identity")
        return self


class DocumentPolicyBinding(Contract):
    mode: Literal["bounded_v1"] = "bounded_v1"
    spec: DocumentPolicySpec
    evidence_binding: Binding

    @model_validator(mode="after")
    def absolute_paths(self):
        for name in ("candidate", "symbol_index", "scope_rules", "ranker_directory"):
            path = getattr(self.spec, name)
            if not path.is_absolute() or path.resolve() != path:
                raise ValueError(
                    "document backend paths must be resolved absolute paths"
                )
        return self


def load_document_policy_spec(path: Path) -> DocumentPolicySpec:
    """严格读取单个显式JSON，路径相对配置文件；不加载环境覆盖或回写源文件。"""
    spec = DocumentPolicySpec.model_validate_json(path.read_bytes())
    values = spec.model_dump()
    for name in ("candidate", "symbol_index", "scope_rules", "ranker_directory"):
        values[name] = (path.resolve().parent / values[name]).resolve()
    return DocumentPolicySpec.model_validate(values)
