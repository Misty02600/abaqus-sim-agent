from __future__ import annotations

import json
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .index import AbaqusDocumentIndex


class RetrievalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    mode: Literal["concept", "dense", "augmented", "symbol"]
    query: str = Field(min_length=1)
    exact_terms: list[str] = Field(default_factory=list)
    products: list[str] = Field(default_factory=list)
    gold_document_ids: list[str] = Field(min_length=1)
    pair_id: str | None = None
    notes: str = ""


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    mode: Literal["concept", "dense", "augmented", "symbol"]
    query: str
    pair_id: str | None
    retrieved_document_ids: list[str]
    hit: bool
    recall: float = Field(ge=0, le=1)
    reciprocal_rank: float = Field(ge=0, le=1)
    elapsed_ms: float = Field(ge=0)


class RetrievalEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    top_k: int = Field(gt=0)
    concept_cases: int = Field(ge=0)
    concept_recall_at_k: float = Field(ge=0, le=1)
    concept_mrr: float = Field(ge=0, le=1)
    dense_cases: int = Field(ge=0)
    dense_recall_at_k: float = Field(ge=0, le=1)
    dense_mrr: float = Field(ge=0, le=1)
    augmented_cases: int = Field(ge=0)
    augmented_recall_at_k: float = Field(ge=0, le=1)
    augmented_mrr: float = Field(ge=0, le=1)
    symbol_cases: int = Field(ge=0)
    symbol_accuracy: float = Field(ge=0, le=1)
    paired_cases: int = Field(ge=0)
    paired_recall_delta: float = Field(ge=-1, le=1)
    paired_mrr_delta: float = Field(ge=-1, le=1)
    p50_latency_ms: float = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)
    cases: list[CaseResult]


def load_cases(path: str | Path) -> list[RetrievalCase]:
    source = Path(path)
    cases: list[RetrievalCase] = []
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            cases.append(RetrievalCase.model_validate_json(line))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"invalid retrieval case at {source}:{line_number}"
            ) from exc
    if not cases:
        raise ValueError(f"retrieval benchmark is empty: {source}")
    return cases


def evaluate_retrieval(
    index: AbaqusDocumentIndex,
    cases: list[RetrievalCase],
    *,
    top_k: int = 5,
) -> RetrievalEvaluation:
    if top_k < 1:
        raise ValueError("top_k must be positive")

    results: list[CaseResult] = []
    for case in cases:
        started = time.perf_counter()
        if case.mode in {"concept", "dense"}:
            documents = [
                document
                for document, _distance in index.search_concepts(
                    case.query,
                    top_k=top_k,
                )
            ]
        elif case.mode == "augmented":
            documents = [
                hit.document
                for hit in index.search(
                    case.query,
                    exact_terms=case.exact_terms,
                    products=case.products,
                    top_k=top_k,
                )
            ]
        else:
            documents = index.lookup_symbol(
                case.query,
                products=case.products,
                limit=top_k,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000
        document_ids = [
            str(document.metadata.get("document_id", "")) for document in documents
        ]
        gold = set(case.gold_document_ids)
        matched = gold.intersection(document_ids)
        first_rank = next(
            (
                rank
                for rank, document_id in enumerate(document_ids, start=1)
                if document_id in gold
            ),
            None,
        )
        results.append(
            CaseResult(
                case_id=case.case_id,
                mode=case.mode,
                query=case.query,
                pair_id=case.pair_id,
                retrieved_document_ids=document_ids,
                hit=bool(matched),
                recall=len(matched) / len(gold),
                reciprocal_rank=0 if first_rank is None else 1 / first_rank,
                elapsed_ms=elapsed_ms,
            )
        )

    concept_results = [result for result in results if result.mode == "concept"]
    dense_results = [result for result in results if result.mode == "dense"]
    augmented_results = [result for result in results if result.mode == "augmented"]
    symbol_results = [result for result in results if result.mode == "symbol"]
    dense_by_pair = {
        result.pair_id: result for result in dense_results if result.pair_id is not None
    }
    augmented_by_pair = {
        result.pair_id: result
        for result in augmented_results
        if result.pair_id is not None
    }
    paired_ids = sorted(dense_by_pair.keys() & augmented_by_pair.keys())
    latencies = sorted(result.elapsed_ms for result in results)
    return RetrievalEvaluation(
        top_k=top_k,
        concept_cases=len(concept_results),
        concept_recall_at_k=_mean(result.recall for result in concept_results),
        concept_mrr=_mean(result.reciprocal_rank for result in concept_results),
        dense_cases=len(dense_results),
        dense_recall_at_k=_mean(result.recall for result in dense_results),
        dense_mrr=_mean(result.reciprocal_rank for result in dense_results),
        augmented_cases=len(augmented_results),
        augmented_recall_at_k=_mean(result.recall for result in augmented_results),
        augmented_mrr=_mean(result.reciprocal_rank for result in augmented_results),
        symbol_cases=len(symbol_results),
        symbol_accuracy=_mean(float(result.hit) for result in symbol_results),
        paired_cases=len(paired_ids),
        paired_recall_delta=_mean(
            augmented_by_pair[pair_id].recall - dense_by_pair[pair_id].recall
            for pair_id in paired_ids
        ),
        paired_mrr_delta=_mean(
            augmented_by_pair[pair_id].reciprocal_rank
            - dense_by_pair[pair_id].reciprocal_rank
            for pair_id in paired_ids
        ),
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
        cases=results,
    )


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
