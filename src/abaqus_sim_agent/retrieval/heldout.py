from __future__ import annotations

import hashlib
import time
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import lxml.etree as etree
from pydantic import BaseModel, ConfigDict, Field, model_validator

from abaqus_sim_agent.retrieval.types import Document

from .comparison import PairedMetricDelta, load_comparison_cases
from .index import AbaqusDocumentIndex, RetrievalHit, reciprocal_rank_fusion

HeldoutStratum = Literal["concept", "identifier_context", "multi_document"]
RetrieverName = Literal["bm25", "dense", "hybrid"]
_REQUIRED_IMPLEMENTATION_FILES = {
    "src/abaqus_sim_agent/retrieval/bm25.py",
    "src/abaqus_sim_agent/retrieval/cli.py",
    "src/abaqus_sim_agent/retrieval/comparison.py",
    "src/abaqus_sim_agent/retrieval/heldout.py",
    "src/abaqus_sim_agent/retrieval/index.py",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HeldoutQueryOrigin(_StrictModel):
    kind: Literal["predefined_scenario", "real_task", "user_question"]
    source_ref: str = Field(min_length=1)


class HeldoutQuery(_StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    stratum: HeldoutStratum
    query: str = Field(min_length=1)
    origin: HeldoutQueryOrigin
    notes: str = ""


class AcceptablePage(_StrictModel):
    document_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    section_ids: tuple[str, ...] = Field(min_length=1)
    evidence_paraphrase: str = Field(min_length=1)
    family_ids: tuple[str, ...] = Field(min_length=1)


class HeldoutRequirement(_StrictModel):
    requirement_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    acceptable_pages: tuple[AcceptablePage, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pages(self) -> HeldoutRequirement:
        document_ids = [page.document_id for page in self.acceptable_pages]
        if len(set(document_ids)) != len(document_ids):
            raise ValueError("acceptable page document IDs must be unique")
        return self


class AnnotationMetadata(_StrictModel):
    method: Literal["direct_official_html"]
    retrieval_assistance: tuple[str, ...] = ()
    annotator_id: str = Field(min_length=1)
    reviewer_id: str | None = None
    completed_at: datetime
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def reject_retrieval_assistance(self) -> AnnotationMetadata:
        if self.retrieval_assistance:
            raise ValueError("held-out gold annotation cannot use retrieval assistance")
        return self


class HeldoutLabel(_StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    annotation_status: Literal["adjudicated"]
    requirements: tuple[HeldoutRequirement, ...] = Field(min_length=1)
    annotation: AnnotationMetadata

    @model_validator(mode="after")
    def validate_requirements(self) -> HeldoutLabel:
        requirement_ids = [item.requirement_id for item in self.requirements]
        if len(set(requirement_ids)) != len(requirement_ids):
            raise ValueError("requirement IDs must be unique within a case")
        return self


class DocumentFamily(_StrictModel):
    family_id: str = Field(min_length=1)
    split: Literal["development", "heldout"]
    member_document_ids: tuple[str, ...] = Field(min_length=1)
    basis: Literal["same_feature", "overview_detail", "keyword_counterpart", "alias"]
    rationale: str = Field(min_length=1)


class FamilyRegistry(_StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str = Field(min_length=1)
    review_method: Literal["manual_feature_family_adjudication"]
    reviewer_id: str = Field(min_length=1)
    reviewed_at: datetime
    scope_note: str = Field(min_length=1)
    families: tuple[DocumentFamily, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_families(self) -> FamilyRegistry:
        family_ids = [family.family_id for family in self.families]
        if len(set(family_ids)) != len(family_ids):
            raise ValueError("family IDs must be unique")
        return self


class HeldoutSeal(_StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str = Field(min_length=1)
    protocol_version: Literal["direct-html-family-disjoint-heldout-v1"]
    status: Literal["sealed"]
    queries_file: str = Field(min_length=1)
    queries_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    labels_file: str = Field(min_length=1)
    labels_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    family_registry_file: str = Field(min_length=1)
    family_registry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    development_benchmark_file: str = Field(min_length=1)
    development_benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    development_gold_document_ids: tuple[str, ...] = Field(min_length=1)
    development_gold_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    release: str = Field(min_length=1)
    dense_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dense_embedding: str = Field(min_length=1)
    bm25_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bm25_database_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    semantic_corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bm25_algorithm: str = Field(min_length=1)
    bm25_tokenizer: str = Field(min_length=1)
    bm25_k1: float = Field(gt=0)
    bm25_b: float = Field(ge=0, le=1)
    implementation_sha256: dict[str, str]
    top_k: int = Field(gt=0)
    fetch_k: int = Field(gt=0)
    rrf_k: int = Field(gt=0)
    stratum_counts: dict[str, int]
    sealed_at: datetime

    @model_validator(mode="after")
    def validate_seal_contract(self) -> HeldoutSeal:
        if self.fetch_k < self.top_k:
            raise ValueError("held-out fetch_k must be at least top_k")
        if self.stratum_counts != {
            "concept": 12,
            "identifier_context": 12,
            "multi_document": 6,
        }:
            raise ValueError("held-out seal strata must be 12/12/6")
        if tuple(sorted(set(self.development_gold_document_ids))) != (
            self.development_gold_document_ids
        ):
            raise ValueError("development gold IDs must be sorted and unique")
        if set(self.implementation_sha256) != _REQUIRED_IMPLEMENTATION_FILES:
            raise ValueError(
                "held-out seal must bind the complete retrieval implementation"
            )
        for path, digest in self.implementation_sha256.items():
            if (
                not path
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError(
                    "held-out implementation hashes must be lowercase SHA-256"
                )
        return self


class HeldoutAuditReport(_StrictModel):
    dataset_id: str
    query_count: int
    label_count: int
    requirement_count: int
    acceptable_page_count: int
    unique_gold_document_count: int
    stratum_counts: dict[str, int]
    document_disjoint: bool
    declared_family_disjoint: bool
    html_sources_verified: int
    semantic_gold_documents_verified: int


@dataclass(frozen=True)
class AuditedHeldout:
    seal: HeldoutSeal
    seal_sha256: str
    queries: tuple[HeldoutQuery, ...]
    labels: tuple[HeldoutLabel, ...]
    audit: HeldoutAuditReport


class HeldoutRankingResult(_StrictModel):
    retrieved_document_ids: list[str]
    covered_requirement_ids: list[str]
    complete_hit: bool
    requirement_recall: float = Field(ge=0, le=1)
    reciprocal_rank: float = Field(ge=0, le=1)
    elapsed_ms: float = Field(ge=0)


class HeldoutCaseResult(_StrictModel):
    case_id: str
    stratum: HeldoutStratum
    query: str
    requirement_ids: list[str]
    bm25: HeldoutRankingResult
    dense: HeldoutRankingResult
    hybrid: HeldoutRankingResult
    fusion_elapsed_ms: float = Field(ge=0)


class HeldoutMetricSummary(_StrictModel):
    cases: int = Field(ge=0)
    complete_hit_rate_at_k: float = Field(ge=0, le=1)
    requirement_recall_at_k: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    p50_latency_ms: float = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)


class HeldoutRetrieverSummary(_StrictModel):
    all: HeldoutMetricSummary
    concept: HeldoutMetricSummary
    identifier_context: HeldoutMetricSummary
    multi_document: HeldoutMetricSummary


class HeldoutPairedComparison(_StrictModel):
    cases: int = Field(ge=0)
    complete_hit_at_k: PairedMetricDelta
    requirement_recall_at_k: PairedMetricDelta
    reciprocal_rank: PairedMetricDelta


class HeldoutHitComplementarity(_StrictModel):
    cases: int = Field(ge=0)
    bm25_only: int = Field(ge=0)
    dense_only: int = Field(ge=0)
    both: int = Field(ge=0)
    neither: int = Field(ge=0)


class HeldoutEvaluation(_StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str
    evaluated_at: datetime
    seal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    top_k: int
    fetch_k: int
    rrf_k: int
    audit: HeldoutAuditReport
    bm25: HeldoutRetrieverSummary
    dense: HeldoutRetrieverSummary
    hybrid: HeldoutRetrieverSummary
    bm25_vs_dense: HeldoutPairedComparison
    hybrid_vs_dense: HeldoutPairedComparison
    hybrid_vs_bm25: HeldoutPairedComparison
    complete_hit_complementarity: HeldoutHitComplementarity
    cases: list[HeldoutCaseResult]


def load_heldout_queries(path: str | Path) -> list[HeldoutQuery]:
    return _load_jsonl(path, HeldoutQuery, "held-out query")


def load_heldout_labels(path: str | Path) -> list[HeldoutLabel]:
    return _load_jsonl(path, HeldoutLabel, "held-out label")


def audit_heldout(
    index: AbaqusDocumentIndex,
    seal_path: str | Path,
    *,
    repository_root: str | Path,
) -> AuditedHeldout:
    """验证 sealed held-out 的字节、标注、语料和防泄漏边界。

    Args:
        index: 当前 Dense/BM25 组合索引，只用于一致性和gold存在性校验。
        seal_path: 不可变 held-out seal manifest 路径。
        repository_root: implementation hash 中仓库相对路径的解析根目录。

    Returns:
        可交给 held-out evaluator 的审计后数据与审计摘要。

    Raises:
        ValueError: 任一hash、schema、HTML证据、语料或防泄漏合同不成立。
    """
    seal_source = Path(seal_path).resolve(strict=True)
    seal = HeldoutSeal.model_validate_json(seal_source.read_text(encoding="utf-8"))
    benchmark_dir = seal_source.parent
    query_path = _bound_relative_file(benchmark_dir, seal.queries_file)
    label_path = _bound_relative_file(benchmark_dir, seal.labels_file)
    family_path = _bound_relative_file(benchmark_dir, seal.family_registry_file)
    development_path = _bound_relative_file(
        benchmark_dir,
        seal.development_benchmark_file,
    )
    _require_hash(query_path, seal.queries_sha256, "held-out queries")
    _require_hash(label_path, seal.labels_sha256, "held-out labels")
    _require_hash(
        family_path,
        seal.family_registry_sha256,
        "held-out family registry",
    )
    _require_hash(
        development_path,
        seal.development_benchmark_sha256,
        "development benchmark",
    )
    root = Path(repository_root).resolve(strict=True)
    for relative_path, expected_hash in seal.implementation_sha256.items():
        implementation_path = _bound_relative_file(root, relative_path)
        _require_hash(
            implementation_path,
            expected_hash,
            f"implementation file {relative_path}",
        )

    dense_manifest = index.read_manifest()
    bm25_manifest = index.read_bm25_manifest()
    if dense_manifest.release != seal.release:
        raise ValueError("held-out release does not match the dense index")
    if dense_manifest.embedding != seal.dense_embedding:
        raise ValueError("held-out seal does not match the dense embedding")
    _require_hash(index.manifest_path, seal.dense_manifest_sha256, "dense manifest")
    _require_hash(
        index.bm25_manifest_path,
        seal.bm25_manifest_sha256,
        "BM25 manifest",
    )
    if bm25_manifest.database_sha256 != seal.bm25_database_sha256:
        raise ValueError("held-out seal does not match the BM25 database")
    if bm25_manifest.semantic_corpus_sha256 != seal.semantic_corpus_sha256:
        raise ValueError("held-out seal does not match the semantic corpus")
    if (
        bm25_manifest.algorithm != seal.bm25_algorithm
        or bm25_manifest.tokenizer != seal.bm25_tokenizer
        or bm25_manifest.k1 != seal.bm25_k1
        or bm25_manifest.b != seal.bm25_b
    ):
        raise ValueError("held-out seal does not match the BM25 algorithm contract")
    index.verify_bm25_corpus()

    queries = load_heldout_queries(query_path)
    labels = load_heldout_labels(label_path)
    registry = FamilyRegistry.model_validate_json(
        family_path.read_text(encoding="utf-8")
    )
    if any(item.dataset_id != seal.dataset_id for item in (*queries, *labels)):
        raise ValueError("held-out query or label dataset_id does not match the seal")
    if registry.dataset_id != seal.dataset_id:
        raise ValueError("held-out family registry dataset_id does not match the seal")
    _validate_query_shape(queries, seal)
    labels_by_id = _unique_by_case_id(labels, "held-out labels")
    if set(labels_by_id) != {query.case_id for query in queries}:
        raise ValueError("held-out queries and labels must have identical case IDs")
    ordered_labels = tuple(labels_by_id[query.case_id] for query in queries)
    for query, label in zip(queries, ordered_labels, strict=True):
        if label.annotation.reviewer_id is None or label.annotation.reviewed_at is None:
            raise ValueError(
                f"held-out label lacks recorded pre-seal review: {query.case_id}"
            )
        if query.stratum == "multi_document" and len(label.requirements) < 2:
            raise ValueError(
                f"multi-document case needs at least two requirements: {query.case_id}"
            )

    development_cases = load_comparison_cases(development_path)
    development_gold = sorted(
        {
            document_id
            for case in development_cases
            for document_id in case.gold_document_ids
        }
    )
    if tuple(development_gold) != seal.development_gold_document_ids:
        raise ValueError("development gold IDs do not match the held-out seal")
    if _hash_strings(development_gold) != seal.development_gold_set_sha256:
        raise ValueError("development gold set hash does not match the held-out seal")
    development_queries = {_normalized_query(case.query) for case in development_cases}
    if any(_normalized_query(query.query) in development_queries for query in queries):
        raise ValueError("held-out query duplicates a development query")

    families = {family.family_id: family for family in registry.families}
    development_family_documents = {
        document_id
        for family in registry.families
        if family.split == "development"
        for document_id in family.member_document_ids
    }
    heldout_family_documents = {
        document_id
        for family in registry.families
        if family.split == "heldout"
        for document_id in family.member_document_ids
    }
    if not set(development_gold).issubset(development_family_documents):
        raise ValueError("development gold pages are not covered by family registry")

    acceptable_pages = [
        page
        for label in ordered_labels
        for requirement in label.requirements
        for page in requirement.acceptable_pages
    ]
    heldout_gold = {page.document_id for page in acceptable_pages}
    if heldout_gold & set(development_gold):
        raise ValueError("held-out and development gold document IDs overlap")
    if heldout_gold & development_family_documents:
        raise ValueError("held-out gold belongs to a development page family")
    if heldout_family_documents & development_family_documents:
        raise ValueError("development and held-out page-family members overlap")
    if heldout_family_documents & set(development_gold):
        raise ValueError("held-out page families contain development gold")
    for page in acceptable_pages:
        if not page.family_ids:
            raise ValueError(f"gold page has no family: {page.document_id}")
        for family_id in page.family_ids:
            family = families.get(family_id)
            if family is None or family.split != "heldout":
                raise ValueError(
                    f"gold page references a non-held-out family: {family_id}"
                )
            if page.document_id not in family.member_document_ids:
                raise ValueError(
                    f"gold page is absent from its family registry: {page.document_id}"
                )

    semantic_ids = _semantic_document_ids(index)
    docs_root = Path(dense_manifest.docs_root).resolve(strict=True)
    verified_sources: set[tuple[str, str]] = set()
    for page in acceptable_pages:
        if page.document_id not in semantic_ids:
            raise ValueError(
                f"held-out gold has no semantic record: {page.document_id}"
            )
        source = _bound_relative_file(docs_root, page.source_path)
        _require_hash(source, page.source_sha256, f"gold HTML {page.document_id}")
        _verify_html_page(source, page)
        verified_sources.add((page.document_id, page.source_sha256))

    requirement_count = sum(len(label.requirements) for label in ordered_labels)
    stratum_counts = dict(Counter(query.stratum for query in queries))
    return AuditedHeldout(
        seal=seal,
        seal_sha256=_sha256_file(seal_source),
        queries=tuple(queries),
        labels=ordered_labels,
        audit=HeldoutAuditReport(
            dataset_id=seal.dataset_id,
            query_count=len(queries),
            label_count=len(ordered_labels),
            requirement_count=requirement_count,
            acceptable_page_count=len(acceptable_pages),
            unique_gold_document_count=len(heldout_gold),
            stratum_counts=stratum_counts,
            document_disjoint=True,
            declared_family_disjoint=True,
            html_sources_verified=len(verified_sources),
            semantic_gold_documents_verified=len(heldout_gold),
        ),
    )


def evaluate_heldout(
    index: AbaqusDocumentIndex,
    dataset: AuditedHeldout,
    *,
    warmup: bool = True,
) -> HeldoutEvaluation:
    """使用 seal 中冻结的参数对 held-out 执行一次公平三臂评测。"""
    top_k = dataset.seal.top_k
    fetch_k = dataset.seal.fetch_k
    rrf_k = dataset.seal.rrf_k
    if fetch_k < top_k:
        raise ValueError("held-out fetch_k must be at least top_k")
    index.verify_bm25_corpus()
    if warmup:
        index.search_bm25(dataset.queries[0].query, top_k=1)
        index.search_concepts(dataset.queries[0].query, top_k=1)

    results: list[HeldoutCaseResult] = []
    for query, label in zip(dataset.queries, dataset.labels, strict=True):
        bm25_started = time.perf_counter()
        bm25_hits = index.search_bm25(query.query, top_k=fetch_k)
        bm25_elapsed = (time.perf_counter() - bm25_started) * 1000
        dense_started = time.perf_counter()
        dense_hits = [
            RetrievalHit(document=document, match_type="semantic", distance=distance)
            for document, distance in index.search_concepts(
                query.query,
                top_k=fetch_k,
            )
        ]
        dense_elapsed = (time.perf_counter() - dense_started) * 1000
        fusion_started = time.perf_counter()
        hybrid_hits = reciprocal_rank_fusion(
            dense_hits,
            bm25_hits,
            top_k=top_k,
            rrf_k=rrf_k,
        )
        fusion_elapsed = (time.perf_counter() - fusion_started) * 1000
        results.append(
            HeldoutCaseResult(
                case_id=query.case_id,
                stratum=query.stratum,
                query=query.query,
                requirement_ids=[item.requirement_id for item in label.requirements],
                bm25=_score_requirements(
                    bm25_hits[:top_k],
                    label.requirements,
                    elapsed_ms=bm25_elapsed,
                ),
                dense=_score_requirements(
                    dense_hits[:top_k],
                    label.requirements,
                    elapsed_ms=dense_elapsed,
                ),
                hybrid=_score_requirements(
                    hybrid_hits,
                    label.requirements,
                    elapsed_ms=bm25_elapsed + dense_elapsed + fusion_elapsed,
                ),
                fusion_elapsed_ms=fusion_elapsed,
            )
        )
    index.verify_bm25_corpus()
    return HeldoutEvaluation(
        dataset_id=dataset.seal.dataset_id,
        evaluated_at=datetime.now().astimezone(),
        seal_sha256=dataset.seal_sha256,
        top_k=top_k,
        fetch_k=fetch_k,
        rrf_k=rrf_k,
        audit=dataset.audit,
        bm25=_retriever_summary(results, "bm25"),
        dense=_retriever_summary(results, "dense"),
        hybrid=_retriever_summary(results, "hybrid"),
        bm25_vs_dense=_paired_comparison(results, "bm25", "dense"),
        hybrid_vs_dense=_paired_comparison(results, "hybrid", "dense"),
        hybrid_vs_bm25=_paired_comparison(results, "hybrid", "bm25"),
        complete_hit_complementarity=_hit_complementarity(results),
        cases=results,
    )


def _score_requirements(
    hits: Sequence[RetrievalHit],
    requirements: Sequence[HeldoutRequirement],
    *,
    elapsed_ms: float,
) -> HeldoutRankingResult:
    document_ids = [_parent_id(hit.document) for hit in hits]
    covered: list[str] = []
    relevant_documents: set[str] = set()
    for requirement in requirements:
        acceptable = {page.document_id for page in requirement.acceptable_pages}
        relevant_documents.update(acceptable)
        if acceptable.intersection(document_ids):
            covered.append(requirement.requirement_id)
    first_rank = next(
        (
            rank
            for rank, document_id in enumerate(document_ids, start=1)
            if document_id in relevant_documents
        ),
        None,
    )
    return HeldoutRankingResult(
        retrieved_document_ids=document_ids,
        covered_requirement_ids=covered,
        complete_hit=len(covered) == len(requirements),
        requirement_recall=len(covered) / len(requirements),
        reciprocal_rank=0.0 if first_rank is None else 1 / first_rank,
        elapsed_ms=elapsed_ms,
    )


def _retriever_summary(
    results: Sequence[HeldoutCaseResult],
    retriever: RetrieverName,
) -> HeldoutRetrieverSummary:
    return HeldoutRetrieverSummary(
        all=_metric_summary(results, retriever),
        concept=_metric_summary(
            [result for result in results if result.stratum == "concept"],
            retriever,
        ),
        identifier_context=_metric_summary(
            [result for result in results if result.stratum == "identifier_context"],
            retriever,
        ),
        multi_document=_metric_summary(
            [result for result in results if result.stratum == "multi_document"],
            retriever,
        ),
    )


def _metric_summary(
    results: Sequence[HeldoutCaseResult],
    retriever: RetrieverName,
) -> HeldoutMetricSummary:
    rankings = [_ranking(result, retriever) for result in results]
    latencies = sorted(item.elapsed_ms for item in rankings)
    return HeldoutMetricSummary(
        cases=len(rankings),
        complete_hit_rate_at_k=_mean(float(item.complete_hit) for item in rankings),
        requirement_recall_at_k=_mean(item.requirement_recall for item in rankings),
        mrr=_mean(item.reciprocal_rank for item in rankings),
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
    )


def _paired_comparison(
    results: Sequence[HeldoutCaseResult],
    left: RetrieverName,
    right: RetrieverName,
) -> HeldoutPairedComparison:
    complete_deltas = [
        float(_ranking(result, left).complete_hit)
        - float(_ranking(result, right).complete_hit)
        for result in results
    ]
    recall_deltas = [
        _ranking(result, left).requirement_recall
        - _ranking(result, right).requirement_recall
        for result in results
    ]
    rank_deltas = [
        _ranking(result, left).reciprocal_rank - _ranking(result, right).reciprocal_rank
        for result in results
    ]
    return HeldoutPairedComparison(
        cases=len(results),
        complete_hit_at_k=_paired_metric(complete_deltas),
        requirement_recall_at_k=_paired_metric(recall_deltas),
        reciprocal_rank=_paired_metric(rank_deltas),
    )


def _paired_metric(deltas: Sequence[float]) -> PairedMetricDelta:
    epsilon = 1e-12
    return PairedMetricDelta(
        mean_delta=_mean(deltas),
        wins=sum(delta > epsilon for delta in deltas),
        ties=sum(abs(delta) <= epsilon for delta in deltas),
        losses=sum(delta < -epsilon for delta in deltas),
    )


def _hit_complementarity(
    results: Sequence[HeldoutCaseResult],
) -> HeldoutHitComplementarity:
    bm25_only = dense_only = both = neither = 0
    for result in results:
        if result.bm25.complete_hit and result.dense.complete_hit:
            both += 1
        elif result.bm25.complete_hit:
            bm25_only += 1
        elif result.dense.complete_hit:
            dense_only += 1
        else:
            neither += 1
    return HeldoutHitComplementarity(
        cases=len(results),
        bm25_only=bm25_only,
        dense_only=dense_only,
        both=both,
        neither=neither,
    )


def _validate_query_shape(
    queries: Sequence[HeldoutQuery],
    seal: HeldoutSeal,
) -> None:
    if len(queries) != 30:
        raise ValueError("held-out query set must contain exactly 30 cases")
    case_ids = [query.case_id for query in queries]
    normalized_queries = [_normalized_query(query.query) for query in queries]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("held-out case IDs must be unique")
    if len(set(normalized_queries)) != len(normalized_queries):
        raise ValueError("held-out normalized queries must be unique")
    counts = dict(Counter(query.stratum for query in queries))
    expected = {"concept": 12, "identifier_context": 12, "multi_document": 6}
    if counts != expected or counts != seal.stratum_counts:
        raise ValueError("held-out query strata must be 12/12/6")


def _verify_html_page(path: Path, page: AcceptablePage) -> None:
    try:
        tree = etree.parse(str(path), etree.HTMLParser())
    except (OSError, etree.XMLSyntaxError) as exc:
        raise ValueError(f"cannot parse held-out gold HTML: {path}") from exc
    identifiers = tree.xpath(
        "//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
        "'abcdefghijklmnopqrstuvwxyz')='dc.identifier']/@content"
    )
    body_ids = tree.xpath("//body/@id")
    if page.document_id not in {str(value).strip() for value in identifiers}:
        raise ValueError(f"gold HTML DC.identifier mismatch: {page.document_id}")
    if page.document_id not in {str(value).strip() for value in body_ids}:
        raise ValueError(f"gold HTML body id mismatch: {page.document_id}")
    html_ids = {str(value) for value in tree.xpath("//*[@id]/@id")}
    missing_sections = set(page.section_ids) - html_ids
    if missing_sections:
        raise ValueError(
            f"gold HTML section IDs are missing for {page.document_id}: "
            f"{sorted(missing_sections)!r}"
        )


def _semantic_document_ids(index: AbaqusDocumentIndex) -> set[str]:
    result = index.vector_store.get(
        where={"retrieval_role": "semantic"},
        include=["metadatas"],
    )
    return {
        str(metadata.get("document_id"))
        for metadata in result.get("metadatas") or []
        if metadata and metadata.get("document_id")
    }


def _load_jsonl[ModelT: BaseModel](
    path: str | Path,
    model: type[ModelT],
    label: str,
) -> list[ModelT]:
    source = Path(path)
    items: list[ModelT] = []
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            items.append(model.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"invalid {label} at {source}:{line_number}") from exc
    if not items:
        raise ValueError(f"{label} file is empty: {source}")
    return items


def _unique_by_case_id(
    labels: Sequence[HeldoutLabel],
    description: str,
) -> dict[str, HeldoutLabel]:
    result: dict[str, HeldoutLabel] = {}
    for label in labels:
        if label.case_id in result:
            raise ValueError(f"duplicate case ID in {description}: {label.case_id}")
        result[label.case_id] = label
    return result


def _bound_relative_file(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise ValueError(f"held-out path escapes its root: {relative_path}")
    if not candidate.is_file():
        raise ValueError(f"held-out path is not a file: {relative_path}")
    return candidate


def _require_hash(path: Path, expected: str, description: str) -> None:
    actual = _sha256_file(path)
    if actual != expected:
        raise ValueError(
            f"{description} hash mismatch: expected {expected}, got {actual}"
        )


def _hash_strings(values: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(values):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_query(query: str) -> str:
    return " ".join(query.casefold().split())


def _parent_id(document: Document) -> str:
    return str(document.metadata.get("document_id", document.id or ""))


def _ranking(
    result: HeldoutCaseResult,
    retriever: RetrieverName,
) -> HeldoutRankingResult:
    if retriever == "bm25":
        return result.bm25
    if retriever == "dense":
        return result.dense
    return result.hybrid


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction
