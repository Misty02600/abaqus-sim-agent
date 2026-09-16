from __future__ import annotations

import math
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.retrieval import AbaqusDocumentIndex, AbaqusDocumentLoader
from abaqus_sim_agent.retrieval.comparison import (
    ComparisonCase,
    benchmark_sha256,
    evaluate_comparison,
    load_comparison_cases,
)
from abaqus_sim_agent.retrieval.types import Embeddings

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "abaqus_docs"
BENCHMARK_PATH = (
    Path(__file__).parent.parent
    / "benchmarks"
    / "rag"
    / "abaqus_2026_bm25_dense_rrf.jsonl"
)
GUIDES = [
    "SIMACAEANLRefMap",
    "SIMACAEKEYRefMap",
    "SIMACAEELMRefMap",
    "SIMACAEOUTRefMap",
]
VOCABULARY = (
    "static",
    "inertia",
    "equilibrium",
    "output",
    "displacement",
    "stress",
    "element",
    "reaction",
)


class FixtureEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        words = re.findall(r"[a-z]+", text.casefold())
        vector = [float(words.count(token)) for token in VOCABULARY]
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return [0.0] * (len(VOCABULARY) - 1) + [1.0]
        return [value / norm for value in vector]


def _index(path: Path) -> AbaqusDocumentIndex:
    index, _manifest = AbaqusDocumentIndex.build(
        AbaqusDocumentLoader(FIXTURE_ROOT, guide_ids=GUIDES),
        path,
        embeddings=FixtureEmbeddings(),
        collection_name="comparison_fixture",
    )
    index.build_bm25()
    return index


def test_comparison_schema_rejects_algorithm_side_channels() -> None:
    with pytest.raises(ValidationError):
        ComparisonCase.model_validate(
            {
                "case_id": "leaky",
                "query_kind": "identifier_context",
                "query": "Where is C3D8R documented?",
                "gold_document_ids": ["simaelm-r-3delem"],
                "exact_terms": ["C3D8R"],
            }
        )
    with pytest.raises(ValidationError, match="at least two gold"):
        ComparisonCase(
            case_id="not-multi",
            query_kind="concept",
            query="Find two kinds of evidence.",
            gold_document_ids=["only-one"],
            requires_multiple_documents=True,
        )


def test_repository_comparison_benchmark_is_query_centric() -> None:
    cases = load_comparison_cases(BENCHMARK_PATH)

    assert len(cases) == 31
    assert len({case.case_id for case in cases}) == 31
    assert len({case.query for case in cases}) == 31
    assert sum(case.query_kind == "concept" for case in cases) == 10
    assert sum(case.query_kind == "identifier_context" for case in cases) == 11
    assert sum(case.query_kind == "identifier_lookup" for case in cases) == 10
    assert sum(len(case.gold_document_ids) == 1 for case in cases) == 30
    assert sum(len(case.gold_document_ids) == 4 for case in cases) == 1
    assert (
        len({document_id for case in cases for document_id in case.gold_document_ids})
        == 19
    )


def test_comparison_reuses_one_component_ranking_per_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index(tmp_path / "index")
    cases = [
        ComparisonCase(
            case_id="static",
            query_kind="concept",
            query="static inertia equilibrium",
            gold_document_ids=["simaanl-c-static"],
        ),
        ComparisonCase(
            case_id="element",
            query_kind="identifier_context",
            query="Where is the CPS4R plane stress element documented?",
            gold_document_ids=["simaelm-r-2delem"],
        ),
    ]
    bm25_calls: list[str] = []
    dense_calls: list[str] = []
    original_bm25 = index.search_bm25
    original_dense = index.search_concepts

    def counted_bm25(query: str, *, top_k: int = 5):
        bm25_calls.append(query)
        return original_bm25(query, top_k=top_k)

    def counted_dense(query: str, *, top_k: int = 5):
        dense_calls.append(query)
        return original_dense(query, top_k=top_k)

    monkeypatch.setattr(index, "search_bm25", counted_bm25)
    monkeypatch.setattr(index, "search_concepts", counted_dense)
    monkeypatch.setattr(
        index,
        "search",
        lambda *_args, **_kwargs: pytest.fail("exact augmentation must not run"),
    )

    report = evaluate_comparison(
        index,
        cases,
        benchmark_sha256="0" * 64,
        top_k=3,
        fetch_k=4,
        warmup=False,
    )

    assert bm25_calls == [case.query for case in cases]
    assert dense_calls == [case.query for case in cases]
    assert report.case_count == 2
    assert report.bm25.natural_language.cases == 2
    assert report.dense.natural_language.cases == 2
    assert report.hybrid.natural_language.cases == 2
    assert report.hybrid.multi_document is not None
    assert report.hybrid.multi_document.cases == 0
    assert report.hybrid_vs_dense.cases == 2
    assert (
        report.natural_language_hit_complementarity.bm25_only
        + report.natural_language_hit_complementarity.dense_only
        + report.natural_language_hit_complementarity.both
        + report.natural_language_hit_complementarity.neither
        == 2
    )
    assert report.provenance.benchmark_sha256 == "0" * 64


def test_benchmark_hash_is_stable_shape() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", benchmark_sha256(BENCHMARK_PATH))
