from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import numpy as np

from .types import Document, Embeddings


class FlatDocumentStore:
    """SQLite metadata plus an aligned, normalized float32 NumPy matrix."""

    DATABASE_NAME = "documents.sqlite3"
    VECTORS_NAME = "vectors.npy"

    def __init__(
        self,
        directory: str | Path,
        *,
        embeddings: Embeddings,
        create_if_missing: bool = False,
    ) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.database_path = self.directory / self.DATABASE_NAME
        self.vectors_path = self.directory / self.VECTORS_NAME
        self.embeddings = embeddings
        if not create_if_missing and (
            not self.database_path.is_file() or not self.vectors_path.is_file()
        ):
            raise FileNotFoundError(f"flat dense index is incomplete: {self.directory}")

    def replace_all(
        self,
        documents: Sequence[Document],
        *,
        batch_size: int = 128,
    ) -> None:
        if not documents:
            raise ValueError("document set must not be empty")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        ids = [_require_document_id(document) for document in documents]
        if len(ids) != len(set(ids)):
            raise ValueError("document IDs must be unique")
        vectors = _embed_documents(self.embeddings, documents, batch_size=batch_size)
        self.replace_all_with_vectors(documents, vectors)

    def replace_all_with_vectors(
        self,
        documents: Sequence[Document],
        vectors: np.ndarray,
    ) -> None:
        if not documents:
            raise ValueError("document set must not be empty")
        ids = [_require_document_id(document) for document in documents]
        if len(ids) != len(set(ids)):
            raise ValueError("document IDs must be unique")
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(documents):
            raise ValueError("vector matrix must align one-to-one with documents")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if np.any(norms == 0) or not np.isfinite(vectors).all():
            raise ValueError("document embeddings must be finite and nonzero")
        vectors = np.asarray(vectors / norms, dtype=np.float32)

        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_database_name = tempfile.mkstemp(
            prefix=".documents-", suffix=".sqlite3", dir=self.directory
        )
        os.close(descriptor)
        temporary_database = Path(temporary_database_name)
        temporary_vectors = self.directory / f".vectors-{os.getpid()}.npy"
        try:
            _build_database(temporary_database, documents)
            with temporary_vectors.open("wb") as stream:
                np.save(stream, vectors, allow_pickle=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_database, self.database_path)
            os.replace(temporary_vectors, self.vectors_path)
        finally:
            temporary_database.unlink(missing_ok=True)
            temporary_vectors.unlink(missing_ok=True)

    def reset_collection(self) -> None:
        self.database_path.unlink(missing_ok=True)
        self.vectors_path.unlink(missing_ok=True)

    def add_documents(
        self,
        documents: Sequence[Document],
        ids: Sequence[str] | None = None,
    ) -> list[str]:
        if ids is not None and list(ids) != [
            _require_document_id(d) for d in documents
        ]:
            raise ValueError("explicit ids must match document ids")
        existing = self.documents() if self.database_path.is_file() else []
        self.replace_all([*existing, *documents])
        return [_require_document_id(document) for document in documents]

    def count(self) -> int:
        with closing(_read_connection(self.database_path)) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM documents WHERE deleted = 0"
                ).fetchone()[0]
            )

    def documents(self, *, where: dict[str, Any] | None = None) -> list[Document]:
        with closing(_read_connection(self.database_path)) as connection:
            rows = _select_rows(connection, where)
        return [
            Document(
                id=str(row[1]),
                page_content=str(row[2]),
                metadata=json.loads(str(row[3])),
            )
            for row in rows
        ]

    def get(
        self,
        *,
        where: dict[str, Any] | None = None,
        include: Sequence[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        if offset < 0 or (limit is not None and limit < 0):
            raise ValueError("limit and offset must be nonnegative")
        include_set = set(include or ("documents", "metadatas"))
        with closing(_read_connection(self.database_path)) as connection:
            rows = _select_rows(connection, where)
        selected = [
            (
                int(str(row[0])),
                str(row[1]),
                str(row[2]),
                json.loads(str(row[3])),
            )
            for row in rows
        ]
        selected = (
            selected[offset:] if limit is None else selected[offset : offset + limit]
        )
        result: dict[str, Any] = {"ids": [row[1] for row in selected]}
        if "documents" in include_set:
            result["documents"] = [row[2] for row in selected]
        if "metadatas" in include_set:
            result["metadatas"] = [row[3] for row in selected]
        if "embeddings" in include_set:
            vectors = self.vectors()
            result["embeddings"] = [vectors[row[0]].tolist() for row in selected]
        return result

    def delete(self, *, ids: Sequence[str]) -> None:
        if not ids:
            return
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.executemany(
                "UPDATE documents SET deleted = 1 WHERE chunk_id = ?",
                ((item,) for item in ids),
            )
            connection.commit()

    def similarity_search_with_score(
        self,
        query: str,
        *,
        k: int,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[Document, float]]:
        if k < 1:
            raise ValueError("k must be positive")
        query_vector = np.asarray(self.embeddings.embed_query(query), dtype=np.float32)
        norm = float(np.linalg.norm(query_vector))
        if norm == 0:
            raise ValueError("query embedding must be nonzero")
        query_vector /= norm
        with closing(_read_connection(self.database_path)) as connection:
            rows = _select_rows(connection, filter)
        candidates = [
            (
                int(str(row[0])),
                str(row[1]),
                str(row[2]),
                json.loads(str(row[3])),
            )
            for row in rows
        ]
        if not candidates:
            return []
        row_numbers = np.asarray([row[0] for row in candidates], dtype=np.int64)
        scores = self.vectors()[row_numbers] @ query_vector
        count = min(k, len(candidates))
        selected = np.argpartition(scores, -count)[-count:]
        selected = selected[np.argsort(scores[selected], kind="stable")[::-1]]
        return [
            (
                Document(
                    id=candidates[index][1],
                    page_content=candidates[index][2],
                    metadata=candidates[index][3],
                ),
                float(1.0 - scores[index]),
            )
            for index in selected.tolist()
        ]

    def vectors(self) -> np.ndarray:
        vectors = np.load(self.vectors_path, mmap_mode="r", allow_pickle=False)
        if vectors.ndim != 2 or vectors.dtype != np.float32:
            raise ValueError(
                "flat dense vectors must be a two-dimensional float32 array"
            )
        return vectors


def _embed_documents(
    embeddings: Embeddings,
    documents: Sequence[Document],
    *,
    batch_size: int,
) -> np.ndarray:
    batches: list[np.ndarray] = []
    for start in range(0, len(documents), batch_size):
        values = embeddings.embed_documents(
            [
                document.page_content
                for document in documents[start : start + batch_size]
            ]
        )
        batch = np.asarray(values, dtype=np.float32)
        if batch.ndim != 2 or batch.shape[0] != len(values):
            raise ValueError("embedding provider returned an invalid document matrix")
        batches.append(batch)
    vectors = np.concatenate(batches)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("document embeddings must be nonzero")
    return np.asarray(vectors / norms, dtype=np.float32)


def _build_database(path: Path, documents: Sequence[Document]) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode = DELETE;
            PRAGMA synchronous = FULL;
            CREATE TABLE documents (
                row_no INTEGER PRIMARY KEY,
                chunk_id TEXT NOT NULL UNIQUE,
                page_content TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                document_id TEXT,
                release TEXT,
                guide_id TEXT,
                retrieval_role TEXT,
                entity_kind TEXT,
                primary_symbol TEXT,
                deleted INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1))
            );
            CREATE INDEX idx_documents_deleted ON documents(deleted, row_no);
            CREATE INDEX idx_documents_role ON documents(retrieval_role, row_no);
            CREATE INDEX idx_documents_document_id ON documents(document_id);
            CREATE INDEX idx_documents_release_guide ON documents(release, guide_id);
            CREATE INDEX idx_documents_primary_symbol ON documents(primary_symbol);
            CREATE TABLE document_products (
                row_no INTEGER NOT NULL REFERENCES documents(row_no),
                product TEXT NOT NULL,
                PRIMARY KEY(row_no, product)
            ) WITHOUT ROWID;
            CREATE INDEX idx_document_products_product
                ON document_products(product, row_no);
            CREATE TABLE document_symbols (
                row_no INTEGER NOT NULL REFERENCES documents(row_no),
                symbol TEXT NOT NULL,
                PRIMARY KEY(row_no, symbol)
            ) WITHOUT ROWID;
            CREATE INDEX idx_document_symbols_symbol
                ON document_symbols(symbol, row_no);
            """
        )
        connection.executemany(
            "INSERT INTO documents("
            "row_no, chunk_id, page_content, metadata_json, document_id, release, "
            "guide_id, retrieval_role, entity_kind, primary_symbol"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    row_no,
                    _require_document_id(document),
                    document.page_content,
                    json.dumps(
                        document.metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    _optional_text(document.metadata.get("document_id")),
                    _optional_text(document.metadata.get("release")),
                    _optional_text(document.metadata.get("guide_id")),
                    _optional_text(document.metadata.get("retrieval_role")),
                    _optional_text(document.metadata.get("entity_kind")),
                    _optional_text(document.metadata.get("primary_symbol")),
                )
                for row_no, document in enumerate(documents)
            ),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO document_products(row_no, product) VALUES (?, ?)",
            (
                (row_no, str(product))
                for row_no, document in enumerate(documents)
                for product in document.metadata.get("products", [])
            ),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO document_symbols(row_no, symbol) VALUES (?, ?)",
            (
                (row_no, str(symbol))
                for row_no, document in enumerate(documents)
                for symbol in document.metadata.get("symbols", [])
            ),
        )
        connection.commit()


def _select_rows(
    connection: sqlite3.Connection,
    condition: dict[str, Any] | None,
) -> list[tuple[object, ...]]:
    clause, parameters = _compile_filter(condition)
    return connection.execute(
        "SELECT d.row_no, d.chunk_id, d.page_content, d.metadata_json "
        f"FROM documents AS d WHERE d.deleted = 0 AND ({clause}) ORDER BY d.row_no",
        parameters,
    ).fetchall()


def _compile_filter(condition: dict[str, Any] | None) -> tuple[str, list[object]]:
    if not condition:
        return "1", []
    if "$and" in condition or "$or" in condition:
        operator = "$and" if "$and" in condition else "$or"
        children = condition[operator]
        if not isinstance(children, list) or not children:
            raise ValueError("logical metadata filters require a nonempty list")
        compiled = [_compile_filter(child) for child in children]
        joiner = " AND " if operator == "$and" else " OR "
        return (
            "(" + joiner.join(item[0] for item in compiled) + ")",
            [value for item in compiled for value in item[1]],
        )
    if len(condition) != 1:
        raise ValueError("metadata filter objects must contain exactly one field")
    key, expected = next(iter(condition.items()))
    expression, prefix = _metadata_expression(key)
    if not isinstance(expected, dict):
        return f"{expression} = ?", [*prefix, expected]
    if len(expected) != 1:
        raise ValueError("metadata field filters must contain exactly one operator")
    operator, value = next(iter(expected.items()))
    if operator == "$contains" and key in {"products", "symbols"}:
        table = "document_products" if key == "products" else "document_symbols"
        column = "product" if key == "products" else "symbol"
        return (
            f"EXISTS (SELECT 1 FROM {table} AS item "
            f"WHERE item.row_no = d.row_no AND item.{column} = ?)",
            [value],
        )
    if operator in {"$in", "$nin"}:
        if not isinstance(value, list):
            raise ValueError(f"{operator} metadata filter requires a list")
        if not value:
            return ("1" if operator == "$nin" else "0"), []
        placeholders = ",".join("?" for _ in value)
        if operator == "$nin":
            return (
                f"({expression} IS NULL OR {expression} NOT IN ({placeholders}))",
                [*prefix, *prefix, *value],
            )
        return f"{expression} IN ({placeholders})", [*prefix, *value]
    if operator == "$ne":
        return (
            f"({expression} IS NULL OR {expression} != ?)",
            [*prefix, *prefix, value],
        )
    comparison = {"$eq": "="}.get(operator)
    if comparison is None:
        raise ValueError(f"unsupported metadata filter operator: {operator}")
    return f"{expression} {comparison} ?", [*prefix, value]


def _metadata_expression(key: str) -> tuple[str, list[object]]:
    columns = {
        "document_id": "d.document_id",
        "release": "d.release",
        "guide_id": "d.guide_id",
        "retrieval_role": "d.retrieval_role",
        "entity_kind": "d.entity_kind",
        "primary_symbol": "d.primary_symbol",
    }
    if key in columns:
        return columns[key], []
    if not key or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in key
    ):
        raise ValueError("metadata filter key must be a lowercase identifier")
    return "json_extract(d.metadata_json, ?)", [f"$.{key}"]


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _read_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _require_document_id(document: Document) -> str:
    if not document.id:
        raise ValueError("document id is required for indexing")
    return document.id
