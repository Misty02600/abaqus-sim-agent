from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, TypedDict

from pydantic_ai import Agent, RunContext

from abaqus_sim_agent.coding.contracts import (
    AbaqusDocEvidence,
    AbaqusDocEvidenceBundle,
    AbaqusDocQuestion,
    ConceptIndexBinding,
    ConceptLookupContext,
    ConceptLookupHitTrace,
    ConceptLookupTrace,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.document_evidence import (
    DOCUMENT_DELIVERY_NOTICE,
    DOCUMENT_DELIVERY_VERSION,
    DocumentExcerpt,
    render_document_bodies,
)
from abaqus_sim_agent.retrieval import (
    AbaqusDocumentIndex,
    IndexManifest,
    RetrievalHit,
)

_WHITESPACE = re.compile(r"\s+")
DOC_QUERY_FORMULATION_VERSION = "question-context-metadata-symbols-v1"
_NOTICE = (
    "These are candidate documentation passages, not a verified answer. "
    "observed_context is supplied by the Agent and is not independently verified. "
    "Each passage retains its source release; 2026 documentation does not guarantee "
    "Abaqus 2024 API behavior. page_products describes page-level scope; "
    "section_product_mentions only reports names in a heading, not verified applicability. "
    "Never invent or override missing user physical inputs. " + DOCUMENT_DELIVERY_NOTICE
)

ConceptRetrievalMode = Literal["off", "tool"]


class _DocEvidenceContext(TypedDict):
    question: str
    observed_context: str | None
    query: str
    notice: str


__all__ = [
    "DOC_QUERY_FORMULATION_VERSION",
    "ConceptRetrievalMode",
    "concept_index_binding",
    "doc_question_key",
    "open_concept_index",
    "record_concept_lookup",
    "register_document_lookup_tool",
]


def register_document_lookup_tool[AgentOutputT](
    agent: Agent[CodingAgentDependencies, AgentOutputT],
) -> None:
    @agent.tool(sequential=True)
    async def search_abaqus_docs(
        ctx: RunContext[CodingAgentDependencies],
        request: AbaqusDocQuestion,
    ) -> AbaqusDocEvidenceBundle:
        """按需查询 Abaqus 建模、适用条件和领域诊断的官方文档。

        Args:
            request: 要确认的技术问题与相关上下文；保留精确符号和报错原文。

        Returns:
            最多四条带实际版本和出处的候选原文；found 不表示问题已被回答。

        Note:
            当已有任务信息、相关代码或运行证据不足以支持下一步领域判断时使用。
            可在建模、修改、运行前后查询；信息充分时无需查询。精确方法签名优先
            查询 API Catalog；本地名称和文件问题应检查工作区，缺失的用户物理
            输入应澄清。本索引目前主要覆盖建模和分析手册，尚未完整覆盖脚本指南。
        """
        trace_context = ctx.deps.concept_lookup_context(
            tool_call_id=ctx.tool_call_id,
        )
        query = "\n".join(
            value for value in (request.question, request.observed_context) if value
        )
        dedupe_key = doc_question_key(request, ctx.deps.current_task_revision)
        key = (dedupe_key, (), ())
        exact_terms: tuple[str, ...] = ()
        products: tuple[str, ...] = ()
        counted = False
        values: _DocEvidenceContext = {
            "question": request.question,
            "observed_context": request.observed_context,
            "query": query,
            "notice": _NOTICE,
        }
        if key in ctx.deps.concept_lookup_queries:
            payload = AbaqusDocEvidenceBundle(
                status="duplicate",
                error="This question and context were already searched in this task revision.",
                **values,
            )
        elif exhausted := ctx.deps.reserve_concept_lookup():
            payload = AbaqusDocEvidenceBundle(
                status="unavailable", error=exhausted, **values
            )
        else:
            counted = True
            ctx.deps.register_concept_lookup_query(key)
            assert ctx.deps.concept_index is not None
            try:
                exact_terms, products = await asyncio.to_thread(
                    _document_query_filters, ctx.deps.concept_index, query
                )
                hits = await asyncio.to_thread(
                    ctx.deps.concept_index.search,
                    query,
                    exact_terms=exact_terms,
                    products=products,
                    top_k=4,
                )
                evidence = _evidence_payloads(hits)
                payload = AbaqusDocEvidenceBundle(
                    status="found" if evidence else "not_found",
                    evidence=evidence,
                    **values,
                )
            except Exception as exc:
                payload = AbaqusDocEvidenceBundle(
                    status="error",
                    error="Document retrieval failed; existing task evidence is unchanged.",
                    error_type=type(exc).__name__,
                    **values,
                )
        record_concept_lookup(
            ctx.deps.request.state_dir,
            query=query,
            exact_terms=exact_terms,
            products=products,
            payload=payload.model_dump(mode="json"),
            counted=counted,
            context=trace_context,
            request=request,
            dedupe_key=dedupe_key,
            index=ctx.deps.request.concept_index,
        )
        return payload


def doc_question_key(request: AbaqusDocQuestion, task_revision: int) -> str:
    content = json.dumps(
        [
            task_revision,
            _normalized_text(request.question).casefold(),
            _normalized_text(request.observed_context or "").casefold(),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _document_query_filters(
    index: AbaqusDocumentIndex,
    query: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """从已有 metadata 识别输入中明确出现的关键字、单元、输出符号和产品。

    Note:
        排除 keyword_parameter，避免把 SECTION、POISSON 等普通词当作精确
        符号。纯字母符号要求输入使用大写，含数字的符号和星号关键字忽略大小写。
        产品过滤只作用于原检索后端的 exact 分支，不推断未给出的产品或物理条件。
    """
    result = index.vector_store.get(
        where={"retrieval_role": "exact"},
        include=["metadatas"],
    )
    symbols: set[str] = set()
    products: set[str] = set()
    for metadata in result.get("metadatas") or ():
        if metadata is None:
            continue
        if metadata.get("entity_kind") not in {"keyword", "element", "output_variable"}:
            continue
        for field, target in (("symbols", symbols), ("products", products)):
            entries = metadata.get(field)
            if isinstance(entries, list):
                target.update(value for value in entries if isinstance(value, str))

    def mentioned(
        values: set[str], *, explicit_symbols: bool = False
    ) -> tuple[str, ...]:
        matches: list[tuple[int, int, str]] = []
        normalized_query = _normalized_text(query)
        for value in values:
            pattern = rf"(?<![A-Za-z0-9_*]){re.escape(value)}(?![A-Za-z0-9_])"
            match = re.search(pattern, normalized_query, flags=re.IGNORECASE)
            if match is not None:
                if (
                    explicit_symbols
                    and not value.startswith("*")
                    and not any(character.isdigit() for character in value)
                    and not match.group().isupper()
                ):
                    continue
                matches.append((match.start(), -len(value), value))
        return tuple(value for _position, _length, value in sorted(matches))

    return mentioned(symbols, explicit_symbols=True), mentioned(products)


def open_concept_index(
    directory: Path,
    *,
    forbidden_root: Path,
) -> AbaqusDocumentIndex:
    resolved = directory.resolve(strict=True)
    root = forbidden_root.resolve()
    if resolved == root or resolved.is_relative_to(root):
        raise ValueError("Concept index directory must be outside the Agent workspace")
    manifest = IndexManifest.model_validate_json(
        (resolved / "manifest.json").read_text(encoding="utf-8")
    )
    return AbaqusDocumentIndex(
        resolved,
        collection_name=manifest.collection_name,
    )


def concept_index_binding(index: AbaqusDocumentIndex) -> ConceptIndexBinding:
    manifest = index.read_manifest()
    from abaqus_sim_agent.retrieval.reproduction import (
        dense_vector_storage_snapshot,
    )

    vector = dense_vector_storage_snapshot(
        index.persist_directory,
        collection_name=manifest.collection_name,
    )
    if index.bm25_manifest_path.is_file():
        bm25 = index.read_bm25_manifest()
        bm25_manifest_sha256 = _sha256_file(index.bm25_manifest_path)
        bm25_database_sha256 = bm25.database_sha256
        semantic_corpus = bm25.semantic_corpus_sha256
    else:
        bm25_manifest_sha256 = None
        bm25_database_sha256 = None
        semantic_corpus = None
    return ConceptIndexBinding(
        schema_version=manifest.schema_version,
        release=manifest.release,
        collection_name=manifest.collection_name,
        document_count=manifest.documents_indexed,
        dense_manifest_sha256=_sha256_file(index.manifest_path),
        dense_vector_sha256=vector.sha256,
        bm25_manifest_sha256=bm25_manifest_sha256,
        bm25_database_sha256=bm25_database_sha256,
        semantic_corpus_sha256=semantic_corpus,
    )


def record_concept_lookup(
    state_dir: Path,
    *,
    query: str,
    exact_terms: Sequence[str],
    products: Sequence[str],
    payload: dict[str, object],
    counted: bool,
    context: ConceptLookupContext | None = None,
    request: AbaqusDocQuestion,
    dedupe_key: str,
    index: ConceptIndexBinding | None,
) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    trace = ConceptLookupTrace(
        tool_name="search_abaqus_docs",
        question=request.question,
        observed_context=request.observed_context,
        formulation_version=DOC_QUERY_FORMULATION_VERSION,
        dedupe_key=dedupe_key,
        index=index,
        query=query,
        exact_terms=tuple(exact_terms),
        products=tuple(products),
        status=str(payload.get("status", "unknown")),
        document_ids=_document_ids(payload),
        ranked_hits=_ranked_hits(payload),
        counted=counted,
        payload_bytes=len(encoded),
        context=context or ConceptLookupContext(),
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "concept_lookups.jsonl").open(
        "a", encoding="utf-8", newline="\n"
    ) as stream:
        stream.write(trace.model_dump_json())
        stream.write("\n")


def _evidence_payloads(hits: Sequence[RetrievalHit]) -> tuple[AbaqusDocEvidence, ...]:
    excerpts = render_document_bodies([hit.document for hit in hits])
    return tuple(
        _evidence_payload(hit, excerpt=excerpt)
        for hit, excerpt in zip(hits, excerpts, strict=True)
    )


def _evidence_payload(
    hit: RetrievalHit, *, excerpt: DocumentExcerpt | None = None
) -> AbaqusDocEvidence:
    match_type = hit.match_type
    if match_type not in ("exact", "semantic"):
        raise ValueError(
            "document tool requires the exact-symbol-augmented Dense profile"
        )
    document = hit.document
    if excerpt is None:
        excerpt = render_document_bodies([document])[0]
    metadata = document.metadata
    document_id = str(metadata.get("document_id") or document.id or "unknown")
    source = str(metadata.get("source_url") or metadata.get("source_path") or "")
    return AbaqusDocEvidence(
        document_id=document_id,
        section_id=str(metadata.get("section_id") or document_id),
        title=str(metadata.get("title") or document_id),
        section=str(metadata.get("section_title") or metadata.get("section_id") or ""),
        excerpt=excerpt.text,
        original_characters=excerpt.original_characters,
        truncated=excerpt.truncated,
        truncation_reason="body_budget" if excerpt.truncated else None,
        delivery_version=DOCUMENT_DELIVERY_VERSION,
        source=source,
        release=str(metadata.get("release") or "unknown"),
        match=match_type,
        heading_path=tuple(metadata.get("heading_path", ())),
        page_products=tuple(metadata.get("products", ())),
        page_products_source=metadata.get("products_source", "unrecorded"),
        section_product_mentions=tuple(metadata.get("section_product_mentions", ())),
        section_product_heading=metadata.get("section_product_heading"),
    )


def _document_ids(payload: dict[str, object]) -> tuple[str, ...]:
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        return ()
    ids: list[str] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        document_id = item.get("document_id")
        if isinstance(document_id, str):
            ids.append(document_id)
    return tuple(ids)


def _ranked_hits(payload: dict[str, object]) -> tuple[ConceptLookupHitTrace, ...]:
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        return ()
    hits: list[ConceptLookupHitTrace] = []
    for rank, item in enumerate(evidence, start=1):
        if not isinstance(item, dict):
            continue
        document_id = item.get("document_id")
        match = item.get("match")
        if not isinstance(document_id, str) or match not in {
            "exact",
            "semantic",
            "bm25",
            "hybrid",
        }:
            continue
        hits.append(
            ConceptLookupHitTrace(
                rank=rank,
                document_id=document_id,
                match=match,
                section_id=(
                    str(item["section_id"]) if item.get("section_id") else None
                ),
            )
        )
    return tuple(hits)


def _normalized_text(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
