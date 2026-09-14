from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from langchain_core.documents import Document
from pydantic import BaseModel, ConfigDict, Field

BM25_MANIFEST_NAME = "lexical/manifest.json"
BM25_TOKENIZER_NAME = "abaqus-lexical-v1"
BM25_ALGORITHM_NAME = "bm25-okapi-v1"
BM25_K1 = 1.2
BM25_B = 0.75

_DENSE_MANIFEST_NAME = "manifest.json"
_DATABASE_PREFIX = "bm25-"
_DATABASE_SUFFIX = ".sqlite3"
_HASH_PATTERN = r"^[0-9a-f]{64}$"
_TOKEN_PATTERN = re.compile(r"\*?[^\W_-]+(?:[-_][^\W_-]+)*", re.UNICODE)
_COMPOUND_SEPARATOR_PATTERN = re.compile(r"[-_]+")


class BM25IndexError(RuntimeError):
    pass


class BM25IndexManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["1"] = "1"
    built_at: datetime
    algorithm: Literal["bm25-okapi-v1"] = BM25_ALGORITHM_NAME
    tokenizer: Literal["abaqus-lexical-v1"] = BM25_TOKENIZER_NAME
    k1: float = Field(gt=0)
    b: float = Field(ge=0, le=1)
    collection_name: str = Field(min_length=1)
    release: str = Field(min_length=1)
    dense_manifest_sha256: str = Field(pattern=_HASH_PATTERN)
    semantic_corpus_sha256: str = Field(pattern=_HASH_PATTERN)
    database_filename: str
    database_sha256: str = Field(pattern=_HASH_PATTERN)
    documents_indexed: int = Field(gt=0)
    terms_indexed: int = Field(gt=0)
    postings_indexed: int = Field(gt=0)
    total_document_length: int = Field(gt=0)
    average_document_length: float = Field(gt=0)
    database_size_bytes: int = Field(gt=0)


@dataclass(frozen=True)
class BM25RetrievalHit:
    document: Document
    score: float


