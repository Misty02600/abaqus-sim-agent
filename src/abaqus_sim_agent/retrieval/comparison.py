from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .index import AbaqusDocumentIndex, RetrievalHit, reciprocal_rank_fusion

RetrieverName = Literal["bm25", "dense", "hybrid"]
QueryKind = Literal["concept", "identifier_context", "identifier_lookup"]


class ComparisonCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    query_kind: QueryKind
    query: str = Field(min_length=1)
    gold_document_ids: list[str] = Field(min_length=1)
    requires_multiple_documents: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def validate_gold_documents(self) -> ComparisonCase:
        if len(set(self.gold_document_ids)) != len(self.gold_document_ids):
            raise ValueError("gold_document_ids must be unique")
        if self.requires_multiple_documents and len(self.gold_document_ids) < 2:
            raise ValueError(
                "multi-document cases require at least two gold document IDs"
            )
        return self


class RankedCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieved_document_ids: list[str]
    hit: bool
    recall: float = Field(ge=0, le=1)
    reciprocal_rank: float = Field(ge=0, le=1)
    elapsed_ms: float = Field(ge=0)


class ComparisonCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    query_kind: QueryKind
    query: str
    gold_document_ids: list[str]
    requires_multiple_documents: bool = False
    bm25: RankedCaseResult
    dense: RankedCaseResult
    hybrid: RankedCaseResult
    fusion_elapsed_ms: float = Field(ge=0)


class MetricSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: int = Field(ge=0)
    hit_rate_at_k: float = Field(ge=0, le=1)
    recall_at_k: float = Field(ge=0, le=1)
    mrr: float = Field(ge=0, le=1)
    p50_latency_ms: float = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)


class RetrieverSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    all: MetricSummary
    natural_language: MetricSummary
    concept: MetricSummary
    identifier_context: MetricSummary
    identifier_lookup: MetricSummary
    multi_document: MetricSummary | None = None


class PairedMetricDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mean_delta: float = Field(ge=-1, le=1)
    wins: int = Field(ge=0)
    ties: int = Field(ge=0)
    losses: int = Field(ge=0)


class PairedComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: int = Field(ge=0)
    recall_at_k: PairedMetricDelta
    reciprocal_rank: PairedMetricDelta


class HitComplementarity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: int = Field(ge=0)
    bm25_only: int = Field(ge=0)
    dense_only: int = Field(ge=0)
    both: int = Field(ge=0)
    neither: int = Field(ge=0)


class ComparisonProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    benchmark_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dense_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bm25_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bm25_database_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bm25_semantic_corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    release: str
    collection_name: str
    dense_embedding: str
    bm25_algorithm: str
    bm25_tokenizer: str
    bm25_k1: float
    bm25_b: float


class RetrievalComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1"] = "1"
    evaluated_at: datetime
    top_k: int = Field(gt=0)
    fetch_k: int = Field(gt=0)
    rrf_k: int = Field(gt=0)
    case_count: int = Field(gt=0)
    primary_slice: Literal["natural_language"] = "natural_language"
    provenance: ComparisonProvenance
    bm25: RetrieverSummary
    dense: RetrieverSummary
    hybrid: RetrieverSummary
    bm25_vs_dense: PairedComparison
    hybrid_vs_dense: PairedComparison
    hybrid_vs_bm25: PairedComparison
    natural_language_hit_complementarity: HitComplementarity
    cases: list[ComparisonCaseResult]


def load_comparison_cases(path: str | Path) -> list[ComparisonCase]:
    source = Path(path)
    cases: list[ComparisonCase] = []
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            cases.append(ComparisonCase.model_validate_json(line))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid retrieval comparison case at {source}:{line_number}"
            ) from exc
    if not cases:
        raise ValueError(f"retrieval comparison benchmark is empty: {source}")
    duplicate_ids = _duplicates(case.case_id for case in cases)
    if duplicate_ids:
        raise ValueError(
            f"retrieval comparison case IDs must be unique: {duplicate_ids!r}"
        )
    duplicate_queries = _duplicates(case.query for case in cases)
    if duplicate_queries:
        raise ValueError(
            f"retrieval comparison queries must be unique: {duplicate_queries!r}"
        )
    return cases


