from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Literal

from abaqus_sim_agent.coding.contracts import (
    DIAGNOSTIC_RETRIEVAL_JOURNAL_NAME,
    DiagnosticRetrievalHitTrace,
    DiagnosticRetrievalIndexTrace,
    DiagnosticRetrievalTrace,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.document_evidence import (
    DOCUMENT_DELIVERY_NOTICE,
    DOCUMENT_DELIVERY_VERSION,
    DocumentExcerpt,
    render_document_bodies,
)
from abaqus_sim_agent.execution import (
    AbaqusDiagnostic,
    AbaqusExecutionResult,
    AbaqusInvocation,
)
from abaqus_sim_agent.retrieval import RetrievalHit

DiagnosticRetrievalMode = Literal["off", "advisory"]

DIAGNOSTIC_QUERY_FORMULATION_VERSION = "goal-identifier-window-v1"
DIAGNOSTIC_RETRIEVER_VERSION = "rrf-top4-fetch20-k60-v1"
DIAGNOSTIC_TOP_K = 4
DIAGNOSTIC_FETCH_K = 20
DIAGNOSTIC_RRF_K = 60

_TOKEN = re.compile(r"\*[A-Za-z][A-Za-z0-9_-]*|[A-Za-z][A-Za-z0-9_-]*")
_DETAIL_PREFIX = re.compile(r"^\s*\*{3}\s*(?:ERROR|WARNING)\b", re.IGNORECASE)
_SUMMARY = re.compile(
    r"(?:^\s*[1-9]\d*\s+(?:FATAL\s+)?ERROR(?:S|\s+MESSAGES?)\b|"
    r"^\s*[1-9]\d*\s+WARNING\s+MESSAGES?\b|"
    r"\b(?:ABORTED|ABORTING|TERMINATED)\b)",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "before",
        "because",
        "current",
        "error",
        "existing",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "made",
        "model",
        "not",
        "of",
        "on",
        "repair",
        "request",
        "requests",
        "since",
        "that",
        "the",
        "this",
        "to",
        "valid",
        "verified",
        "warning",
        "with",
    }
)

__all__ = [
    "DIAGNOSTIC_QUERY_FORMULATION_VERSION",
    "DIAGNOSTIC_RETRIEVER_VERSION",
    "DiagnosticRetrievalMode",
    "formulate_solver_diagnostic_query",
    "maybe_add_solver_diagnostic_advisory",
]


def formulate_solver_diagnostic_query(goal: str, diagnostic: str) -> str:
    """从用户任务与Solver原始诊断机械提取一个有界检索query。

    Args:
        goal: 用户已明确给出的任务上下文；不会添加领域答案。
        diagnostic: Abaqus产生的结构化诊断原文。

    Returns:
        标识符优先、去除常见报错模板词且最多12项的query。

    Raises:
        ValueError: 两个输入都无法产生可检索token。

    Note:
        该规则只保留goal中标识符附近的词和diagnostic中的内容词；它不会
        推断材料参数、自由度编号或其他物理/建模答案。
    """
    goal_tokens = _TOKEN.findall(goal)
    diagnostic_tokens = _TOKEN.findall(diagnostic)
    identifiers = _unique(
        token for token in (*goal_tokens, *diagnostic_tokens) if _is_identifier(token)
    )
    context_terms: list[str] = []
    seen_goal_identifiers: set[str] = set()
    identifier_positions: list[int] = []
    for index, token in enumerate(goal_tokens):
        key = token.casefold()
        if not _is_identifier(token) or key in seen_goal_identifiers:
            continue
        identifier_positions.append(index)
        seen_goal_identifiers.add(key)
    for index in identifier_positions:
        start = max(0, index - 3)
        end = min(len(goal_tokens), index + 5)
        context_terms.extend(
            token
            for token in goal_tokens[start:end]
            if _is_content_token(token) and not _is_identifier(token)
        )
    if not context_terms:
        context_terms.extend(
            token
            for token in goal_tokens
            if _is_content_token(token) and not _is_identifier(token)
        )
    diagnostic_terms = [
        token
        for token in diagnostic_tokens
        if _is_content_token(token) and not _is_identifier(token)
    ]
    terms = _unique((*identifiers, *context_terms, *diagnostic_terms))[:12]
    if not terms:
        raise ValueError("solver diagnostic and task goal contain no query tokens")
    return " ".join(terms)