class BM25DocumentIndex:
    def __init__(
        self,
        persist_directory: Path,
        dense_manifest_path: Path,
        manifest: BM25IndexManifest,
        database_path: Path,
    ) -> None:
        self.persist_directory = persist_directory
        self.dense_manifest_path = dense_manifest_path
        self.manifest_path = persist_directory / BM25_MANIFEST_NAME
        self.manifest = manifest
        self.database_path = database_path

    @classmethod
    def build(
        cls,
        documents: Iterable[Document],
        persist_directory: str | Path,
        *,
        dense_manifest_path: str | Path | None = None,
        k1: float = BM25_K1,
        b: float = BM25_B,
        collection_name: str | None = None,
        release: str | None = None,
    ) -> tuple[BM25DocumentIndex, BM25IndexManifest]:
        """构建独立 BM25 索引并原子发布内容寻址的数据库指针。

        调用者负责只传入 `retrieval_role=semantic` 的文档。本组件不会替调用者
        猜测或静默过滤语料范围，因而 dense 与 BM25 的语料对齐可由上层明确验收。

        Args:
            documents: 要写入词法索引的文档流，每个文档必须有稳定 ID。
            persist_directory: dense 索引所在目录；词法索引写入其 `lexical` 子目录。
            dense_manifest_path: 用作语料版本锚点的 dense manifest。默认读取
                `persist_directory/manifest.json`。
            k1: BM25 词频饱和参数，必须大于零。
            b: BM25 文档长度归一化参数，取值范围为 0 到 1。
            collection_name: dense manifest 缺失 collection name 时的显式值。
            release: dense manifest 缺失 release 时的显式值。

        Returns:
            已严格重新打开的索引以及本次发布的 manifest。

        Raises:
            ValueError: 参数、文档 ID、元数据或词法语料不合法。
            FileNotFoundError: dense manifest 不存在。
            BM25IndexError: SQLite 构建或发布后的完整性校验失败。
        """
        root = Path(persist_directory).expanduser().resolve()
        dense_path = _resolve_dense_manifest_path(root, dense_manifest_path)
        if not dense_path.is_file():
            raise FileNotFoundError(
                f"dense index manifest does not exist: {dense_path}"
            )
        if k1 <= 0:
            raise ValueError("k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("b must be between 0 and 1")
        if k1 != BM25_K1 or b != BM25_B:
            raise ValueError("only BM25 k1=1.2 and b=0.75 are supported")

        dense_manifest_bytes = dense_path.read_bytes()
        dense_manifest_sha256 = _sha256_bytes(dense_manifest_bytes)
        bound_collection_name, bound_release = _dense_index_identity(
            dense_manifest_bytes,
            collection_name=collection_name,
            release=release,
        )
        lexical_directory = root / Path(BM25_MANIFEST_NAME).parent
        lexical_directory.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".bm25-build-",
            suffix=_DATABASE_SUFFIX,
            dir=lexical_directory,
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            counts = _build_database(temporary_path, documents)
            if dense_path.read_bytes() != dense_manifest_bytes:
                raise BM25IndexError(
                    "dense index manifest changed while building the BM25 index"
                )
            database_sha256 = _sha256_file(temporary_path)
            database_filename = f"{_DATABASE_PREFIX}{database_sha256}{_DATABASE_SUFFIX}"
            database_path = lexical_directory / database_filename
            if database_path.exists():
                if _sha256_file(database_path) != database_sha256:
                    raise BM25IndexError(
                        "content-addressed BM25 database has unexpected content"
                    )
                temporary_path.unlink()
            else:
                os.replace(temporary_path, database_path)

            (
                document_count,
                term_count,
                posting_count,
                total_length,
                semantic_corpus_sha256,
            ) = counts
            manifest = BM25IndexManifest(
                built_at=datetime.now(UTC),
                k1=k1,
                b=b,
                collection_name=bound_collection_name,
                release=bound_release,
                dense_manifest_sha256=dense_manifest_sha256,
                semantic_corpus_sha256=semantic_corpus_sha256,
                database_filename=database_filename,
                database_sha256=database_sha256,
                documents_indexed=document_count,
                terms_indexed=term_count,
                postings_indexed=posting_count,
                total_document_length=total_length,
                average_document_length=total_length / document_count,
                database_size_bytes=database_path.stat().st_size,
            )
            _atomic_write_text(
                root / BM25_MANIFEST_NAME,
                manifest.model_dump_json(indent=2),
            )
            index = cls.open(root, dense_manifest_path=dense_path)
            return index, manifest
        finally:
            temporary_path.unlink(missing_ok=True)

    @classmethod
    def open(
        cls,
        persist_directory: str | Path,
        *,
        dense_manifest_path: str | Path | None = None,
    ) -> BM25DocumentIndex:
        """打开并严格校验 BM25 索引、上游 manifest 与 SQLite 统计量。"""
        root = Path(persist_directory).expanduser().resolve()
        manifest_path = root / BM25_MANIFEST_NAME
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"BM25 index manifest does not exist: {manifest_path}"
            )
        manifest_bytes = manifest_path.read_bytes()
        try:
            manifest = BM25IndexManifest.model_validate_json(manifest_bytes)
        except ValueError as exc:
            raise BM25IndexError(
                f"BM25 index manifest is invalid: {manifest_path}"
            ) from exc
        _validate_manifest_contract(manifest)

        dense_path = _resolve_dense_manifest_path(root, dense_manifest_path)
        if not dense_path.is_file():
            raise FileNotFoundError(
                f"dense index manifest does not exist: {dense_path}"
            )
        dense_manifest_bytes = dense_path.read_bytes()
        if _sha256_bytes(dense_manifest_bytes) != manifest.dense_manifest_sha256:
            raise BM25IndexError("dense index manifest hash does not match BM25 index")
        _dense_index_identity(
            dense_manifest_bytes,
            collection_name=manifest.collection_name,
            release=manifest.release,
        )

        database_path = manifest_path.parent / manifest.database_filename
        if not database_path.is_file():
            raise FileNotFoundError(
                f"BM25 content-addressed database does not exist: {database_path}"
            )
        if database_path.stat().st_size != manifest.database_size_bytes:
            raise BM25IndexError("BM25 database size does not match its manifest")
        if _sha256_file(database_path) != manifest.database_sha256:
            raise BM25IndexError("BM25 database hash does not match its manifest")

        _validate_database(database_path, manifest)
        if manifest_path.read_bytes() != manifest_bytes:
            raise BM25IndexError("BM25 index manifest changed while opening the index")
        if dense_path.read_bytes() != dense_manifest_bytes:
            raise BM25IndexError(
                "dense index manifest changed while opening BM25 index"
            )
        if _sha256_file(database_path) != manifest.database_sha256:
            raise BM25IndexError("BM25 database changed while opening the index")
        return cls(root, dense_path, manifest, database_path)

    def search(self, query: str, *, top_k: int = 5) -> list[BM25RetrievalHit]:
        """按 BM25 分数检索，并确保同一父页面最多返回一个文档。"""
        return self._search(query, top_k=top_k, deduplicate_pages=True)

    def search_passages(self, query: str, *, top_k: int = 20) -> list[BM25RetrievalHit]:
        """返回片段级排名，保留同页不同片段供后续选择；不改变search默认行为。"""
        return self._search(query, top_k=top_k, deduplicate_pages=False)

    def _search(
        self, query: str, *, top_k: int, deduplicate_pages: bool
    ) -> list[BM25RetrievalHit]:
        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        query_terms = list(dict.fromkeys(tokenize_abaqus_lexical(query)))
        if not query_terms:
            return []

        statement = """
            SELECT
                terms.term,
                terms.document_frequency,
                postings.term_frequency,
                documents.chunk_id,
                documents.parent_id,
                documents.page_content,
                documents.metadata_json,
                documents.document_length
            FROM terms
            JOIN postings ON postings.term_id = terms.term_id
            JOIN documents ON documents.doc_no = postings.doc_no
            WHERE terms.term = ?
        """
        scores: dict[str, float] = {}
        records: dict[str, tuple[str, str, str]] = {}
        with _read_only_connection(self.database_path) as connection:
            for query_term in query_terms:
                rows = connection.execute(statement, (query_term,))
                for (
                    _term,
                    document_frequency,
                    term_frequency,
                    chunk_id,
                    parent_id,
                    page_content,
                    metadata_json,
                    document_length,
                ) in rows:
                    score = _bm25_term_score(
                        term_frequency=int(term_frequency),
                        document_frequency=int(document_frequency),
                        document_length=int(document_length),
                        document_count=self.manifest.documents_indexed,
                        average_document_length=self.manifest.average_document_length,
                        k1=self.manifest.k1,
                        b=self.manifest.b,
                    )
                    key = str(chunk_id)
                    scores[key] = scores.get(key, 0.0) + score
                    records[key] = (
                        str(parent_id),
                        str(page_content),
                        str(metadata_json),
                    )

        hits: list[BM25RetrievalHit] = []
        seen_parents: set[str] = set()
        for chunk_id, score in sorted(
            scores.items(), key=lambda item: (-item[1], item[0])
        ):
            parent_id, page_content, metadata_json = records[chunk_id]
            if deduplicate_pages and parent_id in seen_parents:
                continue
            metadata = json.loads(metadata_json)
            if not isinstance(metadata, dict):
                raise BM25IndexError(
                    f"BM25 document metadata is not an object: {chunk_id}"
                )
            hits.append(
                BM25RetrievalHit(
                    document=Document(
                        id=chunk_id,
                        page_content=page_content,
                        metadata=metadata,
                    ),
                    score=score,
                )
            )
            seen_parents.add(parent_id)
            if len(hits) == top_k:
                break
        return hits

    def read_manifest(self) -> BM25IndexManifest:
        return self.manifest


