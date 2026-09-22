from __future__ import annotations

import hashlib
import shutil
import stat
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from platformdirs import PlatformDirs
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .persistence import write_bytes_atomic, write_text_atomic

DOMAIN_KNOWLEDGE_STATE_DIRECTORY = "domain-knowledge"
DOMAIN_KNOWLEDGE_ALLOWED_PATTERNS = (
    "*.md",
    "*.txt",
    "*.py",
    "*.inp",
    "*.json",
    "*.yaml",
    "*.yml",
)
_ALLOWED_SUFFIXES = frozenset({".md", ".txt", ".py", ".inp", ".json", ".yaml", ".yml"})
_PACK_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_RESOURCE_BYTES = 2 * 1024 * 1024
_MAX_PACK_BYTES = 20 * 1024 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DomainKnowledgeSource(_StrictModel):
    kind: Literal[
        "original",
        "vendor_documentation",
        "paper",
        "repository",
        "internal_note",
        "other",
    ]
    title: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, min_length=1, max_length=2000)
    version: str | None = Field(default=None, min_length=1, max_length=200)
    license: str | None = Field(default=None, min_length=1, max_length=500)
    notes: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("title", "reference", "version", "license", "notes")
    @classmethod
    def no_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("source text must not be blank")
        return value


class DomainKnowledgeValidation(_StrictModel):
    content_review: Literal["unreviewed", "reviewed"] = "unreviewed"
    runtime: Literal["not_run", "abaqus_2024_observed"] = "not_run"
    physics: Literal["not_checked", "case_checked"] = "not_checked"
    task_benefit: Literal["not_evaluated", "case_observed"] = "not_evaluated"
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=64)
    notes: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("validation evidence_ids must be unique")
        if any(not value.strip() for value in values):
            raise ValueError("validation evidence_ids must not be blank")
        return values

    @field_validator("notes")
    @classmethod
    def no_blank_notes(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("validation notes must not be blank")
        return value


class DomainKnowledgeResource(_StrictModel):
    path: str = Field(min_length=1, max_length=500)
    kind: Literal[
        "guide",
        "reference",
        "example",
        "script",
        "input",
        "data",
        "other",
    ]
    title: str | None = Field(default=None, min_length=1, max_length=500)
    source: DomainKnowledgeSource
    validation: DomainKnowledgeValidation = Field(
        default_factory=DomainKnowledgeValidation
    )

    @field_validator("path")
    @classmethod
    def valid_resource_path(cls, value: str) -> str:
        return _normalize_relative_path(value)

    @field_validator("title")
    @classmethod
    def no_blank_title(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("resource title must not be blank")
        return value


class DomainKnowledgePackManifest(_StrictModel):
    schema_version: Literal["domain-knowledge-pack-v1"] = "domain-knowledge-pack-v1"
    pack_id: str = Field(pattern=_PACK_ID_PATTERN)
    release: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)
    entrypoints: tuple[str, ...] = Field(min_length=1, max_length=32)
    compatibility: tuple[str, ...] = Field(default=(), max_length=64)
    resources: tuple[DomainKnowledgeResource, ...] = Field(min_length=1, max_length=512)

    @field_validator("release", "summary")
    @classmethod
    def no_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("pack text fields must not be blank")
        return value

    @field_validator("entrypoints")
    @classmethod
    def valid_entrypoints(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_normalize_relative_path(value) for value in values)
        if len(normalized) != len(set(normalized)):
            raise ValueError("pack entrypoints must be unique")
        return normalized

    @field_validator("compatibility")
    @classmethod
    def valid_compatibility(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("pack compatibility entries must be unique")
        if any(not value.strip() for value in values):
            raise ValueError("pack compatibility entries must not be blank")
        return values

    @model_validator(mode="after")
    def coherent_resources(self) -> DomainKnowledgePackManifest:
        paths = tuple(resource.path for resource in self.resources)
        if len(paths) != len(set(paths)):
            raise ValueError("pack resource paths must be unique")
        missing = set(self.entrypoints) - set(paths)
        if missing:
            raise ValueError(
                "pack entrypoints must be listed resources: "
                + ", ".join(sorted(missing))
            )
        return self


class DomainKnowledgeFileBinding(_StrictModel):
    relative_path: str = Field(min_length=1, max_length=500)
    size_bytes: int = Field(ge=0, le=_MAX_RESOURCE_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("relative_path")
    @classmethod
    def valid_relative_path(cls, value: str) -> str:
        return _normalize_relative_path(value)


class DomainKnowledgePackBinding(_StrictModel):
    pack_id: str = Field(pattern=_PACK_ID_PATTERN)
    release: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)
    entrypoints: tuple[str, ...] = Field(min_length=1, max_length=32)
    compatibility: tuple[str, ...] = Field(default=(), max_length=64)
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: tuple[DomainKnowledgeFileBinding, ...] = Field(min_length=1, max_length=512)
    pack_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_relative_path: str = Field(min_length=1, max_length=500)

    @field_validator("snapshot_relative_path")
    @classmethod
    def valid_snapshot_path(cls, value: str) -> str:
        normalized = _normalize_relative_path(value, require_supported_suffix=False)
        expected_prefix = f"{DOMAIN_KNOWLEDGE_STATE_DIRECTORY}/packs/"
        if not normalized.startswith(expected_prefix):
            raise ValueError("pack snapshot path is outside the owned state directory")
        return normalized

    @model_validator(mode="after")
    def coherent_binding(self) -> DomainKnowledgePackBinding:
        if self.snapshot_relative_path != (
            f"{DOMAIN_KNOWLEDGE_STATE_DIRECTORY}/packs/{self.pack_id}"
        ):
            raise ValueError("pack snapshot path does not match pack_id")
        paths = tuple(file.relative_path for file in self.files)
        if len(paths) != len(set(paths)):
            raise ValueError("pack file bindings must be unique")
        if set(self.entrypoints) - set(paths):
            raise ValueError("pack binding entrypoints must be bound files")
        return self


class DomainKnowledgeBundleBinding(_StrictModel):
    schema_version: Literal["domain-knowledge-bundle-v1"] = "domain-knowledge-bundle-v1"
    packs: tuple[DomainKnowledgePackBinding, ...] = Field(min_length=1, max_length=32)
    bundle_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def ordered_unique_packs(self) -> DomainKnowledgeBundleBinding:
        ids = tuple(pack.pack_id for pack in self.packs)
        if ids != tuple(sorted(ids)):
            raise ValueError("bundle packs must be ordered by pack_id")
        if len(ids) != len(set(ids)):
            raise ValueError("bundle pack_ids must be unique")
        return self


class DomainKnowledgePackInfo(_StrictModel):
    pack_id: str = Field(pattern=_PACK_ID_PATTERN)
    release: str
    summary: str
    entrypoints: tuple[str, ...]
    compatibility: tuple[str, ...] = ()


class DomainKnowledgePackIssue(_StrictModel):
    directory: str
    error: str


class DomainKnowledgeInventory(_StrictModel):
    root: str
    packs: tuple[DomainKnowledgePackInfo, ...] = ()
    issues: tuple[DomainKnowledgePackIssue, ...] = ()


@dataclass(frozen=True)
class _LoadedResource:
    spec: DomainKnowledgeResource
    content: bytes
    binding: DomainKnowledgeFileBinding


@dataclass(frozen=True)
class _LoadedPack:
    manifest: DomainKnowledgePackManifest
    manifest_bytes: bytes
    resources: tuple[_LoadedResource, ...]


@dataclass(frozen=True)
class MaterializedDomainKnowledge:
    root: Path
    binding: DomainKnowledgeBundleBinding


def domain_knowledge_packs_root() -> Path:
    dirs = PlatformDirs(appname="abaqus-sim-agent", appauthor=False)
    return dirs.user_data_path / "knowledge" / "packs"


def discover_domain_knowledge_packs(
    root: Path | None = None,
) -> DomainKnowledgeInventory:
    selected_root = (root or domain_knowledge_packs_root()).absolute()
    if not selected_root.exists():
        return DomainKnowledgeInventory(root=str(selected_root))
    if not selected_root.is_dir():
        return DomainKnowledgeInventory(
            root=str(selected_root),
            issues=(
                DomainKnowledgePackIssue(
                    directory=selected_root.name,
                    error="knowledge pack root is not a directory",
                ),
            ),
        )
    packs: list[DomainKnowledgePackInfo] = []
    issues: list[DomainKnowledgePackIssue] = []
    for candidate in sorted(selected_root.iterdir(), key=lambda item: item.name):
        if candidate.name.startswith("."):
            continue
        try:
            loaded = _load_pack(candidate)
        except (OSError, ValueError) as exc:
            issues.append(
                DomainKnowledgePackIssue(
                    directory=candidate.name,
                    error=str(exc),
                )
            )
            continue
        manifest = loaded.manifest
        packs.append(
            DomainKnowledgePackInfo(
                pack_id=manifest.pack_id,
                release=manifest.release,
                summary=manifest.summary,
                entrypoints=manifest.entrypoints,
                compatibility=manifest.compatibility,
            )
        )
    return DomainKnowledgeInventory(
        root=str(selected_root), packs=tuple(packs), issues=tuple(issues)
    )


def prepare_domain_knowledge_bundle(
    pack_ids: Sequence[str],
    *,
    state_dir: Path,
    workspace: Path,
    packs_root: Path | None = None,
) -> DomainKnowledgeBundleBinding | None:
    normalized_ids = tuple(sorted(pack_ids))
    if not normalized_ids:
        return None
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ValueError("knowledge pack ids must be unique")
    for pack_id in normalized_ids:
        if not _valid_pack_id(pack_id):
            raise ValueError(f"invalid knowledge pack id: {pack_id}")

    state = state_dir.resolve()
    workspace = workspace.resolve()
    root = (packs_root or domain_knowledge_packs_root()).absolute()
    _ensure_plain_directory(root, must_exist=True)
    resolved_root = root.resolve(strict=True)
    if _paths_overlap(resolved_root, workspace):
        raise ValueError("knowledge pack root must not overlap the Agent workspace")
    if _paths_overlap(resolved_root, state):
        raise ValueError("knowledge pack root must not overlap CodingRun state")

    destination = state / DOMAIN_KNOWLEDGE_STATE_DIRECTORY
    from .resume import ensure_new_run_state_available

    if destination.exists():
        ensure_new_run_state_available(state, prepared_domain_knowledge=True)
        binding = _load_stored_binding(state)
        if tuple(pack.pack_id for pack in binding.packs) != normalized_ids:
            raise ValueError(
                "state already contains a different prepared knowledge bundle"
            )
        validate_domain_knowledge_bundle(state, binding)
        return binding
    ensure_new_run_state_available(state)

    loaded = tuple(_load_pack(resolved_root / pack_id) for pack_id in normalized_ids)
    temporary = state / f".{DOMAIN_KNOWLEDGE_STATE_DIRECTORY}.{uuid4().hex}.tmp"
    temporary.mkdir(parents=True)
    try:
        bindings: list[DomainKnowledgePackBinding] = []
        for pack in loaded:
            manifest = pack.manifest
            pack_destination = temporary / "packs" / manifest.pack_id
            pack_destination.mkdir(parents=True)
            write_bytes_atomic(pack_destination / "pack.json", pack.manifest_bytes)
            for resource in pack.resources:
                write_bytes_atomic(
                    pack_destination / Path(resource.spec.path), resource.content
                )
            manifest_sha256 = _sha256_bytes(pack.manifest_bytes)
            files = tuple(resource.binding for resource in pack.resources)
            pack_digest = _pack_digest(manifest_sha256, files)
            bindings.append(
                DomainKnowledgePackBinding(
                    pack_id=manifest.pack_id,
                    release=manifest.release,
                    summary=manifest.summary,
                    entrypoints=manifest.entrypoints,
                    compatibility=manifest.compatibility,
                    manifest_sha256=manifest_sha256,
                    files=files,
                    pack_digest=pack_digest,
                    snapshot_relative_path=(
                        f"{DOMAIN_KNOWLEDGE_STATE_DIRECTORY}/packs/{manifest.pack_id}"
                    ),
                )
            )
        pack_bindings = tuple(bindings)
        binding = DomainKnowledgeBundleBinding(
            packs=pack_bindings,
            bundle_digest=_bundle_digest(pack_bindings),
        )
        write_text_atomic(temporary / "bundle.json", binding.model_dump_json(indent=2))
        temporary.replace(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    validate_domain_knowledge_bundle(state, binding)
    return binding


def validate_domain_knowledge_bundle(
    state_dir: Path, binding: DomainKnowledgeBundleBinding
) -> None:
    stored = _load_stored_binding(state_dir)
    if stored != binding:
        raise ValueError("knowledge bundle binding differs from launch.json")
    if _bundle_digest(binding.packs) != binding.bundle_digest:
        raise ValueError("knowledge bundle digest is invalid")
    state = state_dir.resolve(strict=True)
    for pack in binding.packs:
        root = state / Path(pack.snapshot_relative_path)
        _ensure_plain_directory(root, must_exist=True)
        manifest_path = _plain_file(root, "pack.json", allow_manifest=True)
        if _sha256_file(manifest_path) != pack.manifest_sha256:
            raise ValueError(f"knowledge pack manifest is corrupt: {pack.pack_id}")
        manifest = DomainKnowledgePackManifest.model_validate_json(
            manifest_path.read_bytes()
        )
        if (
            manifest.pack_id != pack.pack_id
            or manifest.release != pack.release
            or manifest.summary != pack.summary
            or manifest.entrypoints != pack.entrypoints
            or manifest.compatibility != pack.compatibility
            or tuple(resource.path for resource in manifest.resources)
            != tuple(file.relative_path for file in pack.files)
        ):
            raise ValueError(f"knowledge pack manifest binding changed: {pack.pack_id}")
        expected = {"pack.json", *(file.relative_path for file in pack.files)}
        actual = {
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file()
        }
        if actual != expected:
            raise ValueError(
                f"knowledge pack snapshot contents changed: {pack.pack_id}"
            )
        for file in pack.files:
            path = _plain_file(root, file.relative_path)
            if path.stat().st_size != file.size_bytes:
                raise ValueError(f"knowledge pack file size changed: {pack.pack_id}")
            if _sha256_file(path) != file.sha256:
                raise ValueError(f"knowledge pack file is corrupt: {pack.pack_id}")
        if _pack_digest(pack.manifest_sha256, pack.files) != pack.pack_digest:
            raise ValueError(f"knowledge pack digest is invalid: {pack.pack_id}")


@contextmanager
def materialize_domain_knowledge_bundle(
    state_dir: Path,
    binding: DomainKnowledgeBundleBinding | None,
) -> Iterator[MaterializedDomainKnowledge | None]:
    if binding is None:
        yield None
        return
    validate_domain_knowledge_bundle(state_dir, binding)
    with tempfile.TemporaryDirectory(prefix="abaqus-domain-knowledge-") as temporary:
        root = Path(temporary)
        state = state_dir.resolve(strict=True)
        for pack in binding.packs:
            source_root = state / Path(pack.snapshot_relative_path)
            destination_root = root / pack.pack_id
            for file in pack.files:
                destination = destination_root / Path(file.relative_path)
                content = _plain_file(source_root, file.relative_path).read_bytes()
                if (
                    len(content) != file.size_bytes
                    or _sha256_bytes(content) != file.sha256
                ):
                    raise ValueError(
                        f"knowledge pack changed while materializing: {pack.pack_id}"
                    )
                write_bytes_atomic(
                    destination,
                    content,
                )
                destination.chmod(stat.S_IREAD)
        catalog = root / "_catalog.md"
        write_text_atomic(catalog, _catalog_text(binding))
        catalog.chmod(stat.S_IREAD)
        yield MaterializedDomainKnowledge(root=root, binding=binding)


def _load_pack(directory: Path) -> _LoadedPack:
    _ensure_plain_directory(directory, must_exist=True)
    root = directory.resolve(strict=True)
    manifest_path = _plain_file(root, "pack.json", allow_manifest=True)
    manifest_bytes = manifest_path.read_bytes()
    if len(manifest_bytes) > _MAX_MANIFEST_BYTES:
        raise ValueError("pack.json is too large")
    manifest = DomainKnowledgePackManifest.model_validate_json(manifest_bytes)
    if root.name != manifest.pack_id:
        raise ValueError("pack directory name must match pack_id")
    resources: list[_LoadedResource] = []
    total = 0
    for spec in manifest.resources:
        path = _plain_file(root, spec.path)
        before = path.stat()
        if before.st_size > _MAX_RESOURCE_BYTES:
            raise ValueError(f"knowledge resource is too large: {spec.path}")
        content = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"knowledge resource changed while reading: {spec.path}")
        if b"\x00" in content:
            raise ValueError(f"knowledge resource is not plain text: {spec.path}")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"knowledge resource must be UTF-8 text: {spec.path}"
            ) from exc
        total += len(content)
        if total > _MAX_PACK_BYTES:
            raise ValueError("knowledge pack is too large")
        resources.append(
            _LoadedResource(
                spec=spec,
                content=content,
                binding=DomainKnowledgeFileBinding(
                    relative_path=spec.path,
                    size_bytes=len(content),
                    sha256=_sha256_bytes(content),
                ),
            )
        )
    if manifest_path.read_bytes() != manifest_bytes:
        raise ValueError("pack.json changed while reading")
    return _LoadedPack(manifest, manifest_bytes, tuple(resources))


def _load_stored_binding(state_dir: Path) -> DomainKnowledgeBundleBinding:
    path = state_dir.resolve() / DOMAIN_KNOWLEDGE_STATE_DIRECTORY / "bundle.json"
    if not path.is_file():
        raise ValueError("knowledge bundle snapshot is missing")
    return DomainKnowledgeBundleBinding.model_validate_json(path.read_bytes())


def _plain_file(
    root: Path, relative_path: str, *, allow_manifest: bool = False
) -> Path:
    normalized = (
        "pack.json"
        if allow_manifest and relative_path == "pack.json"
        else _normalize_relative_path(relative_path)
    )
    current = root
    for part in PurePosixPath(normalized).parts:
        current = current / part
        if current.is_symlink() or _is_junction(current):
            raise ValueError(
                f"knowledge resource path contains a link: {relative_path}"
            )
    if not current.is_file():
        raise ValueError(f"knowledge resource is missing: {relative_path}")
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise ValueError(f"knowledge resource escapes its pack: {relative_path}")
    return resolved


def _ensure_plain_directory(path: Path, *, must_exist: bool) -> None:
    absolute = path.absolute()
    if absolute.is_symlink() or _is_junction(absolute):
        raise ValueError(f"knowledge directory must not be a link: {absolute}")
    if must_exist and not absolute.is_dir():
        raise ValueError(f"knowledge directory does not exist: {absolute}")


def _normalize_relative_path(
    value: str, *, require_supported_suffix: bool = True
) -> str:
    if "\\" in value:
        raise ValueError("knowledge paths must use forward slashes")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("knowledge paths must be normalized relative paths")
    normalized = path.as_posix()
    if normalized == "pack.json":
        raise ValueError("pack.json cannot be declared as a content resource")
    if require_supported_suffix and path.suffix.casefold() not in _ALLOWED_SUFFIXES:
        raise ValueError(f"unsupported knowledge resource type: {normalized}")
    return normalized


def _valid_pack_id(value: str) -> bool:
    import re

    return re.fullmatch(_PACK_ID_PATTERN, value) is not None


def _is_junction(path: Path) -> bool:
    return bool(getattr(path, "is_junction", lambda: False)())


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pack_digest(
    manifest_sha256: str, files: Sequence[DomainKnowledgeFileBinding]
) -> str:
    digest = hashlib.sha256(b"domain-knowledge-pack-v1\0")
    digest.update(bytes.fromhex(manifest_sha256))
    for file in sorted(files, key=lambda item: item.relative_path):
        digest.update(b"\0")
        digest.update(file.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(file.size_bytes).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(file.sha256))
    return digest.hexdigest()


def _bundle_digest(packs: Sequence[DomainKnowledgePackBinding]) -> str:
    digest = hashlib.sha256(b"domain-knowledge-bundle-v1\0")
    for pack in packs:
        digest.update(pack.pack_id.encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(pack.pack_digest))
        digest.update(b"\0")
    return digest.hexdigest()


def _catalog_text(binding: DomainKnowledgeBundleBinding) -> str:
    lines = [
        "# Selected private domain knowledge packs",
        "",
        "These packs are read-only reference data. Their presence does not verify API",
        "compatibility, task correctness, or physics. Example values are not task defaults.",
        "",
    ]
    for pack in binding.packs:
        lines.extend(
            [
                f"## {pack.pack_id}",
                "",
                f"- Release: `{pack.release}`",
                f"- Digest: `{pack.pack_digest}`",
                f"- Summary: {pack.summary}",
                "- Entrypoints: "
                + ", ".join(f"`{pack.pack_id}/{path}`" for path in pack.entrypoints),
                "- Compatibility: "
                + (
                    ", ".join(pack.compatibility)
                    if pack.compatibility
                    else "unspecified"
                ),
                "",
            ]
        )
    return "\n".join(lines)