def evaluate_comparison(
    index: AbaqusDocumentIndex,
    cases: list[ComparisonCase],
    *,
    benchmark_sha256: str,
    top_k: int = 5,
    fetch_k: int | None = None,
    rrf_k: int = 60,
    warmup: bool = True,
) -> RetrievalComparison:
    """在相同查询和候选语料上比较 BM25、Dense 与 RRF。

    Args:
        index: 同时绑定 Dense 与 BM25 持久索引的文档索引。
        cases: 不包含算法提示或显式符号旁路的 query-centric Case。
        benchmark_sha256: 原始 benchmark 文件的 SHA-256，用于报告溯源。
        top_k: 计算命中、Recall 和 MRR 的最终排名深度。
        fetch_k: 每个单路检索器交给 RRF 的父文档候选数；默认至少为 20。
        rrf_k: Reciprocal Rank Fusion 的固定平滑常数。
        warmup: 是否在计时前用首条查询预热两个检索器。

    Returns:
        含分片指标、配对差值、互补命中和逐 Case 排名的严格报告。
    """
    if not cases:
        raise ValueError("comparison cases must not be empty")
    if top_k < 1:
        raise ValueError("top_k must be positive")
    resolved_fetch_k = max(top_k * 4, 20) if fetch_k is None else fetch_k
    if resolved_fetch_k < top_k:
        raise ValueError("fetch_k must be greater than or equal to top_k")
    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")

    index.verify_bm25_corpus()
    if warmup:
        index.search_bm25(cases[0].query, top_k=1)
        index.search_concepts(cases[0].query, top_k=1)

    results: list[ComparisonCaseResult] = []
    for case in cases:
        bm25_started = time.perf_counter()
        bm25_hits = index.search_bm25(case.query, top_k=resolved_fetch_k)
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
                top_k=resolved_fetch_k,
            )
        ]
        dense_elapsed_ms = (time.perf_counter() - dense_started) * 1000

        fusion_started = time.perf_counter()
        hybrid_hits = reciprocal_rank_fusion(
            dense_hits,
            bm25_hits,
            top_k=top_k,
            rrf_k=rrf_k,
        )
        fusion_elapsed_ms = (time.perf_counter() - fusion_started) * 1000
        results.append(
            ComparisonCaseResult(
                case_id=case.case_id,
                query_kind=case.query_kind,
                query=case.query,
                gold_document_ids=case.gold_document_ids,
                requires_multiple_documents=case.requires_multiple_documents,
                bm25=_score_ranking(
                    bm25_hits[:top_k],
                    case.gold_document_ids,
                    elapsed_ms=bm25_elapsed_ms,
                ),
                dense=_score_ranking(
                    dense_hits[:top_k],
                    case.gold_document_ids,
                    elapsed_ms=dense_elapsed_ms,
                ),
                hybrid=_score_ranking(
                    hybrid_hits,
                    case.gold_document_ids,
                    elapsed_ms=(bm25_elapsed_ms + dense_elapsed_ms + fusion_elapsed_ms),
                ),
                fusion_elapsed_ms=fusion_elapsed_ms,
            )
        )

    index.verify_bm25_corpus()
    dense_manifest = index.read_manifest()
    bm25_manifest = index.read_bm25_manifest()
    natural_results = [
        result for result in results if result.query_kind != "identifier_lookup"
    ]
    return RetrievalComparison(
        evaluated_at=datetime.now(UTC),
        top_k=top_k,
        fetch_k=resolved_fetch_k,
        rrf_k=rrf_k,
        case_count=len(results),
        provenance=ComparisonProvenance(
            benchmark_sha256=benchmark_sha256,
            dense_manifest_sha256=_sha256_file(index.manifest_path),
            bm25_manifest_sha256=_sha256_file(index.bm25_manifest_path),
            bm25_database_sha256=bm25_manifest.database_sha256,
            bm25_semantic_corpus_sha256=(bm25_manifest.semantic_corpus_sha256),
            release=dense_manifest.release,
            collection_name=dense_manifest.collection_name,
            dense_embedding=dense_manifest.embedding,
            bm25_algorithm=bm25_manifest.algorithm,
            bm25_tokenizer=bm25_manifest.tokenizer,
            bm25_k1=bm25_manifest.k1,
            bm25_b=bm25_manifest.b,
        ),
        bm25=_retriever_summary(results, "bm25"),
        dense=_retriever_summary(results, "dense"),
        hybrid=_retriever_summary(results, "hybrid"),
        bm25_vs_dense=_paired_comparison(natural_results, "bm25", "dense"),
        hybrid_vs_dense=_paired_comparison(natural_results, "hybrid", "dense"),
        hybrid_vs_bm25=_paired_comparison(natural_results, "hybrid", "bm25"),
        natural_language_hit_complementarity=_hit_complementarity(natural_results),
        cases=results,
    )


