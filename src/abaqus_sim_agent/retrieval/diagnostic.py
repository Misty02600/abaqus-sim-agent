from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import random
import re
import tempfile
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import lxml.etree as etree
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .bm25 import BM25IndexError
from .comparison import (
    ComparisonProvenance,
    PairedMetricDelta,
)
from .index import (
    DEFAULT_COLLECTION_NAME,
    AbaqusDocumentIndex,
    RetrievalHit,
    reciprocal_rank_fusion,
)
from .reproduction import audit_reproduction

DiagnosticOrigin = Literal["observed_solver_log", "official_document_condition"]
DiagnosticVariant = Literal[
    "verbatim_excerpt",
    "normalized",
    "condition_fragment",
    "contextualized",
]
DiagnosticQueryDerivation = Literal[
    "verbatim_excerpt",
    "deterministic_normalization",
    "reviewed_condition_paraphrase",
    "reviewed_contextualization",
]
DiagnosticTriggerSource = Literal[
    "solver_failure",
    "solver_warning",
    "official_diagnostic",
]
DiagnosticRuntimeStage = Literal[
    "input_processor",
    "standard_solver",
    "explicit_solver",
    "contact",
    "model_definition",
    "output",
]
SourceBindingKind = Literal["run_artifact", "official_html"]
RetrieverName = Literal["bm25", "dense", "hybrid"]

_RETRIEVERS: tuple[RetrieverName, ...] = ("bm25", "dense", "hybrid")
_TOP_K = 5
_PRODUCT_CONTEXT_DEPTH = 4
_FETCH_K = 20
_RRF_K = 60
_BOOTSTRAP_REPLICATES = 20_000
_BOOTSTRAP_SEED = 20_260_904
_WARMUP_QUERY = "Abaqus documentation retrieval warmup; no scored diagnostic case"
_LINE_LOCATOR = re.compile(r"^lines? (?P<start>[1-9]\d*)(?:-(?P<end>[1-9]\d*))?$")
_DIAGNOSTIC_PREFIX = re.compile(r"^\*{2,}\s*(?:ERROR|WARNING)\s*:\s*", re.I)
_FILE_LINE = re.compile(r'\bfile\s+"[^"]+"\s*,\s*line\s+\d+\s*:\s*', re.I)
_VOLATILE_NUMBER = re.compile(
    r"(?<![A-Za-z_])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?"
    r"(?![A-Za-z_])"
)
_OBSERVED_QUERY_TOKEN_LIMIT = 20
_CASE_TARGET_HASH_ALGORITHM = "ordered-canonical-json-sha256-v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiagnosticSourceBinding(_StrictModel):
    kind: SourceBindingKind
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    locator: str = Field(min_length=1)
    encoding: str | None = Field(default=None, min_length=1)
    legacy_inferred: bool


class DiagnosticGoldPage(_StrictModel):
    document_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    section_ids: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sections(self) -> DiagnosticGoldPage:
        if len(set(self.section_ids)) != len(self.section_ids):
            raise ValueError("gold page section IDs must be unique")
        return self


class DiagnosticSectionTarget(_StrictModel):
    document_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    section_title: str = Field(min_length=1)


class DiagnosticCaseTargets(_StrictModel):
    case_id: str = Field(min_length=1)
    targets: tuple[DiagnosticSectionTarget, ...] = Field(min_length=1)


