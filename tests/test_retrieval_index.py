from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path
from typing import Literal

import pytest

from abaqus_sim_agent.retrieval import (
    AbaqusDocumentIndex,
    AbaqusDocumentLoader,
    BM25IndexError,
    RetrievalHit,
    reciprocal_rank_fusion,
)
from abaqus_sim_agent.retrieval.types import Document, Embeddings

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "abaqus_docs"
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
        return [_embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return _embed(text)


def _embed(text: str) -> list[float]:
    words = re.findall(r"[a-z]+", text.casefold())
    vector = [float(words.count(token)) for token in VOCABULARY]
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return [0.0] * (len(VOCABULARY) - 1) + [1.0]
    return [value / norm for value in vector]


def build_fixture_index(index_dir: Path) -> AbaqusDocumentIndex:
    loader = AbaqusDocumentLoader(FIXTURE_ROOT, guide_ids=GUIDES)
    index, _manifest = AbaqusDocumentIndex.build(
        loader,
        index_dir,
        embeddings=FixtureEmbeddings(),
        collection_name="fixture_docs",
    )
    return index


def test_flat_index_preserves_page_provenance_and_section_heading_context(
    tmp_path: Path,
) -> None:
    source = Path(__file__).parent / "fixtures/abaqus_docs_scope"
    index, _ = AbaqusDocumentIndex.build(
        AbaqusDocumentLoader(source, guide_ids=["SIMACAEOUTRefMap"]),
        tmp_path / "context-index",
        embeddings=FixtureEmbeddings(),
        collection_name="context_fixture",
    )
    reopened = AbaqusDocumentIndex(
        tmp_path / "context-index",
        embeddings=FixtureEmbeddings(),
        collection_name="context_fixture",
    )
    assert (
        reopened.read_manifest().document_context_version
        == "abaqus-document-context-v2"
    )
    data = reopened.vector_store.get(
        where={"document_id": "scope-demo"}, include=["metadatas"]
    )
    metadata = next(
        m for m in data["metadatas"] if m["section_id"] == "explicit-output"
    )
    assert metadata["products"] == ["Abaqus/Standard", "Abaqus/Explicit"]
    assert metadata["products_source"] == "official_page_header"
    assert metadata["section_product_mentions"] == ["Abaqus/Explicit"]
    assert "Output in Abaqus/Explicit" in metadata["heading_path"]
    assert index.read_manifest() == reopened.read_manifest()


def test_index_persists_semantic_and_symbol_retrieval(tmp_path: Path) -> None:
    index, manifest = AbaqusDocumentIndex.build(
        AbaqusDocumentLoader(FIXTURE_ROOT, guide_ids=GUIDES),
        tmp_path / "index",
        embeddings=FixtureEmbeddings(),
        collection_name="fixture_docs",
    )

    assert manifest.release == "2026 FD02"
    assert manifest.files_loaded == 4
    assert manifest.documents_indexed > manifest.files_loaded
    assert manifest.embedding == "FixtureEmbeddings"
    assert manifest.document_context_version == "abaqus-document-context-v2"
    assert index.read_manifest() == manifest

    concepts = index.search_concepts("static inertia equilibrium", top_k=2)
    assert concepts[0][0].metadata["document_id"] == "simaanl-c-static"
    assert len({item[0].metadata["document_id"] for item in concepts}) == len(concepts)

    elements = index.lookup_symbol(" cps4r ")
    assert [document.metadata["document_id"] for document in elements] == [
        "simaelm-r-2delem"
    ]
    assert index.lookup_symbol("*static")[0].metadata["entity_kind"] == "keyword"
    assert index.lookup_symbol("u")[0].metadata["entity_kind"] == "output_variable"
    assert (
        index.lookup_symbol("u", products=["Abaqus/Standard"])[0].metadata[
            "document_id"
        ]
        == "simaout-c-std-nodalvariables"
    )

    reopened = AbaqusDocumentIndex(
        tmp_path / "index",
        embeddings=FixtureEmbeddings(),
        collection_name="fixture_docs",
    )
    assert reopened.lookup_symbol("ALLSDTOL")[0].metadata["document_id"] == (
        "simakey-r-static"
    )


def test_flat_index_materializes_metadata_filters_in_sqlite(tmp_path: Path) -> None:
    index = build_fixture_index(tmp_path / "index")
    with sqlite3.connect(index.vector_store.database_path) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(documents)")
        }
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        plan = " ".join(
            str(row[3])
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT row_no FROM documents "
                "WHERE retrieval_role = 'semantic'"
            )
        )

    assert {
        "document_id",
        "release",
        "guide_id",
        "retrieval_role",
        "entity_kind",
        "primary_symbol",
    } <= columns
    assert {"document_products", "document_symbols"} <= tables
    assert "idx_documents_role" in plan


