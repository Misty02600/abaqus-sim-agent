from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path

import pytest
from langchain_core.documents import Document

from abaqus_sim_agent.retrieval.bm25 import (
    BM25_B,
    BM25_K1,
    BM25_MANIFEST_NAME,
    BM25DocumentIndex,
    BM25IndexError,
    BM25IndexManifest,
    tokenize_abaqus_lexical,
)


def _write_dense_manifest(index_directory: Path, *, marker: str = "v1") -> Path:
    path = index_directory / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "collection_name": "fixture_docs",
                "release": "2026 FD02",
                "marker": marker,
            }
        )
    )
    return path


def _documents() -> list[Document]:
    return [
        Document(
            id="static-a",
            page_content="Document: Static analysis\nUse *STATIC for equilibrium.",
            metadata={
                "document_id": "static-page",
                "retrieval_role": "semantic",
                "title": "Static analysis",
            },
        ),
        Document(
            id="static-b",
            page_content="Static procedure with automatic time incrementation.",
            metadata={
                "document_id": "static-page",
                "retrieval_role": "semantic",
                "title": "Static procedure details",
            },
        ),
        Document(
            id="mesh-a",
            page_content="CPS4R is a plane-stress quadrilateral element.",
            metadata={
                "document_id": "mesh-page",
                "retrieval_role": "semantic",
                "title": "Continuum elements",
            },
        ),
    ]


def _build(index_directory: Path) -> BM25DocumentIndex:
    _write_dense_manifest(index_directory)
    index, _manifest = BM25DocumentIndex.build(_documents(), index_directory)
    return index


def test_passage_search_retains_same_page_records_without_changing_default(
    tmp_path: Path,
):
    index = _build(tmp_path)
    pages = index.search("static", top_k=3)
    passages = index.search_passages("static", top_k=3)
    assert len(pages) == 1
    assert {hit.document.id for hit in passages} == {"static-a", "static-b"}
    assert passages[0].document.id == pages[0].document.id
    assert passages[0].score == pages[0].score


def test_tokenizer_preserves_abaqus_symbols_and_compound_subtokens() -> None:
    assert tokenize_abaqus_lexical("＊STATIC CPS4R SIDE1_EDGES plane-stress") == [
        "*static",
        "static",
        "cps4r",
        "side1_edges",
        "side1",
        "edges",
        "plane-stress",
        "plane",
        "stress",
    ]


def test_build_publishes_content_addressed_database_and_reopens(
    tmp_path: Path,
) -> None:
    index_directory = tmp_path / "index"
    dense_manifest_path = _write_dense_manifest(index_directory)

    index, manifest = BM25DocumentIndex.build(_documents(), index_directory)

    pointer_path = index_directory / BM25_MANIFEST_NAME
    database_path = pointer_path.parent / manifest.database_filename
    assert pointer_path.is_file()
    assert database_path.is_file()
    assert manifest.database_filename == (f"bm25-{manifest.database_sha256}.sqlite3")
    assert hashlib.sha256(database_path.read_bytes()).hexdigest() == (
        manifest.database_sha256
    )
    assert hashlib.sha256(dense_manifest_path.read_bytes()).hexdigest() == (
        manifest.dense_manifest_sha256
    )
    assert manifest.documents_indexed == 3
    assert manifest.collection_name == "fixture_docs"
    assert manifest.release == "2026 FD02"
    assert len(manifest.semantic_corpus_sha256) == 64
    assert manifest.terms_indexed > 0
    assert manifest.postings_indexed >= manifest.terms_indexed
    assert index.read_manifest() == manifest
    assert BM25DocumentIndex.open(index_directory).read_manifest() == manifest

    with sqlite3.connect(database_path) as connection:
        document_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(documents)")
        }
        posting_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(postings)")
        }
    assert document_columns == {
        "doc_no",
        "chunk_id",
        "parent_id",
        "page_content",
        "metadata_json",
        "document_length",
    }
    assert posting_columns == {"term_id", "doc_no", "term_frequency"}