async def maybe_add_solver_diagnostic_advisory(
    dependencies: CodingAgentDependencies,
    result: AbaqusExecutionResult,
) -> dict[str, object] | None:
    """对一个可用的Solver详细诊断执行一次独立预算的RRF advisory。"""
    diagnostic = _select_diagnostic(result)
    if diagnostic is None:
        return None
    query = formulate_solver_diagnostic_query(
        dependencies.request.goal,
        diagnostic.message,
    )
    diagnostic_sha256 = _sha256(_normalized(diagnostic.message).encode("utf-8"))
    query_sha256 = _sha256(query.encode("utf-8"))
    dedupe_key = f"{dependencies.current_task_revision}:{diagnostic_sha256}"
    execution_index = len(dependencies.executions) - 1
    if dedupe_key in dependencies.diagnostic_retrieval_keys:
        return _record_advisory(
            dependencies,
            diagnostic,
            execution_index=execution_index,
            diagnostic_sha256=diagnostic_sha256,
            query=query,
            query_sha256=query_sha256,
            status="duplicate",
            counted=False,
            hits=(),
            elapsed_ms=0.0,
        )
    exhausted = dependencies.reserve_diagnostic_retrieval()
    if exhausted is not None:
        return _record_advisory(
            dependencies,
            diagnostic,
            execution_index=execution_index,
            diagnostic_sha256=diagnostic_sha256,
            query=query,
            query_sha256=query_sha256,
            status="unavailable",
            counted=False,
            hits=(),
            elapsed_ms=0.0,
            error_type="DiagnosticRetrievalBudgetExhausted",
        )
    dependencies.diagnostic_retrieval_keys.add(dedupe_key)
    index = dependencies.concept_index
    started = perf_counter()
    if index is None:
        status: Literal["found", "not_found", "error"] = "error"
        hits: tuple[RetrievalHit, ...] = ()
        error_type = "DiagnosticIndexUnavailable"
    else:
        try:
            hits = tuple(
                await asyncio.to_thread(
                    index.search_hybrid,
                    query,
                    top_k=DIAGNOSTIC_TOP_K,
                    fetch_k=DIAGNOSTIC_FETCH_K,
                    rrf_k=DIAGNOSTIC_RRF_K,
                )
            )
        except Exception as exc:
            status = "error"
            hits = ()
            error_type = type(exc).__name__
        else:
            status = "found" if hits else "not_found"
            error_type = None
    return _record_advisory(
        dependencies,
        diagnostic,
        execution_index=execution_index,
        diagnostic_sha256=diagnostic_sha256,
        query=query,
        query_sha256=query_sha256,
        status=status,
        counted=True,
        hits=hits,
        elapsed_ms=(perf_counter() - started) * 1000,
        error_type=error_type,
    )


def _select_diagnostic(result: AbaqusExecutionResult) -> AbaqusDiagnostic | None:
    if result.invocation is not AbaqusInvocation.SOLVER_JOB or result.timed_out:
        return None
    candidates = [
        diagnostic
        for diagnostic in result.diagnostics
        if _DETAIL_PREFIX.search(diagnostic.message)
        and _SUMMARY.search(diagnostic.message) is None
    ]
    if not candidates:
        return None
    return min(
        enumerate(candidates),
        key=lambda item: (item[1].level != "error", item[0]),
    )[1]


