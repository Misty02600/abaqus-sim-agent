from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from pydantic import BaseModel, ConfigDict, Field

from .bm25 import (
    BM25_MANIFEST_NAME,
    BM25DocumentIndex,
    BM25IndexError,
    BM25IndexManifest,
    semantic_corpus_sha256,
)
from .documents import DOCUMENT_CONTEXT_VERSION, AbaqusDocumentLoader

DEFAULT_COLLECTION_NAME = "abaqus_docs_2026_fd02"
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class RetrievalHit:
    document: Document
    match_type: Literal["exact", "semantic", "bm25", "hybrid"]
    distance: float | None = None
    score: float | None = None


class IndexManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "1"
    document_context_version: str = "unrecorded"
    built_at: datetime
    release: str
    collection_name: str
    docs_root: str
    guide_ids: list[str]
    files_seen: int = Field(ge=0)
    files_loaded: int = Field(ge=0)
    files_skipped: int = Field(ge=0)
    documents_indexed: int = Field(ge=0)
    build_elapsed_seconds: float = Field(ge=0)
    embedding: str
    chunk_size_characters: int = Field(gt=0)
    chunk_overlap_characters: int = Field(ge=0)
    index_size_bytes: int = Field(ge=0)


class AbaqusDocumentIndex:
    def __init__(
        self,
        persist_directory: str | Path,
        *,
        embeddings: Embeddings | None = None,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        create_if_missing: bool = False,
    ) -> None:
        self.persist_directory = Path(persist_directory).expanduser().resolve()
        self.manifest_path = self.persist_directory / MANIFEST_NAME
        if not create_if_missing and not self.manifest_path.is_file():
            raise FileNotFoundError(
                f"RAG index manifest does not exist: {self.manifest_path}"
            )
        self.embeddings = embeddings
        self.collection_name = collection_name
        self._bm25_index: BM25DocumentIndex | None = None
        self.vector_store = Chroma(
            collection_name=collection_name,
            embedding_function=self.embeddings,
            persist_directory=str(self.persist_directory),
            create_collection_if_not_exists=create_if_missing,
        )

    @classmethod
    def build(
        cls,
        loader: AbaqusDocumentLoader,
        persist_directory: str | Path,
        *,
        embeddings: Embeddings | None = None,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        batch_size: int = 128,
        reset: bool = True,
    ) -> tuple[AbaqusDocumentIndex, IndexManifest]:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        started = time.perf_counter()
        index = cls(
            persist_directory,
            embeddings=embeddings,
            collection_name=collection_name,
            create_if_missing=True,
        )
        if reset:
            index.vector_store.reset_collection()

        indexed = 0
        for batch in _batched(loader.lazy_load(), batch_size):
            ids = [_require_document_id(document) for document in batch]
            index.vector_store.add_documents(documents=batch, ids=ids)
            indexed += len(batch)

        if indexed == 0:
            raise ValueError("Abaqus document loader produced no documents")

        manifest = IndexManifest(
            document_context_version=DOCUMENT_CONTEXT_VERSION,
            built_at=datetime.now(UTC),
            release=loader.release,
            collection_name=collection_name,
            docs_root=str(loader.docs_root),
            guide_ids=list(loader.guide_ids),
            files_seen=loader.stats.files_seen,
            files_loaded=loader.stats.files_loaded,
            files_skipped=loader.stats.files_skipped,
            documents_indexed=indexed,
            build_elapsed_seconds=time.perf_counter() - started,
            embedding=(
                type(index.embeddings).__name__
                if index.embeddings is not None
                else "chromadb.DefaultEmbeddingFunction/all-MiniLM-L6-v2"
            ),
            chunk_size_characters=loader.max_chunk_size,
            chunk_overlap_characters=min(100, loader.max_chunk_size // 10),
            index_size_bytes=_directory_size(index.persist_directory),
        )
        index.persist_directory.mkdir(parents=True, exist_ok=True)
        index.manifest_path.write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return index, manifest

    def concept_retriever(self, *, top_k: int = 5) -> BaseRetriever:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        return self.vector_store.as_retriever(
            search_kwargs={"k": top_k, "filter": {"retrieval_role": "semantic"}}
        )

    def search_concepts(
        self,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[tuple[Document, float]]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        candidate_count = top_k * 4
        while True:
            candidates = self.vector_store.similarity_search_with_score(
                query,
                k=candidate_count,
                filter={"retrieval_role": "semantic"},
            )
            results: list[tuple[Document, float]] = []
            seen_parents: set[str] = set()
            for document, distance in candidates:
                parent_id = str(document.metadata.get("document_id", document.id))
                if parent_id in seen_parents:
                    continue
                results.append((document, distance))
                seen_parents.add(parent_id)
                if len(results) == top_k:
                    return results
            if len(candidates) < candidate_count:
                return results
            candidate_count *= 2

    @property
    def bm25_manifest_path(self) -> Path:
        return self.persist_directory / BM25_MANIFEST_NAME

    def build_bm25(self) -> BM25IndexManifest:
        dense_manifest = self.read_manifest()
        if dense_manifest.collection_name != self.collection_name:
            raise ValueError(
                "dense manifest collection does not match the opened collection"
            )
        dense_manifest_sha256 = _sha256_file(self.manifest_path)
        documents = self._semantic_documents()
        if not documents:
            raise ValueError("dense index contains no semantic documents")
        if _sha256_file(self.manifest_path) != dense_manifest_sha256:
            raise BM25IndexError(
                "dense manifest changed while reading BM25 source data"
            )
        _sparse, manifest = BM25DocumentIndex.build(
            documents,
            self.persist_directory,
            dense_manifest_path=self.manifest_path,
        )
        self._bm25_index = None
        return manifest

    def search_bm25(
        self,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[RetrievalHit]:
        sparse = self._open_bm25()
        return [
            RetrievalHit(
                document=hit.document,
                match_type="bm25",
                score=hit.score,
            )
            for hit in sparse.search(query, top_k=top_k)
        ]

    def search_hybrid(
        self,
        query: str,
        *,
        top_k: int = 5,
        fetch_k: int | None = None,
        rrf_k: int = 60,
    ) -> list[RetrievalHit]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        resolved_fetch_k = max(top_k * 4, 20) if fetch_k is None else fetch_k
        if resolved_fetch_k < top_k:
            raise ValueError("fetch_k must be greater than or equal to top_k")
        self.verify_bm25_corpus()
        dense_hits = [
            RetrievalHit(
                document=document,
                match_type="semantic",
                distance=distance,
            )
            for document, distance in self.search_concepts(
                query,
                top_k=resolved_fetch_k,
            )
        ]
        result = reciprocal_rank_fusion(
            dense_hits,
            self.search_bm25(query, top_k=resolved_fetch_k),
            top_k=top_k,
            rrf_k=rrf_k,
        )
        self.verify_bm25_corpus()
        return result

    def search(
        self,
        query: str,
        *,
        exact_terms: Sequence[str] = (),
        products: Sequence[str] = (),
        top_k: int = 5,
    ) -> list[RetrievalHit]:
        """合并显式符号命中与语义召回，不引入额外模型重排。"""
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")

        hits: list[RetrievalHit] = []
        seen_ids: set[str] = set()
        seen_parents: set[str] = set()
        exact_budget = top_k if top_k == 1 else top_k - 1
        for term in exact_terms:
            for document, distance in self.search_symbol(
                term,
                context=query,
                products=products,
                limit=1,
            ):
                document_id = _require_document_id(document)
                parent_id = str(document.metadata.get("document_id", document_id))
                if document_id in seen_ids or parent_id in seen_parents:
                    continue
                hits.append(
                    RetrievalHit(
                        document=document,
                        match_type="exact",
                        distance=distance,
                    )
                )
                seen_ids.add(document_id)
                seen_parents.add(parent_id)
                if len(hits) == exact_budget:
                    break
            if len(hits) == exact_budget:
                break

        for document, distance in self.search_concepts(query, top_k=top_k):
            document_id = _require_document_id(document)
            parent_id = str(document.metadata.get("document_id", document_id))
            if document_id in seen_ids or parent_id in seen_parents:
                continue
            hits.append(
                RetrievalHit(
                    document=document,
                    match_type="semantic",
                    distance=distance,
                )
            )
            seen_ids.add(document_id)
            seen_parents.add(parent_id)
            if len(hits) == top_k:
                break
        return hits

    def search_symbol(
        self,
        symbol: str,
        *,
        context: str,
        products: Sequence[str] = (),
        limit: int = 5,
    ) -> list[tuple[Document, float]]:
        normalized = normalize_symbol(symbol)
        if not normalized:
            raise ValueError("symbol must not be empty")
        if limit < 1:
            raise ValueError("limit must be positive")
        context = context.strip() or normalized
        return self.vector_store.similarity_search_with_score(
            context,
            k=limit,
            filter=_symbol_filter(normalized, products),
        )

    def lookup_symbol(
        self,
        symbol: str,
        *,
        products: Sequence[str] = (),
        limit: int = 20,
    ) -> list[Document]:
        normalized = normalize_symbol(symbol)
        if not normalized:
            raise ValueError("symbol must not be empty")
        if limit < 1:
            raise ValueError("limit must be positive")
        result = self.vector_store.get(
            where=_symbol_filter(normalized, products),
            include=["documents", "metadatas"],
        )
        documents = _documents_from_get_result(result)
        return sorted(
            documents,
            key=lambda document: (
                document.metadata.get("primary_symbol") != normalized,
                _reference_priority(str(document.metadata.get("entity_kind", ""))),
                document.metadata.get("dc_type") != "reference",
                str(document.metadata.get("document_id", "")),
                document.id or "",
            ),
        )[:limit]

    def read_manifest(self) -> IndexManifest:
        return IndexManifest.model_validate_json(
            self.manifest_path.read_text(encoding="utf-8")
        )

    def read_bm25_manifest(self) -> BM25IndexManifest:
        return self._open_bm25().read_manifest()

    def verify_bm25_corpus(self) -> str:
        sparse = self._load_bm25()
        current_corpus_sha256 = semantic_corpus_sha256(self._semantic_documents())
        if current_corpus_sha256 != sparse.manifest.semantic_corpus_sha256:
            raise BM25IndexError(
                "current Chroma semantic corpus does not match the BM25 manifest"
            )
        return current_corpus_sha256

    def _open_bm25(self) -> BM25DocumentIndex:
        if self._bm25_index is None:
            self._load_bm25()
            self.verify_bm25_corpus()
        assert self._bm25_index is not None
        return self._bm25_index

    def _load_bm25(self) -> BM25DocumentIndex:
        if self._bm25_index is None:
            sparse = BM25DocumentIndex.open(
                self.persist_directory,
                dense_manifest_path=self.manifest_path,
            )
            if sparse.manifest.collection_name != self.collection_name:
                raise ValueError(
                    "BM25 manifest collection does not match the opened collection"
                )
            self._bm25_index = sparse
        assert self._bm25_index is not None
        return self._bm25_index

    def _semantic_documents(self) -> list[Document]:
        result = self.vector_store.get(
            where={"retrieval_role": "semantic"},
            include=["documents", "metadatas"],
        )
        return _documents_from_get_result(result)


def normalize_symbol(symbol: str) -> str:
    return " ".join(symbol.strip().upper().split())


def reciprocal_rank_fusion(
    dense_hits: Sequence[RetrievalHit],
    bm25_hits: Sequence[RetrievalHit],
    *,
    top_k: int = 5,
    rrf_k: int = 60,
) -> list[RetrievalHit]:
    """按父文档排名融合 Dense 与 BM25，不比较不可通约的原始分数。"""
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if rrf_k < 1:
        raise ValueError("rrf_k must be positive")

    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    documents: dict[str, Document] = {}
    representative_rank: dict[str, tuple[int, int]] = {}
    for source_priority, hits in enumerate((dense_hits, bm25_hits)):
        seen: set[str] = set()
        parent_rank = 0
        for hit in hits:
            parent_id = str(
                hit.document.metadata.get(
                    "document_id",
                    hit.document.id or "",
                )
            )
            if not parent_id or parent_id in seen:
                continue
            seen.add(parent_id)
            parent_rank += 1
            rank = parent_rank
            scores[parent_id] = scores.get(parent_id, 0.0) + 1 / (rrf_k + rank)
            best_rank[parent_id] = min(best_rank.get(parent_id, rank), rank)
            candidate_rank = (rank, source_priority)
            if candidate_rank < representative_rank.get(
                parent_id,
                (len(hits) + 1, source_priority),
            ):
                representative_rank[parent_id] = candidate_rank
                documents[parent_id] = hit.document

    ranked_ids = sorted(
        scores,
        key=lambda parent_id: (
            -scores[parent_id],
            best_rank[parent_id],
            parent_id,
        ),
    )
    return [
        RetrievalHit(
            document=documents[parent_id],
            match_type="hybrid",
            score=scores[parent_id],
        )
        for parent_id in ranked_ids[:top_k]
    ]


def _batched(
    documents: Iterable[Document],
    batch_size: int,
) -> Iterable[list[Document]]:
    batch: list[Document] = []
    for document in documents:
        batch.append(document)
        if len(batch) == batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _require_document_id(document: Document) -> str:
    if document.id is None:
        raise ValueError("document id is required for indexing")
    return document.id


def _documents_from_get_result(result: dict[str, Any]) -> list[Document]:
    ids = result.get("ids") or []
    texts = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    documents: list[Document] = []
    for document_id, text, metadata in zip(ids, texts, metadatas, strict=False):
        if text is None:
            continue
        documents.append(
            Document(
                id=str(document_id),
                page_content=str(text),
                metadata=dict(metadata or {}),
            )
        )
    return documents


def _reference_priority(entity_kind: str) -> int:
    priorities = {
        "keyword": 0,
        "element": 1,
        "output_variable": 2,
        "keyword_parameter": 3,
    }
    return priorities.get(entity_kind, 9)


def _symbol_filter(symbol: str, products: Sequence[str]) -> dict[str, Any]:
    symbol_filter: dict[str, Any] = {"symbols": {"$contains": symbol}}
    normalized_products = [product.strip() for product in products if product.strip()]
    if not normalized_products:
        return symbol_filter
    product_filters = [
        {"products": {"$contains": product}} for product in normalized_products
    ]
    product_filter: dict[str, Any]
    if len(product_filters) == 1:
        product_filter = product_filters[0]
    else:
        product_filter = {"$or": product_filters}
    return {"$and": [symbol_filter, product_filter]}


def _directory_size(path: Path) -> int:
    total = 0
    if path.is_dir():
        for item in path.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
    return total


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
