from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .bm25 import BM25IndexError
from .heldout import (
    AuditedHeldout,
    HeldoutAuditReport,
    HeldoutEvaluation,
    HeldoutSeal,
    audit_heldout,
    evaluate_heldout,
)
from .index import DEFAULT_COLLECTION_NAME, AbaqusDocumentIndex
from .minilm import MiniLMEncoder

_REPRODUCTION_SOURCE = "src/abaqus_sim_agent/retrieval/reproduction.py"
_RUNTIME_PACKAGES = (
    "numpy",
    "onnxruntime",
    "tokenizers",
)
_QUERY_ENCODER_TYPE = "sentence-transformers/all-MiniLM-L6-v2"
_QUERY_ENCODER_REQUIRED_FILES = (
    "model.onnx",
    "tokenizer.json",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactBinding(_StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExternalFileBinding(_StrictModel):
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DenseVectorFile(_StrictModel):
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DenseVectorStorageSnapshot(_StrictModel):
    algorithm: Literal["numpy-flat-cosine-files-v1"]
    collection_name: str = Field(min_length=1)
    collection_id: str = Field(min_length=1)
    dimension: int = Field(gt=0)
    collection_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    collection_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    segment_ids: tuple[str, ...] = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    files: tuple[DenseVectorFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_ordering(self) -> DenseVectorStorageSnapshot:
        if list(self.segment_ids) != sorted(set(self.segment_ids)):
            raise ValueError("Dense vector segment IDs must be sorted and unique")
        paths = [item.path for item in self.files]
        if paths != sorted(set(paths)):
            raise ValueError("Dense vector file paths must be sorted and unique")
        return self


class HeldoutReproductionSeal(_StrictModel):
    schema_version: Literal["2"] = "2"
    dataset_id: str = Field(min_length=1)
    purpose: Literal["identical_reproduction"]
    status: Literal["sealed"]
    parent_seal: ArtifactBinding
    reference_report: ArtifactBinding
    reference_attempts: ArtifactBinding
    reference_consumption: ArtifactBinding
    reference_posthoc_vector_snapshot: ArtifactBinding
    uv_lock: ArtifactBinding
    python_version: str = Field(min_length=1)
    runtime_versions: dict[str, str]
    query_encoder_type: Literal["sentence-transformers/all-MiniLM-L6-v2"]
    query_encoder_root: str = Field(min_length=1)
    query_encoder_files: tuple[ExternalFileBinding, ...] = Field(min_length=1)
    onnx_providers: tuple[str, ...] = Field(min_length=1)
    dense_vector_storage: DenseVectorStorageSnapshot
    reproduction_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sealed_at: datetime

    @model_validator(mode="after")
    def validate_environment_contract(self) -> HeldoutReproductionSeal:
        if set(self.runtime_versions) != set(_RUNTIME_PACKAGES):
            raise ValueError("reproduction seal runtime package set is incomplete")
        paths = [item.path for item in self.query_encoder_files]
        if tuple(paths) != _QUERY_ENCODER_REQUIRED_FILES:
            raise ValueError("query encoder files must be the exact MiniLM runtime set")
        if not Path(self.query_encoder_root).is_absolute():
            raise ValueError("query encoder root must be absolute")
        if len(set(self.onnx_providers)) != len(self.onnx_providers):
            raise ValueError("ONNX providers must be unique and ordered")
        return self


class ReproductionAuditSummary(_StrictModel):
    dataset_id: str
    reproduction_seal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_seal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dense_vector_storage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dense_vector_files_verified: int = Field(gt=0)
    query_encoder_files_verified: int = Field(gt=0)
    heldout: HeldoutAuditReport


@dataclass(frozen=True)
class AuditedReproduction:
    seal: HeldoutReproductionSeal
    seal_sha256: str
    seal_path: Path
    benchmark_root: Path
    repository_root: Path
    parent_seal_path: Path
    dataset: AuditedHeldout
    reference_report: HeldoutEvaluation
    audit: ReproductionAuditSummary


class HeldoutReproductionReport(_StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str
    purpose: Literal["identical_reproduction"]
    reproduced_at: datetime
    reproduction_seal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parent_seal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dense_vector_storage_attested: Literal[True]
    query_encoder_files_attested: Literal[True]
    evaluation: HeldoutEvaluation


class ReproductionComparison(_StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str
    purpose: Literal["identical_reproduction_comparison"]
    reference_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reproduction_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rankings_compared: int = Field(ge=0)
    ranking_mismatches: list[str]
    effect_mismatches: list[str]
    reference_effect_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reproduction_effect_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    identical: bool
    latency_is_identity_gated: Literal[False] = False


def dense_vector_storage_snapshot(
    persist_directory: str | Path,
    *,
    collection_name: str,
) -> DenseVectorStorageSnapshot:
    """对当前 NumPy Flat 文件生成规范快照。

    Args:
        persist_directory: Dense 索引持久目录。
        collection_name: 需要绑定的精确 collection 名称。

    Returns:
        collection投影、segment关联、逐文件hash和规范聚合hash。

    Raises:
        FileNotFoundError: 必需索引文件不存在。
        ValueError: collection/segment不唯一，路径逃逸，或扫描期间发生变化。
    """
    root = Path(persist_directory).resolve(strict=True)
    if (
        not (root / "vectors.npy").is_file()
        or not (root / "documents.sqlite3").is_file()
    ):
        raise FileNotFoundError(f"NumPy Flat index is incomplete: {root}")
    return _flat_vector_storage_snapshot(root, collection_name)


def _flat_vector_storage_snapshot(
    root: Path, collection_name: str
) -> DenseVectorStorageSnapshot:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("collection_name") != collection_name:
        raise ValueError("flat dense collection name does not match its manifest")
    paths = [root / "documents.sqlite3", root / "vectors.npy"]
    aggregate = hashlib.sha256()
    files = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        encoded = relative.encode("utf-8")
        aggregate.update(len(encoded).to_bytes(8, "big"))
        aggregate.update(encoded)
        aggregate.update(len(content).to_bytes(8, "big"))
        aggregate.update(content)
        files.append(
            DenseVectorFile(
                path=relative,
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    return DenseVectorStorageSnapshot(
        algorithm="numpy-flat-cosine-files-v1",
        collection_name=collection_name,
        collection_id=collection_name,
        dimension=int(manifest["vector_dimensions"]),
        collection_config_sha256=_sha256_file(manifest_path),
        collection_schema_sha256=_sha256_text(str(manifest["schema_version"])),
        segment_ids=("numpy-flat",),
        sha256=aggregate.hexdigest(),
        files=tuple(files),
    )


def audit_reproduction(
    index: AbaqusDocumentIndex,
    seal_path: str | Path,
    *,
    repository_root: str | Path,
) -> AuditedReproduction:
    """验证复现seal、原始证据、query encoder与预绑定向量快照。"""
    source = Path(seal_path).resolve(strict=True)
    seal = HeldoutReproductionSeal.model_validate_json(
        source.read_text(encoding="utf-8")
    )
    benchmark_root = source.parent
    repository = Path(repository_root).resolve(strict=True)
    parent_path = _bound_file(benchmark_root, seal.parent_seal.path)
    report_path = _bound_file(benchmark_root, seal.reference_report.path)
    attempts_path = _bound_file(benchmark_root, seal.reference_attempts.path)
    consumption_path = _bound_file(benchmark_root, seal.reference_consumption.path)
    posthoc_path = _bound_file(
        benchmark_root,
        seal.reference_posthoc_vector_snapshot.path,
    )
    _require_hash(parent_path, seal.parent_seal.sha256, "parent held-out seal")
    _require_hash(report_path, seal.reference_report.sha256, "reference report")
    _require_hash(attempts_path, seal.reference_attempts.sha256, "reference attempts")
    _require_hash(
        consumption_path,
        seal.reference_consumption.sha256,
        "reference consumption",
    )
    _require_hash(
        posthoc_path,
        seal.reference_posthoc_vector_snapshot.sha256,
        "post-hoc vector snapshot",
    )
    uv_lock_path = _bound_file(repository, seal.uv_lock.path)
    _require_hash(uv_lock_path, seal.uv_lock.sha256, "uv lock")
    reproduction_source = _bound_file(repository, _REPRODUCTION_SOURCE)
    _require_hash(
        reproduction_source,
        seal.reproduction_source_sha256,
        "reproduction source",
    )
    _verify_runtime(seal)
    _verify_query_encoder(index, seal)

    parent = HeldoutSeal.model_validate_json(parent_path.read_text(encoding="utf-8"))
    reference = HeldoutEvaluation.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    attempts = [
        json.loads(line)
        for line in attempts_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    consumption = json.loads(consumption_path.read_text(encoding="utf-8"))
    posthoc = json.loads(posthoc_path.read_text(encoding="utf-8"))
    if parent.dataset_id != seal.dataset_id or reference.dataset_id != seal.dataset_id:
        raise ValueError("reproduction evidence dataset IDs do not match")
    if reference.seal_sha256 != seal.parent_seal.sha256:
        raise ValueError("reference report does not bind the parent seal")
    if consumption.get("seal_sha256") != seal.parent_seal.sha256:
        raise ValueError("reference consumption does not bind the parent seal")
    if consumption.get("report_sha256") != seal.reference_report.sha256:
        raise ValueError("reference consumption does not bind the reference report")
    if (
        consumption.get("status") != "consumed"
        or consumption.get("post_result_tuning_allowed") is not False
    ):
        raise ValueError("reference consumption does not preserve consumed status")
    if not any(
        item.get("event") == "completed"
        and item.get("report_sha256") == seal.reference_report.sha256
        for item in attempts
    ):
        raise ValueError("reference attempts do not contain the completed first report")
    if posthoc.get("dense_vector_storage_sha256") != seal.dense_vector_storage.sha256:
        raise ValueError("pre-bound vector snapshot differs from post-hoc observation")

    dataset = audit_heldout(index, parent_path, repository_root=repository)
    current_snapshot = dense_vector_storage_snapshot(
        index.persist_directory,
        collection_name=index.collection_name,
    )
    if current_snapshot != seal.dense_vector_storage:
        raise ValueError("Dense vector storage does not match the reproduction seal")
    audit = ReproductionAuditSummary(
        dataset_id=seal.dataset_id,
        reproduction_seal_sha256=_sha256_file(source),
        parent_seal_sha256=seal.parent_seal.sha256,
        reference_report_sha256=seal.reference_report.sha256,
        dense_vector_storage_sha256=current_snapshot.sha256,
        dense_vector_files_verified=len(current_snapshot.files),
        query_encoder_files_verified=len(seal.query_encoder_files),
        heldout=dataset.audit,
    )
    return AuditedReproduction(
        seal=seal,
        seal_sha256=audit.reproduction_seal_sha256,
        seal_path=source,
        benchmark_root=benchmark_root,
        repository_root=repository,
        parent_seal_path=parent_path,
        dataset=dataset,
        reference_report=reference,
        audit=audit,
    )


def run_reproduction(
    index: AbaqusDocumentIndex,
    audited: AuditedReproduction,
    *,
    warmup: bool = True,
) -> HeldoutReproductionReport:
    _verify_reproduction_environment(index, audited)
    evaluation = evaluate_heldout(index, audited.dataset, warmup=warmup)
    _verify_reproduction_environment(index, audited)
    return HeldoutReproductionReport(
        dataset_id=audited.seal.dataset_id,
        purpose="identical_reproduction",
        reproduced_at=datetime.now().astimezone(),
        reproduction_seal_sha256=audited.seal_sha256,
        parent_seal_sha256=audited.seal.parent_seal.sha256,
        reference_report_sha256=audited.seal.reference_report.sha256,
        dense_vector_storage_attested=True,
        query_encoder_files_attested=True,
        evaluation=evaluation,
    )


def compare_reproduction(
    reference: HeldoutEvaluation,
    reproduction: HeldoutEvaluation,
    *,
    reference_report_sha256: str,
    reproduction_report_sha256: str,
) -> ReproductionComparison:
    reference_rankings = _ranking_projection(reference)
    reproduction_rankings = _ranking_projection(reproduction)
    ranking_mismatches = _diff(reference_rankings, reproduction_rankings, "rankings")
    reference_effect = _effect_projection(reference)
    reproduction_effect = _effect_projection(reproduction)
    effect_mismatches = _diff(reference_effect, reproduction_effect, "effects")
    return ReproductionComparison(
        dataset_id=reference.dataset_id,
        purpose="identical_reproduction_comparison",
        reference_report_sha256=reference_report_sha256,
        reproduction_report_sha256=reproduction_report_sha256,
        rankings_compared=sum(
            len(case_rankings) for case_rankings in reference_rankings.values()
        ),
        ranking_mismatches=ranking_mismatches,
        effect_mismatches=effect_mismatches,
        reference_effect_sha256=_canonical_json_sha256(reference_effect),
        reproduction_effect_sha256=_canonical_json_sha256(reproduction_effect),
        identical=not ranking_mismatches and not effect_mismatches,
    )


def _verify_reproduction_environment(
    index: AbaqusDocumentIndex,
    audited: AuditedReproduction,
) -> None:
    _require_hash(
        audited.seal_path,
        audited.seal_sha256,
        "reproduction seal",
    )
    for description, binding in (
        ("parent held-out seal", audited.seal.parent_seal),
        ("reference report", audited.seal.reference_report),
        ("reference attempts", audited.seal.reference_attempts),
        ("reference consumption", audited.seal.reference_consumption),
        (
            "post-hoc vector snapshot",
            audited.seal.reference_posthoc_vector_snapshot,
        ),
    ):
        _require_hash(
            _bound_file(audited.benchmark_root, binding.path),
            binding.sha256,
            description,
        )
    _require_hash(
        _bound_file(audited.repository_root, audited.seal.uv_lock.path),
        audited.seal.uv_lock.sha256,
        "uv lock",
    )
    _require_hash(
        _bound_file(audited.repository_root, _REPRODUCTION_SOURCE),
        audited.seal.reproduction_source_sha256,
        "reproduction source",
    )
    _verify_runtime(audited.seal)
    _verify_query_encoder(index, audited.seal)
    current = dense_vector_storage_snapshot(
        index.persist_directory,
        collection_name=index.collection_name,
    )
    if current != audited.seal.dense_vector_storage:
        raise ValueError("Dense vector storage drifted during reproduction")
    audit_heldout(
        index,
        audited.parent_seal_path,
        repository_root=audited.repository_root,
    )


def _ranking_projection(report: HeldoutEvaluation) -> dict[str, dict[str, list[str]]]:
    return {
        case.case_id: {
            retriever: list(getattr(case, retriever).retrieved_document_ids)
            for retriever in ("bm25", "dense", "hybrid")
        }
        for case in report.cases
    }


def _effect_projection(report: HeldoutEvaluation) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for retriever in ("bm25", "dense", "hybrid"):
        summary = getattr(report, retriever)
        summaries[retriever] = {
            stratum: {
                key: value
                for key, value in getattr(summary, stratum)
                .model_dump(mode="json")
                .items()
                if key not in {"p50_latency_ms", "p95_latency_ms"}
            }
            for stratum in ("all", "concept", "identifier_context", "multi_document")
        }
    cases: list[dict[str, Any]] = []
    for case in report.cases:
        case_projection: dict[str, Any] = {
            "case_id": case.case_id,
            "stratum": case.stratum,
            "query": case.query,
            "requirement_ids": case.requirement_ids,
        }
        for retriever in ("bm25", "dense", "hybrid"):
            ranking = getattr(case, retriever).model_dump(mode="json")
            ranking.pop("elapsed_ms")
            case_projection[retriever] = ranking
        cases.append(case_projection)
    return {
        "dataset_id": report.dataset_id,
        "top_k": report.top_k,
        "fetch_k": report.fetch_k,
        "rrf_k": report.rrf_k,
        "audit": report.audit.model_dump(mode="json"),
        "summaries": summaries,
        "bm25_vs_dense": report.bm25_vs_dense.model_dump(mode="json"),
        "hybrid_vs_dense": report.hybrid_vs_dense.model_dump(mode="json"),
        "hybrid_vs_bm25": report.hybrid_vs_bm25.model_dump(mode="json"),
        "complete_hit_complementarity": report.complete_hit_complementarity.model_dump(
            mode="json"
        ),
        "cases": cases,
    }


def _diff(left: Any, right: Any, path: str) -> list[str]:
    if type(left) is not type(right):
        return [path]
    if isinstance(left, dict):
        if set(left) != set(right):
            return [path]
        result: list[str] = []
        for key in sorted(left):
            result.extend(_diff(left[key], right[key], f"{path}.{key}"))
        return result
    if isinstance(left, list):
        if len(left) != len(right):
            return [path]
        result = []
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            result.extend(_diff(left_item, right_item, f"{path}[{index}]"))
        return result
    return [] if left == right else [path]


def _verify_runtime(seal: HeldoutReproductionSeal) -> None:
    if platform.python_version() != seal.python_version:
        raise ValueError("Python version does not match the reproduction seal")
    current = {
        package: importlib.metadata.version(package) for package in _RUNTIME_PACKAGES
    }
    if current != seal.runtime_versions:
        raise ValueError(
            "runtime dependency versions do not match the reproduction seal"
        )
    onnxruntime = importlib.import_module("onnxruntime")
    providers = tuple(onnxruntime.get_available_providers())
    if providers != seal.onnx_providers:
        raise ValueError("ONNX provider order does not match the reproduction seal")


def _verify_query_encoder(
    index: AbaqusDocumentIndex,
    seal: HeldoutReproductionSeal,
) -> None:
    if not isinstance(index.embeddings, MiniLMEncoder):
        raise ValueError("identical reproduction requires the bound MiniLM encoder")
    manifest = index.read_manifest()
    if manifest.embedding != _QUERY_ENCODER_TYPE:
        raise ValueError("dense manifest does not declare the sealed query encoder")

    root = Path(seal.query_encoder_root).resolve(strict=True)
    if root != index.embeddings.directory:
        raise ValueError("query encoder root does not match the index binding")
    _verify_external_files(root, seal.query_encoder_files)


def _verify_external_files(
    root: Path,
    files: Iterable[ExternalFileBinding],
) -> None:
    for binding in files:
        path = (root / binding.path).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError(f"query encoder file escapes its root: {binding.path}")
        if not path.is_file() or path.stat().st_size != binding.size_bytes:
            raise ValueError(f"query encoder file size mismatch: {path}")
        _require_hash(path, binding.sha256, f"query encoder file {path.name}")


def _bound_file(root: Path, relative_path: str) -> Path:
    path = (root / relative_path).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"artifact path escapes its root: {relative_path}")
    return path


def _require_hash(path: Path, expected: str, description: str) -> None:
    actual = _sha256_file(path)
    if actual != expected:
        raise ValueError(
            f"{description} hash mismatch: expected {expected}, got {actual}"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_text_new_atomic(path: Path, content: str) -> None:
    """原子发布一个不可覆盖的新证据文件。

    Args:
        path: 已确认处于证据目录中的最终路径。
        content: 需要按UTF-8写入的文本。

    Raises:
        ValueError: 最终路径已经存在。
        OSError: 临时文件或同目录硬链接无法创建。
    """
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as exc:
            raise ValueError(f"reproduction output already exists: {path}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def _resolve_output_paths(
    report_output: Path,
    comparison_output: Path,
    *,
    evidence_root: Path,
    index_root: Path,
) -> tuple[Path, Path]:
    """解析并隔离一次复现产生的两个证据路径。

    Args:
        report_output: 复现报告路径。
        comparison_output: 与首次报告逐项比较的结果路径。
        evidence_root: 复现seal所在的受允许证据目录。
        index_root: 不允许被输出污染的检索索引目录。

    Returns:
        已解析且互不相同的报告和比较结果路径。

    Raises:
        FileNotFoundError: 输出父目录、证据目录或索引目录不存在。
        ValueError: 输出越界、进入索引目录或两个路径相同。
    """
    resolved_evidence_root = evidence_root.resolve(strict=True)
    resolved_index_root = index_root.resolve(strict=True)
    outputs: list[Path] = []
    for raw_path in (report_output, comparison_output):
        expanded = raw_path.expanduser()
        parent = expanded.parent.resolve(strict=True)
        path = parent / expanded.name
        if not path.is_relative_to(resolved_evidence_root):
            raise ValueError(
                "reproduction outputs must be inside the reproduction seal directory"
            )
        if path.is_relative_to(resolved_index_root):
            raise ValueError("reproduction outputs must be outside the index directory")
        if os.path.lexists(path):
            raise ValueError(f"reproduction output already exists: {path}")
        outputs.append(path)
    if outputs[0] == outputs[1]:
        raise ValueError("report and comparison outputs must be different paths")
    return outputs[0], outputs[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m abaqus_sim_agent.retrieval.reproduction",
        description="Audit or run a vector-bound identical held-out reproduction.",
    )
    parser.add_argument("command", choices=("audit", "run"))
    parser.add_argument("--index-dir", type=Path, default=Path("var/rag"))
    parser.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
    parser.add_argument("--seal", required=True, type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--report-output", type=Path)
    parser.add_argument("--comparison-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        index = AbaqusDocumentIndex(
            args.index_dir,
            collection_name=args.collection,
        )
        audited = audit_reproduction(
            index,
            args.seal,
            repository_root=args.repository_root,
        )
        if args.command == "audit":
            print(audited.audit.model_dump_json(indent=2))
            return 0
        if args.report_output is None or args.comparison_output is None:
            raise ValueError("run requires --report-output and --comparison-output")
        report_output, comparison_output = _resolve_output_paths(
            args.report_output,
            args.comparison_output,
            evidence_root=audited.benchmark_root,
            index_root=index.persist_directory,
        )
        report = run_reproduction(
            index,
            audited,
            warmup=not args.no_warmup,
        )
        _write_text_new_atomic(report_output, report.model_dump_json(indent=2))
        report_sha256 = _sha256_file(report_output)
        comparison = compare_reproduction(
            audited.reference_report,
            report.evaluation,
            reference_report_sha256=audited.seal.reference_report.sha256,
            reproduction_report_sha256=report_sha256,
        )
        _write_text_new_atomic(
            comparison_output,
            comparison.model_dump_json(indent=2),
        )
        print(comparison.model_dump_json(indent=2))
        return 0 if comparison.identical else 1
    except (BM25IndexError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