def test_explicit_symbol_is_ranked_before_semantic_results(tmp_path: Path) -> None:
    index = build_fixture_index(tmp_path / "index")

    hits = index.search(
        "Which quadrilateral should be used?",
        exact_terms=["CPS4R"],
        top_k=3,
    )

    assert hits[0].match_type == "exact"
    assert hits[0].document.metadata["primary_symbol"] == "CPS4R"
    assert any(hit.match_type == "semantic" for hit in hits[1:])


def test_multiple_symbols_leave_room_for_semantic_evidence(tmp_path: Path) -> None:
    index = build_fixture_index(tmp_path / "index")

    hits = index.search(
        "static plane stress model with displacement output",
        exact_terms=["*STATIC", "CPS4R", "U"],
        products=["Abaqus/Standard"],
        top_k=3,
    )

    assert [hit.match_type for hit in hits].count("exact") == 2
    assert [hit.match_type for hit in hits].count("semantic") == 1
    assert len({hit.document.metadata["document_id"] for hit in hits}) == len(hits)


def test_index_builds_bm25_from_the_same_semantic_corpus(tmp_path: Path) -> None:
    index = build_fixture_index(tmp_path / "index")

    manifest = index.build_bm25()
    bm25_hits = index.search_bm25("CPS4R plane stress", top_k=3)
    hybrid_hits = index.search_hybrid("CPS4R plane stress", top_k=3)

    semantic_records = index.vector_store.get(
        where={"retrieval_role": "semantic"},
        include=["metadatas"],
    )
    assert manifest.documents_indexed == len(semantic_records["ids"])
    assert bm25_hits
    assert all(
        hit.document.metadata["retrieval_role"] == "semantic" for hit in bm25_hits
    )
    assert len(hybrid_hits) == 3
    assert all(hit.match_type == "hybrid" for hit in hybrid_hits)


def test_bm25_rejects_current_dense_semantic_corpus_drift(tmp_path: Path) -> None:
    index_directory = tmp_path / "index"
    index = build_fixture_index(index_directory)
    index.build_bm25()
    semantic_records = index.vector_store.get(
        where={"retrieval_role": "semantic"},
        include=[],
    )
    index.vector_store.delete(ids=[str(semantic_records["ids"][0])])

    with pytest.raises(BM25IndexError, match="current dense semantic corpus"):
        index.search_bm25("static equilibrium")

    with pytest.raises(ValueError, match="document database hash"):
        AbaqusDocumentIndex(
            index_directory,
            embeddings=FixtureEmbeddings(),
            collection_name="fixture_docs",
        )


def test_hybrid_rechecks_dense_corpus_after_bm25_was_cached(tmp_path: Path) -> None:
    index = build_fixture_index(tmp_path / "index")
    index.build_bm25()
    assert index.search_bm25("static equilibrium")
    semantic_records = index.vector_store.get(
        where={"retrieval_role": "semantic"},
        include=[],
    )
    index.vector_store.delete(ids=[str(semantic_records["ids"][0])])

    with pytest.raises(BM25IndexError, match="current dense semantic corpus"):
        index.search_hybrid("static equilibrium")


def test_reciprocal_rank_fusion_is_deterministic_and_uses_parent_ids() -> None:
    def hit(
        record_id: str,
        parent_id: str,
        match_type: Literal["semantic", "bm25"],
    ) -> RetrievalHit:
        return RetrievalHit(
            document=Document(
                id=record_id,
                page_content=record_id,
                metadata={"document_id": parent_id},
            ),
            match_type=match_type,
        )

    dense = [
        hit("a#dense", "a", "semantic"),
        hit("a#duplicate", "a", "semantic"),
        hit("b#dense", "b", "semantic"),
    ]
    bm25 = [
        hit("b#bm25", "b", "bm25"),
        hit("a#bm25", "a", "bm25"),
    ]

    fused = reciprocal_rank_fusion(dense, bm25, top_k=2, rrf_k=60)

    assert [item.document.metadata["document_id"] for item in fused] == ["a", "b"]
    assert all(item.match_type == "hybrid" for item in fused)
    assert fused[0].document.id == "a#dense"
    assert fused[0].score == fused[1].score