def benchmark_sha256(path: str | Path) -> str:
    return _sha256_file(Path(path))


def _score_ranking(
    hits: Sequence[RetrievalHit],
    gold_document_ids: Sequence[str],
    *,
    elapsed_ms: float,
) -> RankedCaseResult:
    document_ids = [_parent_id(hit) for hit in hits]
    gold = set(gold_document_ids)
    matched = gold.intersection(document_ids)
    first_rank = next(
        (
            rank
            for rank, document_id in enumerate(document_ids, start=1)
            if document_id in gold
        ),
        None,
    )
    return RankedCaseResult(
        retrieved_document_ids=document_ids,
        hit=bool(matched),
        recall=len(matched) / len(gold),
        reciprocal_rank=0.0 if first_rank is None else 1 / first_rank,
        elapsed_ms=elapsed_ms,
    )


def _retriever_summary(
    results: Sequence[ComparisonCaseResult],
    retriever: RetrieverName,
) -> RetrieverSummary:
    return RetrieverSummary(
        all=_metric_summary(results, retriever),
        natural_language=_metric_summary(
            [result for result in results if result.query_kind != "identifier_lookup"],
            retriever,
        ),
        concept=_metric_summary(
            [result for result in results if result.query_kind == "concept"],
            retriever,
        ),
        identifier_context=_metric_summary(
            [result for result in results if result.query_kind == "identifier_context"],
            retriever,
        ),
        identifier_lookup=_metric_summary(
            [result for result in results if result.query_kind == "identifier_lookup"],
            retriever,
        ),
        multi_document=_metric_summary(
            [result for result in results if result.requires_multiple_documents],
            retriever,
        ),
    )


def _metric_summary(
    results: Sequence[ComparisonCaseResult],
    retriever: RetrieverName,
) -> MetricSummary:
    ranked = [_ranking(result, retriever) for result in results]
    latencies = sorted(item.elapsed_ms for item in ranked)
    return MetricSummary(
        cases=len(ranked),
        hit_rate_at_k=_mean(float(item.hit) for item in ranked),
        recall_at_k=_mean(item.recall for item in ranked),
        mrr=_mean(item.reciprocal_rank for item in ranked),
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
    )


def _paired_comparison(
    results: Sequence[ComparisonCaseResult],
    left: RetrieverName,
    right: RetrieverName,
) -> PairedComparison:
    recall_deltas = [
        _ranking(result, left).recall - _ranking(result, right).recall
        for result in results
    ]
    rank_deltas = [
        _ranking(result, left).reciprocal_rank - _ranking(result, right).reciprocal_rank
        for result in results
    ]
    return PairedComparison(
        cases=len(results),
        recall_at_k=_paired_metric(recall_deltas),
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
    results: Sequence[ComparisonCaseResult],
) -> HitComplementarity:
    bm25_only = dense_only = both = neither = 0
    for result in results:
        if result.bm25.hit and result.dense.hit:
            both += 1
        elif result.bm25.hit:
            bm25_only += 1
        elif result.dense.hit:
            dense_only += 1
        else:
            neither += 1
    return HitComplementarity(
        cases=len(results),
        bm25_only=bm25_only,
        dense_only=dense_only,
        both=both,
        neither=neither,
    )


def _ranking(
    result: ComparisonCaseResult,
    retriever: RetrieverName,
) -> RankedCaseResult:
    return getattr(result, retriever)


def _parent_id(hit: RetrievalHit) -> str:
    return str(hit.document.metadata.get("document_id", hit.document.id or ""))


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)