def test_search_uses_okapi_score_and_deduplicates_parent_pages(
    tmp_path: Path,
) -> None:
    index = _build(tmp_path / "index")

    hits = index.search("*STATIC", top_k=3)

    assert [hit.document.id for hit in hits] == ["static-a"]
    token_lengths = [
        len(tokenize_abaqus_lexical(doc.page_content)) for doc in _documents()
    ]
    static_a_tokens = tokenize_abaqus_lexical(_documents()[0].page_content)
    average_length = sum(token_lengths) / 3

    def term_score(term: str, document_frequency: int) -> float:
        frequency = static_a_tokens.count(term)
        inverse_document_frequency = math.log(
            1 + (3 - document_frequency + 0.5) / (document_frequency + 0.5)
        )
        denominator = frequency + BM25_K1 * (
            1 - BM25_B + BM25_B * token_lengths[0] / average_length
        )
        return inverse_document_frequency * frequency * (BM25_K1 + 1) / denominator

    assert hits[0].score == pytest.approx(
        term_score("*static", 1) + term_score("static", 2)
    )


def test_build_does_not_silently_filter_caller_documents(tmp_path: Path) -> None:
    index_directory = tmp_path / "index"
    _write_dense_manifest(index_directory)
    documents = [
        *_documents(),
        Document(
            id="exact-document",
            page_content="EXACT_ONLY_TOKEN",
            metadata={
                "document_id": "exact-page",
                "retrieval_role": "exact",
            },
        ),
    ]

    index, manifest = BM25DocumentIndex.build(documents, index_directory)

    assert manifest.documents_indexed == 4
    assert index.search("exact_only_token")[0].document.id == "exact-document"


def test_open_rejects_dense_manifest_drift(tmp_path: Path) -> None:
    index_directory = tmp_path / "index"
    _build(index_directory)
    _write_dense_manifest(index_directory, marker="changed")

    with pytest.raises(BM25IndexError, match="dense index manifest hash"):
        BM25DocumentIndex.open(index_directory)


def test_open_rejects_database_hash_drift(tmp_path: Path) -> None:
    index_directory = tmp_path / "index"
    index = _build(index_directory)
    with index.database_path.open("ab") as database:
        database.write(b"drift")

    with pytest.raises(BM25IndexError, match="database size"):
        BM25DocumentIndex.open(index_directory)


def test_open_rejects_manifest_count_drift(tmp_path: Path) -> None:
    index_directory = tmp_path / "index"
    index = _build(index_directory)
    pointer_path = index_directory / BM25_MANIFEST_NAME
    data = index.manifest.model_dump(mode="json")
    data["documents_indexed"] += 1
    pointer_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(BM25IndexError, match="counts do not match"):
        BM25DocumentIndex.open(index_directory)


def test_build_rejects_empty_and_duplicate_document_sets(tmp_path: Path) -> None:
    empty_directory = tmp_path / "empty"
    _write_dense_manifest(empty_directory)
    with pytest.raises(ValueError, match="must not be empty"):
        BM25DocumentIndex.build([], empty_directory)

    duplicate_directory = tmp_path / "duplicate"
    _write_dense_manifest(duplicate_directory)
    duplicate = Document(id="same", page_content="one", metadata={})
    with pytest.raises(ValueError, match="IDs must be unique"):
        BM25DocumentIndex.build([duplicate, duplicate], duplicate_directory)


def test_dense_identity_can_be_supplied_only_when_manifest_omits_it(
    tmp_path: Path,
) -> None:
    index_directory = tmp_path / "index"
    dense_manifest_path = index_directory / "manifest.json"
    dense_manifest_path.parent.mkdir(parents=True)
    dense_manifest_path.write_text('{"schema_version":"1"}', encoding="utf-8")

    _index, manifest = BM25DocumentIndex.build(
        _documents(),
        index_directory,
        collection_name="fixture_docs",
        release="2026 FD02",
    )

    assert manifest.collection_name == "fixture_docs"
    assert manifest.release == "2026 FD02"
    _write_dense_manifest(index_directory)
    with pytest.raises(ValueError, match="conflicts with dense index manifest"):
        BM25DocumentIndex.build(
            _documents(),
            index_directory,
            collection_name="different_collection",
        )


def test_build_rejects_noncanonical_scoring_parameters(tmp_path: Path) -> None:
    index_directory = tmp_path / "index"
    _write_dense_manifest(index_directory)

    with pytest.raises(ValueError, match=r"only BM25 k1=1\.2 and b=0\.75"):
        BM25DocumentIndex.build(_documents(), index_directory, k1=2.0)


def test_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    index_directory = tmp_path / "index"
    index = _build(index_directory)
    pointer_path = index_directory / BM25_MANIFEST_NAME
    data = index.manifest.model_dump(mode="json")
    data["unexpected"] = True
    pointer_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(BM25IndexError, match="manifest is invalid"):
        BM25DocumentIndex.open(index_directory)

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        BM25IndexManifest.model_validate(data)