def tokenize_abaqus_lexical(text: str) -> list[str]:
    """按稳定的 Abaqus 词法规则生成 BM25 token。"""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(normalized):
        raw_token = match.group(0)
        has_keyword_prefix = raw_token.startswith("*")
        base_token = raw_token[1:] if has_keyword_prefix else raw_token
        if has_keyword_prefix:
            tokens.append(raw_token)
            tokens.append(base_token)
        else:
            tokens.append(base_token)
        if "-" in base_token or "_" in base_token:
            tokens.extend(
                part for part in _COMPOUND_SEPARATOR_PATTERN.split(base_token) if part
            )
    return tokens


def semantic_corpus_sha256(documents: Iterable[Document]) -> str:
    """计算与持久 BM25 manifest 相同的 semantic corpus 内容哈希。"""
    rows: list[tuple[str, str, str, str]] = []
    for document in documents:
        chunk_id = _require_document_id(document)
        rows.append(
            (
                chunk_id,
                str(document.metadata.get("document_id", chunk_id)),
                document.page_content,
                _serialize_metadata(document.metadata, chunk_id),
            )
        )
    return _corpus_sha256(sorted(rows, key=lambda row: row[0]))


def _build_database(
    database_path: Path,
    documents: Iterable[Document],
) -> tuple[int, int, int, int, str]:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(
            """
            CREATE TABLE documents (
                doc_no INTEGER PRIMARY KEY,
                chunk_id TEXT NOT NULL UNIQUE,
                parent_id TEXT NOT NULL,
                page_content TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                document_length INTEGER NOT NULL CHECK (document_length >= 0)
            );

            CREATE TABLE terms (
                term_id INTEGER PRIMARY KEY,
                term TEXT NOT NULL UNIQUE,
                document_frequency INTEGER NOT NULL
                    CHECK (document_frequency > 0)
            );

            CREATE TABLE postings (
                term_id INTEGER NOT NULL REFERENCES terms(term_id),
                doc_no INTEGER NOT NULL REFERENCES documents(doc_no),
                term_frequency INTEGER NOT NULL CHECK (term_frequency > 0),
                PRIMARY KEY (term_id, doc_no)
            ) WITHOUT ROWID;

            CREATE TEMP TABLE raw_postings (
                term TEXT NOT NULL,
                doc_no INTEGER NOT NULL,
                term_frequency INTEGER NOT NULL
            );
            """
        )
        document_count = 0
        total_document_length = 0
        try:
            with connection:
                for document in documents:
                    chunk_id = _require_document_id(document)
                    metadata_json = _serialize_metadata(document.metadata, chunk_id)
                    tokens = tokenize_abaqus_lexical(document.page_content)
                    document_length = len(tokens)
                    parent_id = str(document.metadata.get("document_id", chunk_id))
                    cursor = connection.execute(
                        """
                        INSERT INTO documents (
                            chunk_id,
                            parent_id,
                            page_content,
                            metadata_json,
                            document_length
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            chunk_id,
                            parent_id,
                            document.page_content,
                            metadata_json,
                            document_length,
                        ),
                    )
                    doc_no = cursor.lastrowid
                    if doc_no is None:
                        raise BM25IndexError("SQLite did not assign a document number")
                    connection.executemany(
                        """
                        INSERT INTO raw_postings (
                            term,
                            doc_no,
                            term_frequency
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            (term, doc_no, frequency)
                            for term, frequency in Counter(tokens).items()
                        ),
                    )
                    document_count += 1
                    total_document_length += document_length

                if document_count == 0:
                    raise ValueError("documents must not be empty")
                if total_document_length == 0:
                    raise ValueError("documents produced no lexical tokens")
                connection.execute(
                    """
                    INSERT INTO terms (term, document_frequency)
                    SELECT term, COUNT(*)
                    FROM raw_postings
                    GROUP BY term
                    ORDER BY term
                    """
                )
                connection.execute(
                    """
                    INSERT INTO postings (term_id, doc_no, term_frequency)
                    SELECT terms.term_id, raw_postings.doc_no,
                           raw_postings.term_frequency
                    FROM raw_postings
                    JOIN terms ON terms.term = raw_postings.term
                    ORDER BY terms.term_id, raw_postings.doc_no
                    """
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("document IDs must be unique and valid") from exc

        term_count = _scalar_int(connection, "SELECT COUNT(*) FROM terms")
        posting_count = _scalar_int(connection, "SELECT COUNT(*) FROM postings")
        semantic_corpus_sha256 = _semantic_corpus_sha256(connection)
        connection.execute("DROP TABLE raw_postings")
        connection.execute("PRAGMA optimize")
        connection.commit()
    finally:
        connection.close()
    return (
        document_count,
        term_count,
        posting_count,
        total_document_length,
        semantic_corpus_sha256,
    )


def _validate_manifest_contract(manifest: BM25IndexManifest) -> None:
    expected_filename = (
        f"{_DATABASE_PREFIX}{manifest.database_sha256}{_DATABASE_SUFFIX}"
    )
    if manifest.database_filename != expected_filename:
        raise BM25IndexError("BM25 database filename is not bound to its content hash")
    if Path(manifest.database_filename).name != manifest.database_filename:
        raise BM25IndexError("BM25 database filename must not contain a path")
    if manifest.k1 != BM25_K1 or manifest.b != BM25_B:
        raise BM25IndexError("BM25 scoring parameters are not supported")


def _validate_database(
    database_path: Path,
    manifest: BM25IndexManifest,
) -> None:
    try:
        with _read_only_connection(database_path) as connection:
            quick_check = [
                str(row[0]) for row in connection.execute("PRAGMA quick_check")
            ]
            if quick_check != ["ok"]:
                raise BM25IndexError(
                    f"BM25 database quick_check failed: {quick_check!r}"
                )
            counts = (
                _scalar_int(connection, "SELECT COUNT(*) FROM documents"),
                _scalar_int(connection, "SELECT COUNT(*) FROM terms"),
                _scalar_int(connection, "SELECT COUNT(*) FROM postings"),
                _scalar_int(
                    connection,
                    "SELECT COALESCE(SUM(document_length), 0) FROM documents",
                ),
            )
            expected = (
                manifest.documents_indexed,
                manifest.terms_indexed,
                manifest.postings_indexed,
                manifest.total_document_length,
            )
            if counts != expected:
                raise BM25IndexError(
                    f"BM25 database counts do not match manifest: {counts!r}"
                )
            summed_document_frequency = _scalar_int(
                connection,
                "SELECT COALESCE(SUM(document_frequency), 0) FROM terms",
            )
            summed_term_frequency = _scalar_int(
                connection,
                "SELECT COALESCE(SUM(term_frequency), 0) FROM postings",
            )
            foreign_key_errors = list(connection.execute("PRAGMA foreign_key_check"))
            if (
                summed_document_frequency != manifest.postings_indexed
                or summed_term_frequency != manifest.total_document_length
                or foreign_key_errors
            ):
                raise BM25IndexError("BM25 database statistics are internally invalid")
            if _semantic_corpus_sha256(connection) != manifest.semantic_corpus_sha256:
                raise BM25IndexError(
                    "BM25 semantic corpus hash does not match manifest"
                )
            expected_average = (
                manifest.total_document_length / manifest.documents_indexed
            )
            if not math.isclose(
                manifest.average_document_length,
                expected_average,
                rel_tol=1e-12,
                abs_tol=0.0,
            ):
                raise BM25IndexError(
                    "BM25 average document length does not match its counts"
                )
    except sqlite3.DatabaseError as exc:
        raise BM25IndexError(f"BM25 database is invalid: {database_path}") from exc


def _bm25_term_score(
    *,
    term_frequency: int,
    document_frequency: int,
    document_length: int,
    document_count: int,
    average_document_length: float,
    k1: float,
    b: float,
) -> float:
    inverse_document_frequency = math.log(
        1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
    )
    length_normalization = 1 - b + b * document_length / average_document_length
    return inverse_document_frequency * (
        term_frequency * (k1 + 1) / (term_frequency + k1 * length_normalization)
    )


def _read_only_connection(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _require_document_id(document: Document) -> str:
    if document.id is None or not str(document.id):
        raise ValueError("document id is required for BM25 indexing")
    return str(document.id)


def _serialize_metadata(metadata: dict[str, object], document_id: str) -> str:
    try:
        return json.dumps(
            metadata,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"document metadata must be JSON serializable: {document_id}"
        ) from exc


def _resolve_dense_manifest_path(
    persist_directory: Path,
    dense_manifest_path: str | Path | None,
) -> Path:
    if dense_manifest_path is None:
        return persist_directory / _DENSE_MANIFEST_NAME
    return Path(dense_manifest_path).expanduser().resolve()


def _dense_index_identity(
    dense_manifest_bytes: bytes,
    *,
    collection_name: str | None,
    release: str | None,
) -> tuple[str, str]:
    try:
        dense_manifest = json.loads(dense_manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BM25IndexError("dense index manifest is not valid JSON") from exc
    if not isinstance(dense_manifest, dict):
        raise BM25IndexError("dense index manifest must be a JSON object")
    return (
        _bound_identity_value(
            dense_manifest,
            field="collection_name",
            explicit_value=collection_name,
        ),
        _bound_identity_value(
            dense_manifest,
            field="release",
            explicit_value=release,
        ),
    )


def _bound_identity_value(
    dense_manifest: dict[str, object],
    *,
    field: str,
    explicit_value: str | None,
) -> str:
    manifest_value = dense_manifest.get(field)
    if manifest_value is not None and (
        not isinstance(manifest_value, str) or not manifest_value.strip()
    ):
        raise BM25IndexError(f"dense index manifest has invalid {field}")
    normalized_explicit = explicit_value.strip() if explicit_value is not None else None
    if normalized_explicit == "":
        raise ValueError(f"{field} must not be empty")
    if isinstance(manifest_value, str):
        if normalized_explicit is not None and normalized_explicit != manifest_value:
            raise ValueError(f"{field} conflicts with dense index manifest")
        return manifest_value
    if normalized_explicit is None:
        raise ValueError(
            f"{field} is missing from dense index manifest and was not provided"
        )
    return normalized_explicit


def _semantic_corpus_sha256(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        """
        SELECT chunk_id, parent_id, page_content, metadata_json
        FROM documents
        ORDER BY chunk_id
        """
    )
    return _corpus_sha256(rows)


def _corpus_sha256(rows: Iterable[tuple[object, ...]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        for value in row:
            encoded = str(value).encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return digest.hexdigest()


def _scalar_int(connection: sqlite3.Connection, statement: str) -> int:
    row = connection.execute(statement).fetchone()
    if row is None:
        raise BM25IndexError(f"SQLite aggregate returned no row: {statement}")
    return int(row[0])


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