def _record_advisory(
    dependencies: CodingAgentDependencies,
    diagnostic: AbaqusDiagnostic,
    *,
    execution_index: int,
    diagnostic_sha256: str,
    query: str,
    query_sha256: str,
    status: Literal["found", "not_found", "duplicate", "unavailable", "error"],
    counted: bool,
    hits: tuple[RetrievalHit, ...],
    elapsed_ms: float,
    error_type: str | None = None,
) -> dict[str, object]:
    evidence = _evidence_payloads(hits)
    index_trace = _index_trace(dependencies) if dependencies.concept_index else None
    evidence_id = (
        _sha256(
            json.dumps(
                {
                    "query_sha256": query_sha256,
                    "index": (
                        index_trace.model_dump(mode="json")
                        if index_trace is not None
                        else None
                    ),
                    "evidence": evidence,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        if status in {"found", "not_found"}
        else None
    )
    payload: dict[str, object] = {
        "status": status,
        "formulation_version": DIAGNOSTIC_QUERY_FORMULATION_VERSION,
        "retriever_version": DIAGNOSTIC_RETRIEVER_VERSION,
        "query": query,
        "diagnostic": {
            "level": diagnostic.level,
            "source": diagnostic.source,
            "line": diagnostic.line,
            "sha256": diagnostic_sha256,
        },
        "evidence": evidence,
        "evidence_id": evidence_id,
        "notice": (
            "This is Abaqus 2026 documentation evidence used with an Abaqus 2024 "
            "runtime. It is not user intent, an API compatibility guarantee, or "
            "permission to invent missing physical inputs. page_products is page-level "
            "scope; section_product_mentions is only a heading-text hint. "
            + DOCUMENT_DELIVERY_NOTICE
        ),
    }
    if error_type is not None:
        payload["error_type"] = error_type
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    trace = DiagnosticRetrievalTrace(
        run_id=dependencies.request.run_id,
        recorded_at=datetime.now(UTC),
        turn=dependencies.current_turn or None,
        amendment_sequence=dependencies.current_amendment_sequence,
        task_revision=dependencies.current_task_revision,
        execution_index=execution_index,
        diagnostic_level=diagnostic.level,
        diagnostic_source=diagnostic.source,
        diagnostic_line=diagnostic.line,
        diagnostic_sha256=diagnostic_sha256,
        formulation_version=DIAGNOSTIC_QUERY_FORMULATION_VERSION,
        query=query,
        query_sha256=query_sha256,
        index=index_trace,
        status=status,
        counted=counted,
        ranked_hits=tuple(
            DiagnosticRetrievalHitTrace(
                rank=rank,
                document_id=str(
                    hit.document.metadata.get("document_id")
                    or hit.document.id
                    or "unknown"
                ),
                section_id=str(hit.document.metadata.get("section_id") or "unknown"),
                section_title=str(
                    hit.document.metadata.get("section_title") or "unknown"
                ),
            )
            for rank, hit in enumerate(hits, start=1)
        ),
        evidence_id=evidence_id,
        payload_bytes=len(encoded),
        elapsed_ms=elapsed_ms,
        error_type=error_type,
    )
    try:
        _append_trace(dependencies.request.state_dir, trace)
    except OSError as exc:
        payload["trace_status"] = "error"
        payload["trace_error_type"] = type(exc).__name__
    else:
        payload["trace_status"] = "recorded"
    return payload


def _index_trace(
    dependencies: CodingAgentDependencies,
) -> DiagnosticRetrievalIndexTrace:
    index = dependencies.concept_index
    assert index is not None
    dense = index.read_manifest()
    sparse = index.read_bm25_manifest()
    request_binding = dependencies.request.concept_index
    return DiagnosticRetrievalIndexTrace(
        release=dense.release,
        collection_name=dense.collection_name,
        dense_manifest_sha256=_sha256(index.manifest_path.read_bytes()),
        dense_vector_sha256=(
            request_binding.dense_vector_sha256 if request_binding is not None else None
        ),
        bm25_manifest_sha256=_sha256(index.bm25_manifest_path.read_bytes()),
        bm25_database_sha256=sparse.database_sha256,
        semantic_corpus_sha256=sparse.semantic_corpus_sha256,
    )


def _evidence_payloads(hits: tuple[RetrievalHit, ...]) -> list[dict[str, object]]:
    excerpts = render_document_bodies([hit.document for hit in hits])
    return [
        _evidence_payload(hit, excerpt=excerpt)
        for hit, excerpt in zip(hits, excerpts, strict=True)
    ]


def _evidence_payload(
    hit: RetrievalHit, *, excerpt: DocumentExcerpt | None = None
) -> dict[str, object]:
    document = hit.document
    metadata = document.metadata
    document_id = str(metadata.get("document_id") or document.id or "unknown")
    if excerpt is None:
        excerpt = render_document_bodies([document])[0]
    payload: dict[str, object] = {
        "document_id": document_id,
        "title": str(metadata.get("title") or document_id),
        "section_id": str(metadata.get("section_id") or "unknown"),
        "section": str(metadata.get("section_title") or "unknown"),
        "excerpt": excerpt.text,
        "original_characters": excerpt.original_characters,
        "truncated": excerpt.truncated,
        "truncation_reason": "body_budget" if excerpt.truncated else None,
        "delivery_version": DOCUMENT_DELIVERY_VERSION,
        "match": "hybrid",
        "heading_path": list(metadata.get("heading_path", ())),
        "page_products": list(metadata.get("products", ())),
        "page_products_source": metadata.get("products_source", "unrecorded"),
        "section_product_mentions": list(metadata.get("section_product_mentions", ())),
        "section_product_heading": metadata.get("section_product_heading"),
    }
    if source := metadata.get("source_url") or metadata.get("source_path"):
        payload["source"] = str(source)
    if release := metadata.get("release"):
        payload["release"] = str(release)
    return payload


def _append_trace(state_dir: Path, trace: DiagnosticRetrievalTrace) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / DIAGNOSTIC_RETRIEVAL_JOURNAL_NAME).open(
        "a",
        encoding="utf-8",
        newline="\n",
    ) as stream:
        stream.write(trace.model_dump_json())
        stream.write("\n")


def _is_identifier(token: str) -> bool:
    return token.casefold() not in {"*warning", "*error"} and (
        token.startswith("*")
        or (
            any(character.isalpha() for character in token)
            and any(character.isdigit() for character in token)
        )
    )


def _is_content_token(token: str) -> bool:
    return len(token) >= 3 and token.lstrip("*").casefold() not in _STOPWORDS


def _unique(tokens: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in tokens:
        key = value.casefold()
        if key in seen:
            continue
        unique.append(value)
        seen.add(key)
    return unique


def _normalized(value: str) -> str:
    return " ".join(value.split())


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