class DiagnosticCase(_StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    need_group_id: str = Field(min_length=1)
    origin: DiagnosticOrigin
    variant: DiagnosticVariant
    query_derivation: DiagnosticQueryDerivation
    trigger_source: DiagnosticTriggerSource
    runtime_stage: DiagnosticRuntimeStage
    query: str = Field(min_length=1)
    source_binding: DiagnosticSourceBinding
    gold_pages: tuple[DiagnosticGoldPage, ...] = Field(min_length=1)
    notes: str = ""

    @model_validator(mode="after")
    def validate_provenance(self) -> DiagnosticCase:
        document_ids = [page.document_id for page in self.gold_pages]
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("gold page document IDs must be unique within a case")
        if self.origin == "observed_solver_log":
            if self.source_binding.kind != "run_artifact":
                raise ValueError("observed solver logs must bind a run artifact")
            if self.trigger_source not in {"solver_failure", "solver_warning"}:
                raise ValueError(
                    "observed solver logs require a solver failure or warning trigger"
                )
            if self.source_binding.encoding is None:
                raise ValueError("observed solver logs require an explicit encoding")
            if _LINE_LOCATOR.fullmatch(self.source_binding.locator) is None:
                raise ValueError("observed solver logs require a line-range locator")
            expected_derivation = {
                "verbatim_excerpt": "verbatim_excerpt",
                "normalized": "deterministic_normalization",
                "contextualized": "reviewed_contextualization",
            }.get(self.variant)
        else:
            if self.source_binding.kind != "official_html":
                raise ValueError("official document conditions must bind official HTML")
            if self.trigger_source != "official_diagnostic":
                raise ValueError(
                    "official document conditions require an official diagnostic trigger"
                )
            if not self.source_binding.locator.startswith("#"):
                raise ValueError("official HTML sources require a section ID locator")
            expected_derivation = {
                "condition_fragment": "reviewed_condition_paraphrase",
                "contextualized": "reviewed_contextualization",
            }.get(self.variant)
        if self.query_derivation != expected_derivation:
            raise ValueError("query derivation does not match the diagnostic variant")
        return self


class DiagnosticReviewManifest(_StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["reviewed"]
    review_method: Literal[
        "source_derivation_and_direct_html_without_retrieval_rankings"
    ]
    reviewer_id: str = Field(min_length=1)
    case_count: int = Field(gt=0)
    need_group_count: int = Field(gt=0)
    case_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_at: datetime
    limitations: tuple[str, ...] = ()


class DiagnosticAuditReport(_StrictModel):
    dataset_id: str
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_seal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_id: str = Field(min_length=1)
    implementation: DiagnosticImplementationBinding
    case_count: int = Field(gt=0)
    need_group_count: int = Field(gt=0)
    origin_counts: dict[str, int]
    runtime_stage_counts: dict[str, int]
    variant_counts: dict[str, int]
    source_bindings_verified: int = Field(gt=0)
    unique_source_files_verified: int = Field(gt=0)
    gold_page_bindings_verified: int = Field(gt=0)
    unique_gold_html_files_verified: int = Field(gt=0)
    semantic_gold_documents_verified: int = Field(gt=0)
    semantic_section_targets_verified: int = Field(gt=0)
    legacy_inferred_case_count: int = Field(ge=0)


@dataclass(frozen=True)
class AuditedDiagnosticDataset:
    source: Path
    cases: tuple[DiagnosticCase, ...]
    case_targets: tuple[DiagnosticCaseTargets, ...]
    audit: DiagnosticAuditReport
    review_manifest: DiagnosticReviewManifest
    review_path: Path
    environment_seal_path: Path
    repository_root: Path
    docs_root: Path
    run_root: Path


class DiagnosticImplementationBinding(_StrictModel):
    diagnostic_py: str = Field(pattern=r"^[0-9a-f]{64}$")
    comparison_py: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_py: str = Field(pattern=r"^[0-9a-f]{64}$")
    bm25_py: str = Field(pattern=r"^[0-9a-f]{64}$")


class DiagnosticArtifactBinding(_StrictModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_relative_path(self) -> DiagnosticArtifactBinding:
        path = Path(self.path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("diagnostic seal artifact paths must be root-relative")
        return self


class DiagnosticDependencyVersions(_StrictModel):
    lxml: str = Field(min_length=1)
    pydantic: str = Field(min_length=1)


class DiagnosticEvaluationContract(_StrictModel):
    top_k: Literal[5] = _TOP_K
    product_context_depth: Literal[4] = _PRODUCT_CONTEXT_DEPTH
    fetch_k: Literal[20] = _FETCH_K
    rrf_k: Literal[60] = _RRF_K
    bootstrap_method: Literal["need_group_resampling_percentile"] = (
        "need_group_resampling_percentile"
    )
    bootstrap_replicates: Literal[20_000] = _BOOTSTRAP_REPLICATES
    bootstrap_seed: Literal[20_260_904] = _BOOTSTRAP_SEED
    bootstrap_confidence_level_percent: Literal[95] = 95
    warmup_enabled: bool
    warmup_query: str | None

    @model_validator(mode="after")
    def validate_warmup(self) -> DiagnosticEvaluationContract:
        expected = _WARMUP_QUERY if self.warmup_enabled else None
        if self.warmup_query != expected:
            raise ValueError("diagnostic warmup query does not match its enabled state")
        return self


class DiagnosticSeal(_StrictModel):
    schema_version: Literal["1"] = "1"
    protocol_version: Literal["diagnostic-retrieval-reproduction-v1"] = (
        "diagnostic-retrieval-reproduction-v1"
    )
    status: Literal["sealed"] = "sealed"
    purpose: Literal["identical_replay"] = "identical_replay"
    dataset_id: str = Field(min_length=1)
    dataset: DiagnosticArtifactBinding
    review: DiagnosticArtifactBinding
    base_environment_seal: DiagnosticArtifactBinding
    reference_report: DiagnosticArtifactBinding
    reference_attempts: DiagnosticArtifactBinding
    reference_implementation: DiagnosticImplementationBinding
    implementation: DiagnosticImplementationBinding
    dependencies: DiagnosticDependencyVersions
    case_count: int = Field(gt=0)
    need_group_count: int = Field(gt=0)
    case_target_count: int = Field(gt=0)
    case_targets_hash_algorithm: Literal["ordered-canonical-json-sha256-v1"] = (
        _CASE_TARGET_HASH_ALGORITHM
    )
    ordered_case_targets_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_contract: DiagnosticEvaluationContract
    sealed_at: datetime


@dataclass(frozen=True)
class AuditedDiagnosticSeal:
    source: Path
    seal: DiagnosticSeal
    seal_sha256: str


class DiagnosticDepthMetrics(_StrictModel):
    section_hit_rate: float = Field(ge=0, le=1)
    section_mrr: float = Field(ge=0, le=1)
    page_hit_rate: float = Field(ge=0, le=1)
    page_mrr: float = Field(ge=0, le=1)


class DiagnosticDepthPair(_StrictModel):
    at_4: DiagnosticDepthMetrics
    at_5: DiagnosticDepthMetrics


class DiagnosticRetrieverMetrics(_StrictModel):
    bm25: DiagnosticDepthPair
    dense: DiagnosticDepthPair
    hybrid: DiagnosticDepthPair


class DiagnosticSliceSummary(_StrictModel):
    case_count: int = Field(gt=0)
    need_group_count: int = Field(gt=0)
    variant_level: DiagnosticRetrieverMetrics
    need_group_macro: DiagnosticRetrieverMetrics


class DiagnosticSlices(_StrictModel):
    origin: dict[str, DiagnosticSliceSummary]
    runtime_stage: dict[str, DiagnosticSliceSummary]
    form: dict[str, DiagnosticSliceSummary]


class DiagnosticPairedDepth(_StrictModel):
    groups: int = Field(gt=0)
    section_hit: PairedMetricDelta
    section_reciprocal_rank: PairedMetricDelta
    section_hit_interval: DiagnosticBootstrapInterval
    section_reciprocal_rank_interval: DiagnosticBootstrapInterval


class DiagnosticBootstrapInterval(_StrictModel):
    method: Literal["need_group_resampling_percentile"] = (
        "need_group_resampling_percentile"
    )
    replicates: Literal[20_000] = 20_000
    seed: int
    confidence_level_percent: Literal[95] = 95
    mean: float = Field(ge=-1, le=1)
    lower: float = Field(ge=-1, le=1)
    upper: float = Field(ge=-1, le=1)


class DiagnosticPairedComparison(_StrictModel):
    at_4: DiagnosticPairedDepth
    at_5: DiagnosticPairedDepth


class DiagnosticPairedComparisons(_StrictModel):
    bm25_vs_dense: DiagnosticPairedComparison
    hybrid_vs_dense: DiagnosticPairedComparison
    hybrid_vs_bm25: DiagnosticPairedComparison


class DiagnosticRankingResult(_StrictModel):
    retrieved_document_ids: tuple[str, ...]
    retrieved_section_ids: tuple[str | None, ...]
    retrieved_section_titles: tuple[str | None, ...]
    section_hit_at_4: bool
    section_reciprocal_rank_at_4: float = Field(ge=0, le=1)
    section_hit_at_5: bool
    section_reciprocal_rank_at_5: float = Field(ge=0, le=1)
    page_hit_at_4: bool
    page_reciprocal_rank_at_4: float = Field(ge=0, le=1)
    page_hit_at_5: bool
    page_reciprocal_rank_at_5: float = Field(ge=0, le=1)
    elapsed_ms: float = Field(ge=0)


class DiagnosticCaseResult(_StrictModel):
    case_id: str
    need_group_id: str
    origin: DiagnosticOrigin
    variant: DiagnosticVariant
    trigger_source: DiagnosticTriggerSource
    runtime_stage: DiagnosticRuntimeStage
    query: str
    gold_document_ids: tuple[str, ...]
    gold_targets: tuple[DiagnosticSectionTarget, ...]
    bm25: DiagnosticRankingResult
    dense: DiagnosticRankingResult
    hybrid: DiagnosticRankingResult
    fusion_elapsed_ms: float = Field(ge=0)


class DiagnosticEvaluation(_StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str
    dataset_role: Literal["development_diagnostic_stress"] = (
        "development_diagnostic_stress"
    )
    evaluation_scope: Literal["raw_query_algorithm_comparison"] = (
        "raw_query_algorithm_comparison"
    )
    heldout: Literal[False] = False
    product_path_evaluated: Literal[False] = False
    production_traffic_representative: Literal[False] = False
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    diagnostic_seal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluated_at: datetime
    case_count: int = Field(gt=0)
    need_group_count: int = Field(gt=0)
    top_k: Literal[5] = 5
    product_context_depth: Literal[4] = 4
    fetch_k: Literal[20] = 20
    rrf_k: Literal[60] = 60
    bootstrap_replicates: Literal[20_000] = _BOOTSTRAP_REPLICATES
    bootstrap_seed: Literal[20_260_904] = _BOOTSTRAP_SEED
    warmup_performed: bool
    warmup_query: str | None
    audit: DiagnosticAuditReport
    provenance: ComparisonProvenance
    implementation: DiagnosticImplementationBinding
    variant_level: DiagnosticRetrieverMetrics
    need_group_macro: DiagnosticRetrieverMetrics
    slices: DiagnosticSlices
    paired_need_group_macro: DiagnosticPairedComparisons
    cases: tuple[DiagnosticCaseResult, ...]


def load_diagnostic_cases(path: str | Path) -> list[DiagnosticCase]:
    """读取严格 JSONL，并验证数据集和 need group 的结构合同。

    Args:
        path: 诊断检索 JSONL 文件。

    Returns:
        按文件顺序排列且已验证的 Case。

    Raises:
        ValueError: JSONL、唯一性或 need group 合同不成立。
    """
    source = Path(path)
    cases: list[DiagnosticCase] = []
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            cases.append(DiagnosticCase.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(
                f"invalid diagnostic retrieval case at {source}:{line_number}"
            ) from exc
    if not cases:
        raise ValueError(f"diagnostic retrieval dataset is empty: {source}")

    dataset_ids = {case.dataset_id for case in cases}
    if len(dataset_ids) != 1:
        raise ValueError("diagnostic cases must share one dataset_id")
    duplicate_ids = _duplicates(case.case_id for case in cases)
    if duplicate_ids:
        raise ValueError(f"diagnostic case IDs must be unique: {duplicate_ids!r}")
    duplicate_queries = _duplicates(_normalized_query(case.query) for case in cases)
    if duplicate_queries:
        raise ValueError(
            f"diagnostic normalized queries must be unique: {duplicate_queries!r}"
        )

    for group_id, group in _group_cases(cases).items():
        if len(group) not in {2, 3}:
            raise ValueError(
                f"diagnostic need group must contain 2 or 3 variants: {group_id}"
            )
        variants = [case.variant for case in group]
        if len(set(variants)) != len(variants):
            raise ValueError(
                f"diagnostic need group variants must be unique: {group_id}"
            )
        first = group[0]
        expected_variants = (
            {"verbatim_excerpt", "normalized", "contextualized"}
            if first.origin == "observed_solver_log"
            else {"condition_fragment", "contextualized"}
        )
        if set(variants) != expected_variants:
            raise ValueError(
                f"diagnostic need group has invalid variants for its origin: {group_id}"
            )
        expected_gold = _gold_contract(first.gold_pages)
        expected_source = first.source_binding.model_dump_json()
        for case in group[1:]:
            if case.origin != first.origin:
                raise ValueError(
                    f"diagnostic need group must share one origin: {group_id}"
                )
            if case.runtime_stage != first.runtime_stage:
                raise ValueError(
                    f"diagnostic need group must share one runtime stage: {group_id}"
                )
            if case.trigger_source != first.trigger_source:
                raise ValueError(
                    f"diagnostic need group must share one trigger source: {group_id}"
                )
            if case.source_binding.model_dump_json() != expected_source:
                raise ValueError(
                    f"diagnostic need group must share one source binding: {group_id}"
                )
            if _gold_contract(case.gold_pages) != expected_gold:
                raise ValueError(
                    f"diagnostic need group must share one gold contract: {group_id}"
                )
    return cases


def audit_diagnostic_dataset(
    index: AbaqusDocumentIndex,
    dataset_path: str | Path,
    *,
    review_path: str | Path,
    environment_seal_path: str | Path,
    repository_root: str | Path,
    docs_root: str | Path,
    run_root: str | Path,
) -> AuditedDiagnosticDataset:
    """验证诊断数据的原始来源、官方 HTML gold 和索引父文档。

    Args:
        index: 需要参与评测的 Dense/BM25 组合索引。
        dataset_path: 尚未执行排名的诊断 JSONL 文件。
        review_path: 在排名前完成并绑定当前数据集hash的复核记录。
        environment_seal_path: 已预绑定HNSW、query encoder与运行依赖的环境seal。
        repository_root: 用于复核环境seal和评测实现文件的仓库根目录。
        docs_root: 官方 HTML 快照根目录。
        run_root: 已观察运行产物的共同根目录。

    Returns:
        只包含已通过来源、gold 和语料检查的审计后数据集。

    Raises:
        ValueError: 数据结构、路径归属、hash、HTML 或语料父文档不成立。
    """
    source = Path(dataset_path).resolve(strict=True)
    cases = load_diagnostic_cases(source)
    review_source = Path(review_path).resolve(strict=True)
    environment_source = Path(environment_seal_path).resolve(strict=True)
    repository = Path(repository_root).resolve(strict=True)
    review = DiagnosticReviewManifest.model_validate_json(
        review_source.read_text(encoding="utf-8")
    )
    _verify_review_manifest(review, cases, source)
    environment_sha256 = _verify_environment_seal(
        index,
        environment_source,
        repository,
    )
    docs = Path(docs_root).resolve(strict=True)
    runs = Path(run_root).resolve(strict=True)
    semantic_sections = _semantic_section_metadata(index)
    semantic_ids = set(semantic_sections)

    verified_sources: set[tuple[SourceBindingKind, str, str]] = set()
    verified_gold_files: set[tuple[str, str]] = set()
    verified_gold_documents: set[str] = set()
    case_targets: list[DiagnosticCaseTargets] = []
    gold_binding_count = 0
    for case in cases:
        binding_root = runs if case.source_binding.kind == "run_artifact" else docs
        bound_source = _bound_relative_file(binding_root, case.source_binding.path)
        _require_hash(
            bound_source,
            case.source_binding.sha256,
            f"diagnostic source for {case.case_id}",
        )
        if case.source_binding.encoding is not None:
            try:
                bound_source.read_text(encoding=case.source_binding.encoding)
            except (LookupError, UnicodeError) as exc:
                raise ValueError(
                    f"cannot decode diagnostic source for {case.case_id} "
                    f"as {case.source_binding.encoding}"
                ) from exc
        _verify_source_locator(case, bound_source)
        verified_sources.add(
            (
                case.source_binding.kind,
                case.source_binding.path,
                case.source_binding.sha256,
            )
        )

        targets: set[tuple[str, str, str]] = set()
        for page in case.gold_pages:
            gold_binding_count += 1
            if page.document_id not in semantic_ids:
                raise ValueError(
                    f"diagnostic gold has no semantic parent: {page.document_id}"
                )
            gold_source = _bound_relative_file(docs, page.source_path)
            _require_hash(
                gold_source,
                page.source_sha256,
                f"diagnostic gold HTML {page.document_id}",
            )
            tree = _verify_html_page(gold_source, page)
            targets.update(
                _derive_section_targets(
                    tree,
                    page,
                    semantic_sections[page.document_id],
                )
            )
            verified_gold_files.add((page.source_path, page.source_sha256))
            verified_gold_documents.add(page.document_id)
        if not targets:
            raise ValueError(
                f"diagnostic case has no index-representable section target: "
                f"{case.case_id}"
            )
        case_targets.append(
            DiagnosticCaseTargets(
                case_id=case.case_id,
                targets=tuple(
                    DiagnosticSectionTarget(
                        document_id=document_id,
                        section_id=section_id,
                        section_title=section_title,
                    )
                    for document_id, section_id, section_title in sorted(targets)
                ),
            )
        )

    audit = DiagnosticAuditReport(
        dataset_id=cases[0].dataset_id,
        dataset_sha256=_sha256_file(source),
        review_manifest_sha256=_sha256_file(review_source),
        environment_seal_sha256=environment_sha256,
        reviewer_id=review.reviewer_id,
        implementation=_implementation_binding(),
        case_count=len(cases),
        need_group_count=len(_group_cases(cases)),
        origin_counts=dict(Counter(case.origin for case in cases)),
        runtime_stage_counts=dict(Counter(case.runtime_stage for case in cases)),
        variant_counts=dict(Counter(case.variant for case in cases)),
        source_bindings_verified=len(cases),
        unique_source_files_verified=len(verified_sources),
        gold_page_bindings_verified=gold_binding_count,
        unique_gold_html_files_verified=len(verified_gold_files),
        semantic_gold_documents_verified=len(verified_gold_documents),
        semantic_section_targets_verified=len(
            {
                (
                    target.document_id,
                    target.section_id,
                    _normalized_section_title(target.section_title),
                )
                for binding in case_targets
                for target in binding.targets
            }
        ),
        legacy_inferred_case_count=sum(
            case.source_binding.legacy_inferred for case in cases
        ),
    )
    return AuditedDiagnosticDataset(
        source=source,
        cases=tuple(cases),
        case_targets=tuple(case_targets),
        audit=audit,
        review_manifest=review,
        review_path=review_source,
        environment_seal_path=environment_source,
        repository_root=repository,
        docs_root=docs,
        run_root=runs,
    )


def create_diagnostic_seal(
    index: AbaqusDocumentIndex,
    dataset: AuditedDiagnosticDataset,
    output_path: str | Path,
    *,
    reference_report_path: str | Path,
    reference_attempts_path: str | Path,
    warmup: bool = True,
) -> AuditedDiagnosticSeal:
    """在重新审计数据后原子创建不可覆盖的诊断评测 seal。

    Args:
        index: 已绑定 Dense、BM25 与基础环境 seal 的索引。
        dataset: 无排名审计已经通过的数据集。
        output_path: 新 seal 的输出路径，必须位于检索索引之外。
        reference_report_path: 需要执行 identical replay 的首轮评测报告。
        reference_attempts_path: 记录首轮报告及其 attestation gap 的只读账本。
        warmup: 后续 reproduction 是否执行固定的非计分 warmup。

    Returns:
        写入后再次通过完整合同检查的 seal。

    Raises:
        ValueError: 审计证据、路径边界或 seal 合同不成立。
    """
    _verify_audited_dataset(index, dataset)
    target = _resolve_output_path(Path(output_path), index.persist_directory)
    seal = _build_diagnostic_seal(
        dataset,
        seal_directory=target.parent,
        reference_report_path=Path(reference_report_path),
        reference_attempts_path=Path(reference_attempts_path),
        warmup=warmup,
    )
    _write_text_new_atomic(target, seal.model_dump_json(indent=2) + "\n")
    audited = audit_diagnostic_seal(dataset, target, warmup=warmup)
    _verify_audited_dataset(index, dataset)
    return audited


def audit_diagnostic_seal(
    dataset: AuditedDiagnosticDataset,
    seal_path: str | Path,
    *,
    warmup: bool,
) -> AuditedDiagnosticSeal:
    """核对 seal 与当前审计数据、代码、依赖和冻结评测合同。"""
    source = Path(seal_path).resolve(strict=True)
    seal = DiagnosticSeal.model_validate_json(source.read_text(encoding="utf-8"))
    root = source.parent
    bound_paths = {
        "dataset": _bound_relative_file(root, seal.dataset.path),
        "review": _bound_relative_file(root, seal.review.path),
        "base environment seal": _bound_relative_file(
            root,
            seal.base_environment_seal.path,
        ),
    }
    expected_paths = {
        "dataset": dataset.source,
        "review": dataset.review_path,
        "base environment seal": dataset.environment_seal_path,
    }
    bindings = {
        "dataset": seal.dataset,
        "review": seal.review,
        "base environment seal": seal.base_environment_seal,
    }
    for label, path in bound_paths.items():
        if path != expected_paths[label]:
            raise ValueError(f"diagnostic seal binds a different {label}")
        _require_hash(path, bindings[label].sha256, f"diagnostic seal {label}")

    reference_report = _bound_relative_file(root, seal.reference_report.path)
    reference_attempts = _bound_relative_file(root, seal.reference_attempts.path)
    _require_hash(
        reference_report,
        seal.reference_report.sha256,
        "diagnostic seal reference report",
    )
    _require_hash(
        reference_attempts,
        seal.reference_attempts.sha256,
        "diagnostic seal reference attempts",
    )

    expected_implementation = _implementation_binding()
    expected_dependencies = _installed_dependency_versions()
    expected_contract = _evaluation_contract(warmup)
    expected_target_hash = _ordered_case_targets_sha256(dataset.case_targets)
    reference_implementation = _verify_reference_evidence(
        reference_report,
        reference_attempts,
        dataset,
        warmup=warmup,
    )
    if seal.dataset_id != dataset.audit.dataset_id:
        raise ValueError("diagnostic seal dataset ID does not match")
    if seal.dataset.sha256 != dataset.audit.dataset_sha256:
        raise ValueError("diagnostic seal dataset hash does not match the audit")
    if seal.review.sha256 != dataset.audit.review_manifest_sha256:
        raise ValueError("diagnostic seal review hash does not match the audit")
    if seal.base_environment_seal.sha256 != dataset.audit.environment_seal_sha256:
        raise ValueError("diagnostic seal base environment hash does not match")
    if seal.implementation != expected_implementation:
        raise ValueError("diagnostic seal implementation hashes do not match")
    if seal.implementation != dataset.audit.implementation:
        raise ValueError("diagnostic seal implementation differs from dataset audit")
    if seal.dependencies != expected_dependencies:
        raise ValueError("diagnostic seal dependency versions do not match")
    if seal.reference_implementation != reference_implementation:
        raise ValueError("diagnostic seal reference implementation does not match")
    if seal.case_count != len(dataset.cases):
        raise ValueError("diagnostic seal case count does not match")
    if seal.need_group_count != len(_group_cases(dataset.cases)):
        raise ValueError("diagnostic seal need-group count does not match")
    if seal.case_target_count != sum(
        len(binding.targets) for binding in dataset.case_targets
    ):
        raise ValueError("diagnostic seal case-target count does not match")
    if seal.ordered_case_targets_sha256 != expected_target_hash:
        raise ValueError("diagnostic seal ordered case-target hash does not match")
    if seal.evaluation_contract != expected_contract:
        raise ValueError("diagnostic seal evaluation contract does not match")
    return AuditedDiagnosticSeal(
        source=source,
        seal=seal,
        seal_sha256=_sha256_file(source),
    )


def _build_diagnostic_seal(
    dataset: AuditedDiagnosticDataset,
    *,
    seal_directory: Path,
    reference_report_path: Path,
    reference_attempts_path: Path,
    warmup: bool,
) -> DiagnosticSeal:
    if dataset.audit.implementation != _implementation_binding():
        raise ValueError("diagnostic implementation changed after dataset audit")
    reference_report = reference_report_path.resolve(strict=True)
    reference_attempts = reference_attempts_path.resolve(strict=True)
    reference_implementation = _verify_reference_evidence(
        reference_report,
        reference_attempts,
        dataset,
        warmup=warmup,
    )
    return DiagnosticSeal(
        dataset_id=dataset.audit.dataset_id,
        dataset=_artifact_binding(seal_directory, dataset.source),
        review=_artifact_binding(seal_directory, dataset.review_path),
        base_environment_seal=_artifact_binding(
            seal_directory,
            dataset.environment_seal_path,
        ),
        reference_report=_artifact_binding(seal_directory, reference_report),
        reference_attempts=_artifact_binding(seal_directory, reference_attempts),
        reference_implementation=reference_implementation,
        implementation=_implementation_binding(),
        dependencies=_installed_dependency_versions(),
        case_count=len(dataset.cases),
        need_group_count=len(_group_cases(dataset.cases)),
        case_target_count=sum(len(binding.targets) for binding in dataset.case_targets),
        ordered_case_targets_sha256=_ordered_case_targets_sha256(dataset.case_targets),
        evaluation_contract=_evaluation_contract(warmup),
        sealed_at=datetime.now(UTC),
    )


def evaluate_diagnostic_dataset(
    index: AbaqusDocumentIndex,
    dataset: AuditedDiagnosticDataset,
    *,
    diagnostic_seal_path: str | Path,
    warmup: bool = True,
) -> DiagnosticEvaluation:
    """以一次固定的三臂调用计算 variant 与 need-group 指标。

    Args:
        index: 已在审计中核对 gold 父文档的组合索引。
        dataset: 排名前完成来源审计的数据集。
        warmup: 是否用一条不计分且不属于数据集的查询预热两路检索。

    Returns:
        同一组 Top-5 排名导出的 Top-4/Top-5 分片和配对报告。

    Note:
        Top-4 是产品实际提供给 Agent 的上下文深度；Top-5 仅用于与既有
        检索 benchmark 对齐。每个计分 query 在 BM25 和 Dense 各执行一次。
    """
    _verify_audited_dataset(index, dataset)
    sealed = audit_diagnostic_seal(
        dataset,
        diagnostic_seal_path,
        warmup=warmup,
    )
    index.verify_bm25_corpus()
    if warmup:
        index.search_bm25(_WARMUP_QUERY, top_k=1)
        index.search_concepts(_WARMUP_QUERY, top_k=1)
    targets_by_case = {
        binding.case_id: binding.targets for binding in dataset.case_targets
    }
    if set(targets_by_case) != {case.case_id for case in dataset.cases}:
        raise ValueError("diagnostic audited targets do not match the case set")
    results: list[DiagnosticCaseResult] = []
    for case in dataset.cases:
        bm25_started = time.perf_counter()
        bm25_hits = index.search_bm25(case.query, top_k=_FETCH_K)
        bm25_elapsed_ms = (time.perf_counter() - bm25_started) * 1000

        dense_started = time.perf_counter()
        dense_hits = [
            RetrievalHit(
                document=document,
                match_type="semantic",
                distance=distance,
            )
            for document, distance in index.search_concepts(
                case.query,
                top_k=_FETCH_K,
            )
        ]
        dense_elapsed_ms = (time.perf_counter() - dense_started) * 1000

        fusion_started = time.perf_counter()
        hybrid_hits = reciprocal_rank_fusion(
            dense_hits,
            bm25_hits,
            top_k=_TOP_K,
            rrf_k=_RRF_K,
        )
        fusion_elapsed_ms = (time.perf_counter() - fusion_started) * 1000
        results.append(
            _case_result(
                case,
                targets_by_case[case.case_id],
                bm25_hits[:_TOP_K],
                dense_hits[:_TOP_K],
                hybrid_hits,
                bm25_elapsed_ms=bm25_elapsed_ms,
                dense_elapsed_ms=dense_elapsed_ms,
                fusion_elapsed_ms=fusion_elapsed_ms,
            )
        )
    index.verify_bm25_corpus()
    _verify_audited_dataset(index, dataset)
    sealed_after = audit_diagnostic_seal(
        dataset,
        diagnostic_seal_path,
        warmup=warmup,
    )
    if (
        sealed_after.seal != sealed.seal
        or sealed_after.seal_sha256 != sealed.seal_sha256
    ):
        raise ValueError("diagnostic seal changed while rankings were evaluated")
    dense_manifest = index.read_manifest()
    bm25_manifest = index.read_bm25_manifest()
    provenance = ComparisonProvenance(
        benchmark_sha256=dataset.audit.dataset_sha256,
        dense_manifest_sha256=_sha256_file(index.manifest_path),
        bm25_manifest_sha256=_sha256_file(index.bm25_manifest_path),
        bm25_database_sha256=bm25_manifest.database_sha256,
        bm25_semantic_corpus_sha256=bm25_manifest.semantic_corpus_sha256,
        release=dense_manifest.release,
        collection_name=dense_manifest.collection_name,
        dense_embedding=dense_manifest.embedding,
        bm25_algorithm=bm25_manifest.algorithm,
        bm25_tokenizer=bm25_manifest.tokenizer,
        bm25_k1=bm25_manifest.k1,
        bm25_b=bm25_manifest.b,
    )
    result_tuple = tuple(results)
    return DiagnosticEvaluation(
        dataset_id=dataset.audit.dataset_id,
        dataset_sha256=dataset.audit.dataset_sha256,
        diagnostic_seal_sha256=sealed.seal_sha256,
        evaluated_at=datetime.now(UTC),
        case_count=len(result_tuple),
        need_group_count=len(_group_results(result_tuple)),
        audit=dataset.audit,
        provenance=provenance,
        implementation=_implementation_binding(),
        warmup_performed=warmup,
        warmup_query=_WARMUP_QUERY if warmup else None,
        variant_level=_retriever_metrics(result_tuple, group_macro=False),
        need_group_macro=_retriever_metrics(result_tuple, group_macro=True),
        slices=DiagnosticSlices(
            origin=_slice_summaries(result_tuple, "origin"),
            runtime_stage=_slice_summaries(result_tuple, "runtime_stage"),
            form=_slice_summaries(result_tuple, "variant"),
        ),
        paired_need_group_macro=DiagnosticPairedComparisons(
            bm25_vs_dense=_paired_comparison(result_tuple, "bm25", "dense"),
            hybrid_vs_dense=_paired_comparison(result_tuple, "hybrid", "dense"),
            hybrid_vs_bm25=_paired_comparison(result_tuple, "hybrid", "bm25"),
        ),
        cases=result_tuple,
    )


def _verify_audited_dataset(
    index: AbaqusDocumentIndex,
    dataset: AuditedDiagnosticDataset,
) -> None:
    current = audit_diagnostic_dataset(
        index,
        dataset.source,
        review_path=dataset.review_path,
        environment_seal_path=dataset.environment_seal_path,
        repository_root=dataset.repository_root,
        docs_root=dataset.docs_root,
        run_root=dataset.run_root,
    )
    if (
        current.cases != dataset.cases
        or current.case_targets != dataset.case_targets
        or current.audit != dataset.audit
    ):
        raise ValueError("diagnostic dataset or its bound evidence changed")


def _verify_environment_seal(
    index: AbaqusDocumentIndex,
    seal_path: Path,
    repository_root: Path,
) -> str:
    audited = audit_reproduction(
        index,
        seal_path,
        repository_root=repository_root,
    )
    return audited.seal_sha256


def _verify_review_manifest(
    review: DiagnosticReviewManifest,
    cases: Sequence[DiagnosticCase],
    dataset_path: Path,
) -> None:
    if review.dataset_id != cases[0].dataset_id:
        raise ValueError("diagnostic review dataset ID does not match")
    if review.dataset_sha256 != _sha256_file(dataset_path):
        raise ValueError("diagnostic review does not bind the current dataset")
    if review.case_count != len(cases):
        raise ValueError("diagnostic review case count does not match")
    if review.need_group_count != len(_group_cases(cases)):
        raise ValueError("diagnostic review need-group count does not match")
    if review.case_ids_sha256 != _hash_strings(case.case_id for case in cases):
        raise ValueError("diagnostic review case IDs do not match")


def _implementation_binding() -> DiagnosticImplementationBinding:
    root = Path(__file__).parent
    return DiagnosticImplementationBinding(
        diagnostic_py=_sha256_file(Path(__file__)),
        comparison_py=_sha256_file(root / "comparison.py"),
        index_py=_sha256_file(root / "index.py"),
        bm25_py=_sha256_file(root / "bm25.py"),
    )


def _installed_dependency_versions() -> DiagnosticDependencyVersions:
    return DiagnosticDependencyVersions(
        lxml=importlib.metadata.version("lxml"),
        pydantic=importlib.metadata.version("pydantic"),
    )


def _evaluation_contract(warmup: bool) -> DiagnosticEvaluationContract:
    return DiagnosticEvaluationContract(
        warmup_enabled=warmup,
        warmup_query=_WARMUP_QUERY if warmup else None,
    )


def _artifact_binding(root: Path, source: Path) -> DiagnosticArtifactBinding:
    resolved_root = root.resolve(strict=True)
    resolved_source = source.resolve(strict=True)
    if not resolved_source.is_relative_to(resolved_root):
        raise ValueError(
            f"diagnostic seal artifact is outside its evidence root: {source}"
        )
    return DiagnosticArtifactBinding(
        path=resolved_source.relative_to(resolved_root).as_posix(),
        sha256=_sha256_file(resolved_source),
    )


def _ordered_case_targets_sha256(
    bindings: Sequence[DiagnosticCaseTargets],
) -> str:
    case_ids = [binding.case_id for binding in bindings]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("diagnostic case-target bindings must have unique case IDs")
    canonical = json.dumps(
        [binding.model_dump(mode="json") for binding in bindings],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _verify_reference_evidence(
    report_path: Path,
    attempts_path: Path,
    dataset: AuditedDiagnosticDataset,
    *,
    warmup: bool,
) -> DiagnosticImplementationBinding:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("diagnostic reference report is not valid UTF-8 JSON") from exc
    if not isinstance(report, dict):
        raise ValueError("diagnostic reference report must be a JSON object")
    contract = _evaluation_contract(warmup)
    expected_fields: dict[str, object] = {
        "dataset_id": dataset.audit.dataset_id,
        "dataset_sha256": dataset.audit.dataset_sha256,
        "case_count": len(dataset.cases),
        "need_group_count": len(_group_cases(dataset.cases)),
        "top_k": contract.top_k,
        "product_context_depth": contract.product_context_depth,
        "fetch_k": contract.fetch_k,
        "rrf_k": contract.rrf_k,
        "bootstrap_replicates": contract.bootstrap_replicates,
        "bootstrap_seed": contract.bootstrap_seed,
        "warmup_performed": contract.warmup_enabled,
        "warmup_query": contract.warmup_query,
    }
    for field, expected in expected_fields.items():
        if report.get(field) != expected:
            raise ValueError(
                f"diagnostic reference report field does not match: {field}"
            )
    try:
        implementation = DiagnosticImplementationBinding.model_validate(
            report["implementation"]
        )
        report_cases = report["cases"]
        if not isinstance(report_cases, list):
            raise TypeError
        reference_targets = tuple(
            DiagnosticCaseTargets.model_validate(
                {
                    "case_id": item["case_id"],
                    "targets": item["gold_targets"],
                }
            )
            for item in report_cases
            if isinstance(item, dict)
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "diagnostic reference report lacks strict implementation or target data"
        ) from exc
    if len(reference_targets) != len(report_cases):
        raise ValueError("diagnostic reference report contains a non-object case")
    if _ordered_case_targets_sha256(reference_targets) != (
        _ordered_case_targets_sha256(dataset.case_targets)
    ):
        raise ValueError("diagnostic reference report case targets do not match")

    attempts: list[object] = []
    for line in attempts_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                attempts.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "diagnostic reference attempts JSONL is invalid"
                ) from exc
    report_sha256 = _sha256_file(report_path)
    completed = [
        item
        for item in attempts
        if isinstance(item, dict)
        and item.get("rankings_emitted") is True
        and item.get("report_sha256") == report_sha256
    ]
    if len(completed) != 1:
        raise ValueError(
            "diagnostic reference attempts must bind one completed reference report"
        )
    return implementation


def _case_result(
    case: DiagnosticCase,
    gold_targets: Sequence[DiagnosticSectionTarget],
    bm25_hits: Sequence[RetrievalHit],
    dense_hits: Sequence[RetrievalHit],
    hybrid_hits: Sequence[RetrievalHit],
    *,
    bm25_elapsed_ms: float,
    dense_elapsed_ms: float,
    fusion_elapsed_ms: float,
) -> DiagnosticCaseResult:
    return DiagnosticCaseResult(
        case_id=case.case_id,
        need_group_id=case.need_group_id,
        origin=case.origin,
        variant=case.variant,
        trigger_source=case.trigger_source,
        runtime_stage=case.runtime_stage,
        query=case.query,
        gold_document_ids=tuple(page.document_id for page in case.gold_pages),
        gold_targets=tuple(gold_targets),
        bm25=_score_ranking(
            bm25_hits,
            case.gold_pages,
            gold_targets,
            elapsed_ms=bm25_elapsed_ms,
        ),
        dense=_score_ranking(
            dense_hits,
            case.gold_pages,
            gold_targets,
            elapsed_ms=dense_elapsed_ms,
        ),
        hybrid=_score_ranking(
            hybrid_hits,
            case.gold_pages,
            gold_targets,
            elapsed_ms=bm25_elapsed_ms + dense_elapsed_ms + fusion_elapsed_ms,
        ),
        fusion_elapsed_ms=fusion_elapsed_ms,
    )


def _score_ranking(
    hits: Sequence[RetrievalHit],
    gold_pages: Sequence[DiagnosticGoldPage],
    gold_targets: Sequence[DiagnosticSectionTarget],
    *,
    elapsed_ms: float,
) -> DiagnosticRankingResult:
    selected = tuple(hits[:_TOP_K])
    document_ids = tuple(_document_id(hit) for hit in selected)
    section_ids = tuple(_section_id(hit) for hit in selected)
    section_titles = tuple(_section_title(hit) for hit in selected)
    page_gold = {page.document_id for page in gold_pages}
    page_hit_4, page_rank_4 = _hit_and_rank(
        document_ids[:_PRODUCT_CONTEXT_DEPTH],
        page_gold,
    )
    page_hit_5, page_rank_5 = _hit_and_rank(document_ids, page_gold)
    section_matches = tuple(
        _section_hit(document_id, section_id, section_title, gold_targets)
        for document_id, section_id, section_title in zip(
            document_ids,
            section_ids,
            section_titles,
            strict=True,
        )
    )
    section_hit_4, section_rank_4 = _boolean_hit_and_rank(
        section_matches[:_PRODUCT_CONTEXT_DEPTH]
    )
    section_hit_5, section_rank_5 = _boolean_hit_and_rank(section_matches)
    return DiagnosticRankingResult(
        retrieved_document_ids=document_ids,
        retrieved_section_ids=section_ids,
        retrieved_section_titles=section_titles,
        section_hit_at_4=section_hit_4,
        section_reciprocal_rank_at_4=section_rank_4,
        section_hit_at_5=section_hit_5,
        section_reciprocal_rank_at_5=section_rank_5,
        page_hit_at_4=page_hit_4,
        page_reciprocal_rank_at_4=page_rank_4,
        page_hit_at_5=page_hit_5,
        page_reciprocal_rank_at_5=page_rank_5,
        elapsed_ms=elapsed_ms,
    )


def _hit_and_rank(
    document_ids: Sequence[str],
    gold: set[str],
) -> tuple[bool, float]:
    rank = next(
        (
            position
            for position, document_id in enumerate(document_ids, start=1)
            if document_id in gold
        ),
        None,
    )
    return rank is not None, 0.0 if rank is None else 1 / rank


def _boolean_hit_and_rank(matches: Sequence[bool]) -> tuple[bool, float]:
    rank = next(
        (position for position, matched in enumerate(matches, start=1) if matched),
        None,
    )
    return rank is not None, 0.0 if rank is None else 1 / rank


def _document_id(hit: RetrievalHit) -> str:
    return str(hit.document.metadata.get("document_id") or hit.document.id or "")


def _section_id(hit: RetrievalHit) -> str | None:
    value = hit.document.metadata.get("section_id")
    return str(value) if isinstance(value, str) and value else None


def _section_title(hit: RetrievalHit) -> str | None:
    value = hit.document.metadata.get("section_title")
    return str(value) if isinstance(value, str) and value.strip() else None


def _section_hit(
    document_id: str,
    section_id: str | None,
    section_title: str | None,
    gold_targets: Sequence[DiagnosticSectionTarget],
) -> bool:
    if section_id is None or section_title is None:
        return False
    normalized_title = _normalized_section_title(section_title)
    return any(
        target.document_id == document_id
        and target.section_id == section_id
        and _normalized_section_title(target.section_title) == normalized_title
        for target in gold_targets
    )


def _retriever_metrics(
    results: Sequence[DiagnosticCaseResult],
    *,
    group_macro: bool,
) -> DiagnosticRetrieverMetrics:
    metrics = {
        retriever: DiagnosticDepthPair(
            at_4=_metric_summary(results, retriever, 4, group_macro=group_macro),
            at_5=_metric_summary(results, retriever, 5, group_macro=group_macro),
        )
        for retriever in _RETRIEVERS
    }
    return DiagnosticRetrieverMetrics(**metrics)


def _metric_summary(
    results: Sequence[DiagnosticCaseResult],
    retriever: RetrieverName,
    depth: Literal[4, 5],
    *,
    group_macro: bool,
) -> DiagnosticDepthMetrics:
    if not group_macro:
        rankings = [_ranking(result, retriever) for result in results]
        return DiagnosticDepthMetrics(
            section_hit_rate=_mean(
                _section_hit_at(ranking, depth) for ranking in rankings
            ),
            section_mrr=_mean(_section_rank_at(ranking, depth) for ranking in rankings),
            page_hit_rate=_mean(_page_hit_at(ranking, depth) for ranking in rankings),
            page_mrr=_mean(_page_rank_at(ranking, depth) for ranking in rankings),
        )
    groups = _group_results(results)
    return DiagnosticDepthMetrics(
        section_hit_rate=_mean(
            _mean(_section_hit_at(_ranking(item, retriever), depth) for item in group)
            for group in groups.values()
        ),
        section_mrr=_mean(
            _mean(_section_rank_at(_ranking(item, retriever), depth) for item in group)
            for group in groups.values()
        ),
        page_hit_rate=_mean(
            _mean(_page_hit_at(_ranking(item, retriever), depth) for item in group)
            for group in groups.values()
        ),
        page_mrr=_mean(
            _mean(_page_rank_at(_ranking(item, retriever), depth) for item in group)
            for group in groups.values()
        ),
    )


def _slice_summaries(
    results: Sequence[DiagnosticCaseResult],
    field: Literal["origin", "runtime_stage", "variant"],
) -> dict[str, DiagnosticSliceSummary]:
    values = sorted({str(getattr(result, field)) for result in results})
    return {
        value: DiagnosticSliceSummary(
            case_count=len(selected),
            need_group_count=len(_group_results(selected)),
            variant_level=_retriever_metrics(selected, group_macro=False),
            need_group_macro=_retriever_metrics(selected, group_macro=True),
        )
        for value in values
        if (
            selected := [
                result for result in results if getattr(result, field) == value
            ]
        )
    }


def _paired_comparison(
    results: Sequence[DiagnosticCaseResult],
    left: RetrieverName,
    right: RetrieverName,
) -> DiagnosticPairedComparison:
    return DiagnosticPairedComparison(
        at_4=_paired_depth(results, left, right, 4),
        at_5=_paired_depth(results, left, right, 5),
    )


def _paired_depth(
    results: Sequence[DiagnosticCaseResult],
    left: RetrieverName,
    right: RetrieverName,
    depth: Literal[4, 5],
) -> DiagnosticPairedDepth:
    groups = _group_results(results)
    hit_deltas: list[float] = []
    rank_deltas: list[float] = []
    for group in groups.values():
        hit_deltas.append(
            _mean(_section_hit_at(_ranking(item, left), depth) for item in group)
            - _mean(_section_hit_at(_ranking(item, right), depth) for item in group)
        )
        rank_deltas.append(
            _mean(_section_rank_at(_ranking(item, left), depth) for item in group)
            - _mean(_section_rank_at(_ranking(item, right), depth) for item in group)
        )
    return DiagnosticPairedDepth(
        groups=len(groups),
        section_hit=_paired_delta(hit_deltas),
        section_reciprocal_rank=_paired_delta(rank_deltas),
        section_hit_interval=_bootstrap_interval(
            hit_deltas,
            seed=_bootstrap_seed(left, right, depth, "hit"),
        ),
        section_reciprocal_rank_interval=_bootstrap_interval(
            rank_deltas,
            seed=_bootstrap_seed(left, right, depth, "mrr"),
        ),
    )


def _paired_delta(deltas: Sequence[float]) -> PairedMetricDelta:
    epsilon = 1e-12
    return PairedMetricDelta(
        mean_delta=_mean(deltas),
        wins=sum(delta > epsilon for delta in deltas),
        ties=sum(abs(delta) <= epsilon for delta in deltas),
        losses=sum(delta < -epsilon for delta in deltas),
    )


def _bootstrap_seed(
    left: RetrieverName,
    right: RetrieverName,
    depth: Literal[4, 5],
    metric: Literal["hit", "mrr"],
) -> int:
    digest = hashlib.sha256(f"{left}:{right}:{depth}:{metric}".encode()).digest()
    return _BOOTSTRAP_SEED ^ int.from_bytes(digest[:4], "big")


def _bootstrap_interval(
    deltas: Sequence[float],
    *,
    seed: int,
) -> DiagnosticBootstrapInterval:
    if not deltas:
        raise ValueError("paired bootstrap requires at least one need group")
    values = tuple(deltas)
    randomizer = random.Random(seed)
    estimates = sorted(
        _mean(randomizer.choice(values) for _ in values)
        for _ in range(_BOOTSTRAP_REPLICATES)
    )
    return DiagnosticBootstrapInterval(
        seed=seed,
        mean=_mean(values),
        lower=_percentile(estimates, 0.025),
        upper=_percentile(estimates, 0.975),
    )


def _percentile(values: Sequence[float], quantile: float) -> float:
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction


def _ranking(
    result: DiagnosticCaseResult,
    retriever: RetrieverName,
) -> DiagnosticRankingResult:
    if retriever == "bm25":
        return result.bm25
    if retriever == "dense":
        return result.dense
    return result.hybrid


def _section_hit_at(
    ranking: DiagnosticRankingResult,
    depth: Literal[4, 5],
) -> float:
    return float(ranking.section_hit_at_4 if depth == 4 else ranking.section_hit_at_5)


def _section_rank_at(
    ranking: DiagnosticRankingResult,
    depth: Literal[4, 5],
) -> float:
    return (
        ranking.section_reciprocal_rank_at_4
        if depth == 4
        else ranking.section_reciprocal_rank_at_5
    )


def _page_hit_at(ranking: DiagnosticRankingResult, depth: Literal[4, 5]) -> float:
    return float(ranking.page_hit_at_4 if depth == 4 else ranking.page_hit_at_5)


def _page_rank_at(ranking: DiagnosticRankingResult, depth: Literal[4, 5]) -> float:
    return (
        ranking.page_reciprocal_rank_at_4
        if depth == 4
        else ranking.page_reciprocal_rank_at_5
    )


def _group_cases(
    cases: Sequence[DiagnosticCase],
) -> dict[str, list[DiagnosticCase]]:
    groups: dict[str, list[DiagnosticCase]] = defaultdict(list)
    for case in cases:
        groups[case.need_group_id].append(case)
    return dict(groups)


def _group_results(
    results: Sequence[DiagnosticCaseResult],
) -> dict[str, list[DiagnosticCaseResult]]:
    groups: dict[str, list[DiagnosticCaseResult]] = defaultdict(list)
    for result in results:
        groups[result.need_group_id].append(result)
    return dict(groups)


def _gold_contract(
    pages: Sequence[DiagnosticGoldPage],
) -> tuple[str, ...]:
    return tuple(sorted(page.model_dump_json() for page in pages))


def _semantic_section_metadata(
    index: AbaqusDocumentIndex,
) -> dict[str, set[tuple[str, str]]]:
    result = index.vector_store.get(
        where={"retrieval_role": "semantic"},
        include=["metadatas"],
    )
    sections: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for metadata in result.get("metadatas") or []:
        if not metadata or not metadata.get("document_id"):
            continue
        document_id = str(metadata["document_id"])
        sections.setdefault(document_id, set())
        section_id = metadata.get("section_id")
        section_title = metadata.get("section_title")
        if (
            isinstance(section_id, str)
            and section_id
            and isinstance(section_title, str)
            and section_title.strip()
        ):
            sections[document_id].add((section_id, section_title))
    return dict(sections)


def _derive_section_targets(
    tree: etree._ElementTree,
    page: DiagnosticGoldPage,
    indexed_sections: set[tuple[str, str]],
) -> set[tuple[str, str, str]]:
    """把细粒度 HTML anchor 映射到索引实际保存的 section 组合。

    Abaqus HTML 的 H3 article 可能被切分在外层 H2 content root 内，因此
    HTML anchor 不一定等于 chunk 的 ``section_id``。这里从 anchor 的标题和
    ancestor IDs 推导候选，再只接受当前 semantic index 中真实存在的
    ``(section_id, section_title)`` 组合。
    """
    targets: set[tuple[str, str, str]] = set()
    for anchor_id in page.section_ids:
        anchors = tree.xpath("//*[@id=$anchor_id]", anchor_id=anchor_id)
        for anchor in anchors:
            if not isinstance(anchor, etree._Element):
                continue
            title = _anchor_section_title(anchor)
            if title is None:
                continue
            normalized_title = _normalized_section_title(title)
            ancestor_ids = {
                str(element.get("id"))
                for element in (anchor, *anchor.iterancestors())
                if element.get("id")
            }
            for section_id, section_title in indexed_sections:
                if (
                    section_id in ancestor_ids
                    and _normalized_section_title(section_title) == normalized_title
                ):
                    targets.add((page.document_id, section_id, section_title))
    return targets


def _anchor_section_title(anchor: etree._Element) -> str | None:
    tag = etree.QName(anchor).localname.casefold()
    headings: list[etree._Element]
    if tag in {"h1", "h2", "h3"}:
        headings = [anchor]
    else:
        headings = [
            heading
            for heading in anchor.xpath(".//*[self::h1 or self::h2 or self::h3]")
            if isinstance(heading, etree._Element)
        ]
    if not headings:
        headings = [
            heading
            for heading in anchor.xpath(
                "preceding::*[self::h1 or self::h2 or self::h3][1]"
            )
            if isinstance(heading, etree._Element)
        ]
    if not headings:
        return None
    heading = headings[0]
    direct_text = [heading.text or ""]
    direct_text.extend(child.tail or "" for child in heading)
    title = " ".join(" ".join(direct_text).split())
    return title or None


def _normalized_section_title(value: str) -> str:
    return " ".join(value.casefold().split())


def _verify_html_page(
    path: Path,
    page: DiagnosticGoldPage,
) -> etree._ElementTree:
    try:
        tree = etree.parse(str(path), etree.HTMLParser())
    except (OSError, etree.XMLSyntaxError) as exc:
        raise ValueError(f"cannot parse diagnostic gold HTML: {path}") from exc
    identifiers = {
        str(value).strip()
        for value in tree.xpath(
            "//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
            "'abcdefghijklmnopqrstuvwxyz')='dc.identifier']/@content"
        )
    }
    body_ids = {str(value).strip() for value in tree.xpath("//body/@id")}
    if page.document_id not in identifiers:
        raise ValueError(f"gold HTML DC.identifier mismatch: {page.document_id}")
    if page.document_id not in body_ids:
        raise ValueError(f"gold HTML body id mismatch: {page.document_id}")
    html_ids = {str(value) for value in tree.xpath("//*[@id]/@id")}
    missing = set(page.section_ids) - html_ids
    if missing:
        raise ValueError(
            f"gold HTML section IDs are missing for {page.document_id}: "
            f"{sorted(missing)!r}"
        )
    return tree


def _verify_source_locator(case: DiagnosticCase, path: Path) -> None:
    if case.source_binding.kind == "official_html":
        try:
            tree = etree.parse(str(path), etree.HTMLParser())
        except (OSError, etree.XMLSyntaxError) as exc:
            raise ValueError(f"cannot parse diagnostic source HTML: {path}") from exc
        locator = case.source_binding.locator.removeprefix("#")
        if not tree.xpath("//*[@id=$locator]", locator=locator):
            raise ValueError(
                f"diagnostic source section is missing for {case.case_id}: {locator}"
            )
        return

    match = _LINE_LOCATOR.fullmatch(case.source_binding.locator)
    assert match is not None
    assert case.source_binding.encoding is not None
    lines = path.read_text(encoding=case.source_binding.encoding).splitlines()
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if end < start or end > len(lines):
        raise ValueError(f"diagnostic source line range is invalid: {case.case_id}")
    selected = lines[start - 1 : end]
    selected_text = " ".join(" ".join(selected).split())
    if case.variant == "verbatim_excerpt":
        query = " ".join(case.query.split())
        if len(query.split()) > _OBSERVED_QUERY_TOKEN_LIMIT:
            raise ValueError(f"verbatim diagnostic excerpt is too long: {case.case_id}")
        if _normalized_query(query) not in _normalized_query(selected_text):
            raise ValueError(
                f"verbatim diagnostic excerpt does not match its source: {case.case_id}"
            )
    elif case.variant == "normalized":
        expected = normalize_observed_diagnostic(selected_text)
        if case.query != expected:
            raise ValueError(
                f"normalized diagnostic query is not deterministic: {case.case_id}"
            )


def _bound_relative_file(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise ValueError(f"diagnostic path escapes its root: {relative_path}")
    if not candidate.is_file():
        raise ValueError(f"diagnostic path is not a file: {relative_path}")
    return candidate


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


def _hash_strings(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def normalize_observed_diagnostic(value: str) -> str:
    """从绑定的 Solver 文本生成稳定、去易变字段的检索 query。"""
    normalized = " ".join(value.split())
    normalized = _DIAGNOSTIC_PREFIX.sub("", normalized)
    normalized = _FILE_LINE.sub("", normalized)
    normalized = _VOLATILE_NUMBER.sub("<n>", normalized)
    return " ".join(normalized.casefold().split()[:_OBSERVED_QUERY_TOKEN_LIMIT])


def _normalized_query(query: str) -> str:
    return " ".join(query.casefold().split())


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _write_text_new_atomic(path: Path, content: str) -> None:
    """在同目录原子发布新文件，并拒绝覆盖已有证据。

    Args:
        path: 输出文件路径；父目录不存在时会创建。
        content: 按 UTF-8 和 LF 写入的完整文本。

    Raises:
        ValueError: 目标文件已经存在。
        OSError: 临时文件写入或硬链接发布失败。
    """
    expanded = path.expanduser()
    expanded.parent.mkdir(parents=True, exist_ok=True)
    target = expanded.parent.resolve(strict=True) / expanded.name
    if os.path.lexists(target):
        raise ValueError(f"diagnostic output already exists: {target}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise ValueError(f"diagnostic output already exists: {target}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m abaqus_sim_agent.retrieval.diagnostic",
        description="Audit or evaluate provenance-bound diagnostic retrieval cases.",
    )
    parser.add_argument("command", choices=("audit", "seal", "evaluate"))
    parser.add_argument("--index-dir", type=Path, default=Path("var/rag"))
    parser.add_argument("--collection", default=DEFAULT_COLLECTION_NAME)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--environment-seal", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--docs-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("--diagnostic-seal", type=Path)
    parser.add_argument("--reference-report", type=Path)
    parser.add_argument("--reference-attempts", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _validate_cli_arguments(args)
        index = AbaqusDocumentIndex(
            args.index_dir,
            collection_name=args.collection,
        )
        audited = audit_diagnostic_dataset(
            index,
            args.dataset,
            review_path=args.review,
            environment_seal_path=args.environment_seal,
            repository_root=args.repository_root,
            docs_root=args.docs_root,
            run_root=args.run_root,
        )
        if args.command == "audit":
            print(audited.audit.model_dump_json(indent=2))
            return 0
        assert args.output is not None
        if args.command == "seal":
            assert args.reference_report is not None
            assert args.reference_attempts is not None
            sealed = create_diagnostic_seal(
                index,
                audited,
                args.output,
                reference_report_path=args.reference_report,
                reference_attempts_path=args.reference_attempts,
                warmup=not args.no_warmup,
            )
            print(sealed.seal.model_dump_json(indent=2))
            return 0
        assert args.diagnostic_seal is not None
        output = _resolve_output_path(args.output, index.persist_directory)
        report = evaluate_diagnostic_dataset(
            index,
            audited,
            diagnostic_seal_path=args.diagnostic_seal,
            warmup=not args.no_warmup,
        )
        _write_text_new_atomic(output, report.model_dump_json(indent=2) + "\n")
        print(report.model_dump_json(indent=2))
        return 0
    except (BM25IndexError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    return 2


def _validate_cli_arguments(args: argparse.Namespace) -> None:
    if args.command == "audit":
        if any(
            value is not None
            for value in (
                args.output,
                args.diagnostic_seal,
                args.reference_report,
                args.reference_attempts,
            )
        ):
            raise ValueError("audit does not accept seal or output arguments")
        return
    if args.output is None:
        raise ValueError(f"{args.command} requires --output")
    if args.command == "seal":
        if args.diagnostic_seal is not None:
            raise ValueError("seal does not accept --diagnostic-seal")
        if args.reference_report is None or args.reference_attempts is None:
            raise ValueError(
                "seal requires --reference-report and --reference-attempts"
            )
        return
    if args.reference_report is not None or args.reference_attempts is not None:
        raise ValueError("evaluate takes reference evidence from --diagnostic-seal")
    if args.diagnostic_seal is None:
        raise ValueError("evaluate requires --diagnostic-seal")


def _resolve_output_path(path: Path, index_root: Path) -> Path:
    expanded = path.expanduser()
    resolved_index = index_root.resolve(strict=True)
    candidate = expanded.parent.resolve(strict=False) / expanded.name
    if candidate.is_relative_to(resolved_index):
        raise ValueError("diagnostic report must be outside the retrieval index")
    expanded.parent.mkdir(parents=True, exist_ok=True)
    target = expanded.parent.resolve(strict=True) / expanded.name
    if target.is_relative_to(resolved_index):
        raise ValueError("diagnostic report must be outside the retrieval index")
    if os.path.lexists(target):
        raise ValueError(f"diagnostic output already exists: {target}")
    return target


if __name__ == "__main__":
    raise SystemExit(main())
