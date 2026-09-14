"""显式离线候选流程；类型化查询、共同范围、片段重排与独立交付预算。"""

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, runtime_checkable

from langchain_core.documents import Document
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from abaqus_sim_agent.retrieval.evidence_blocks import (
    EvidenceBlock,
    expand_ranked_children,
    scoring_document,
)

from .cross_encoder import ScoredPassage
from .passage_selection import fuse_passage_candidates

PROFILE = "explicit-scoped-parent-retrieval-v1"
RECALL_PROFILE = "explicit-scoped-recall-policies-v1"
Product = Literal["Abaqus/Standard", "Abaqus/Explicit"]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class QuestionQuery(_Contract):
    kind: Literal["question"] = "question"
    question: str = Field(min_length=1, max_length=20000)
    observed_context: str = Field(default="", max_length=20000)
    product: Product | None = None
    source_release: str | None = None


class SymbolQuery(_Contract):
    kind: Literal["symbol"] = "symbol"
    symbol: str = Field(min_length=1, max_length=80)
    symbol_kind: Literal["keyword", "element", "output_variable"]
    product: Product | None = None
    source_release: str | None = None


Query = Annotated[QuestionQuery | SymbolQuery, Field(discriminator="kind")]
QUERY_ADAPTER = TypeAdapter(Query)


class CandidateBudget(_Contract):
    candidates: int = Field(default=64, ge=1, le=128)
    output_units: int = Field(default=4, ge=1, le=8)
    embedding_tokens: int = Field(default=256, ge=4, le=256)
    reranker_pair_tokens: int = Field(default=512, ge=4, le=512)
    unit_body_tokens: int = Field(default=1200, ge=1, le=16000)
    context_tokens: int = Field(default=4000, ge=1, le=32000)


@dataclass(frozen=True)
class Applicability:
    products: tuple[str, ...]
    origin: Literal["official_page", "verified_section", "unknown"]
    evidence: str

    def permits(self, product: str | None) -> bool:
        return product is None or self.origin == "unknown" or product in self.products


class CandidateBackend(Protocol):
    def dense(
        self, query: str, *, limit: int, excluded_parents: frozenset[str]
    ) -> list[Document]: ...
    def lexical(
        self, query: str, *, limit: int, excluded_parents: frozenset[str]
    ) -> list[Document]: ...
    def symbols(self, symbol: str, symbol_kind: str) -> list[Document]: ...


@runtime_checkable
class PageLexicalBackend(Protocol):
    def page_lexical(
        self,
        query: str,
        *,
        document_ids: tuple[str, ...],
        limit_per_page: int,
        excluded_parents: frozenset[str],
        represented_parents: frozenset[str],
    ) -> list[Document]: ...


class PassageRanker(Protocol):
    def rank(
        self, query: str, documents: Sequence[Document]
    ) -> tuple[list[ScoredPassage], float]: ...
    def pair_count(self, query: str, document: Document) -> int: ...


class UnifiedRetrievalCandidate:
    def __init__(
        self,
        backend: CandidateBackend,
        ranker: PassageRanker,
        parents: Mapping[str, EvidenceBlock],
        scopes: Mapping[str, Applicability],
        verified_sources: Mapping[str, str],
        *,
        embedding_count: Callable[[str], int],
        delivery_count: Callable[[str], int],
        delivery_tokenizer: str,
        budget: CandidateBudget = CandidateBudget(),
    ) -> None:
        """装配已核对来源的候选流程，不注册产品工具。

        Note:
            scopes以parent_id索引；符号卡以自身record ID索引。没有范围声明时保留
            unknown，不能从任意metadata产品词推断硬过滤。两个token计数器独立注入。
        """
        self.backend, self.ranker = backend, ranker
        self.parents, self.scopes, self.verified_sources = (
            parents,
            scopes,
            verified_sources,
        )
        self.embedding_count, self.delivery_count = embedding_count, delivery_count
        self.delivery_tokenizer, self.budget = delivery_tokenizer, budget

    def _scope(self, key: str) -> Applicability:
        return self.scopes.get(
            key, Applicability((), "unknown", "no verified declaration")
        )

    def _validate_child(self, child: Document) -> None:
        key = child.metadata.get("parent_id")
        if not isinstance(key, str) or key not in self.parents:
            raise ValueError("missing or unknown parent mapping")
        parent = self.parents[key]
        if parent.projection_status != "text" or parent.source_role != "content":
            raise ValueError("parent is not verified searchable content")
        if any(
            type(child.metadata.get(k)) is not int
            for k in ("search_start", "search_end")
        ):
            raise ValueError("non-integer source offset")
        scoring_document(child, self.parents)
        for k, v in {
            "source_url": parent.source,
            "source_xpath": parent.xpath,
            "section_id": parent.anchor,
            "offset_scope": "retrieval_projection_characters",
        }.items():
            if child.metadata.get(k) != v:
                raise ValueError("child scope mapping mismatch")
        if self.verified_sources.get(parent.document_id) != parent.source_sha256:
            raise ValueError("source not verified")

    def search(
        self,
        request: QuestionQuery | SymbolQuery,
        *,
        retrieval_mode: Literal["dense", "hybrid"] = "dense",
        query_text_mode: Literal[
            "question_context", "question_only", "rerank_question_only"
        ] = "question_context",
        pool_policy: Literal["flat", "page_lexical"] = "flat",
    ) -> dict:
        """执行显式路由→共同范围候选→片段重排→父单元归并→预算交付。

        Note:
            question路径绝不自动扫描大写符号。hybrid仅通过参数开启。来源/排序器
            失败直接报错，不自动改用其他检索模式；unknown适用性保留在结果中。
            超长单元/剩余预算不足会跳过并记录原因，不截断；最多交付output_units。
            question_only显式省略observed_context的向量/词法/重排输入，但保留原请求
            和显式适用范围；rerank_question_only只省略重排输入中的context，召回文本
            不变。page_lexical只支持hybrid：保留半数全局候选，再从前两页
            各补最多16个尚未表示的父单元片段；不合并正文，不含领域词或gold规则。
        """
        if retrieval_mode not in {"dense", "hybrid"}:
            raise ValueError("unknown retrieval mode")
        if query_text_mode not in {
            "question_context",
            "question_only",
            "rerank_question_only",
        }:
            raise ValueError("unknown query text mode")
        if pool_policy not in {"flat", "page_lexical"}:
            raise ValueError("unknown candidate pool policy")
        if pool_policy == "page_lexical" and isinstance(request, QuestionQuery):
            if retrieval_mode != "hybrid" or not isinstance(
                self.backend, PageLexicalBackend
            ):
                raise ValueError(
                    "page_lexical requires hybrid and a page-capable backend"
                )
        started = time.perf_counter()
        trace: dict = {
            "profile": PROFILE
            if query_text_mode == "question_context" and pool_policy == "flat"
            else RECALL_PROFILE,
            "request": request.model_dump(mode="json"),
            "mode": retrieval_mode,
            "query_text_mode": query_text_mode,
            "pool_policy": pool_policy,
            "excluded_parents": 0,
            "dense_ids": [],
            "lexical_ids": [],
            "ranking": [],
            "rejected": [],
            "latency_seconds": {},
        }
        candidates: list[Document] = []
        if isinstance(request, SymbolQuery):
            # Explicit symbol values only; equality is checked again at this boundary.
            for doc in self.backend.symbols(request.symbol, request.symbol_kind):
                if (
                    doc.metadata.get("primary_symbol") != request.symbol
                    or doc.metadata.get("entity_kind") != request.symbol_kind
                ):
                    raise ValueError("symbol backend returned a nonexact match")
                if self.verified_sources.get(
                    doc.metadata["document_id"]
                ) != doc.metadata.get("source_sha256"):
                    raise ValueError("symbol source not verified")
                if (
                    request.source_release is None
                    or doc.metadata.get("release") == request.source_release
                ) and self._scope(str(doc.id)).permits(request.product):
                    candidates.append(doc)
            candidates.sort(key=lambda d: d.id or "")
            if any(not d.id for d in candidates) or len(
                {d.id for d in candidates}
            ) != len(candidates):
                raise ValueError("symbol backend returned invalid or duplicate IDs")
            trace["symbol_match_count"] = len(candidates)
        else:
            query = "\n".join(
                s
                for s in (
                    request.question,
                    request.observed_context
                    if query_text_mode != "question_only"
                    else "",
                )
                if s
            ).strip()
            if not query:
                raise ValueError("empty question")
            trace["retrieval_query"] = query
            trace["context_omitted"] = query_text_mode == "question_only" and bool(
                request.observed_context
            )
            rerank_query = (
                request.question.strip()
                if query_text_mode == "rerank_question_only"
                else query
            )
            if not rerank_query:
                raise ValueError("empty rerank question")
            trace["rerank_query"] = rerank_query
            trace["rerank_context_omitted"] = (
                query_text_mode != "question_context" and bool(request.observed_context)
            )
            if self.embedding_count(query) > self.budget.embedding_tokens:
                return {"status": "query_too_long", "evidence": [], "trace": trace}
            excluded = frozenset(
                k
                for k, p in self.parents.items()
                if not self._scope(k).permits(request.product)
                or (
                    request.source_release is not None
                    and p.release != request.source_release
                )
            )
            trace["excluded_parents"] = len(excluded)
            if len(excluded) == len(self.parents):
                return {"status": "not_found", "evidence": [], "trace": trace}
            t = time.perf_counter()
            dense = self.backend.dense(
                query, limit=self.budget.candidates, excluded_parents=excluded
            )
            trace["latency_seconds"]["dense"] = time.perf_counter() - t
            lexical = []
            if retrieval_mode == "hybrid":
                t = time.perf_counter()
                lexical = self.backend.lexical(
                    query, limit=self.budget.candidates, excluded_parents=excluded
                )
                trace["latency_seconds"]["lexical"] = time.perf_counter() - t
            for source in (dense, lexical):
                if len(source) > self.budget.candidates or len(
                    {d.id for d in source}
                ) != len(source):
                    raise ValueError("candidate backend violated bound or uniqueness")
                for doc in source:
                    self._validate_child(doc)
                    if doc.metadata["parent_id"] in excluded:
                        raise ValueError(
                            "candidate backend violated common eligibility"
                        )
            trace["dense_ids"], trace["lexical_ids"] = (
                [d.id for d in dense],
                [d.id for d in lexical],
            )
            pool = (
                dense
                if retrieval_mode == "dense"
                else [
                    h.document
                    for h in fuse_passage_candidates(
                        dense, lexical, top_k=self.budget.candidates
                    )
                ]
            )
            trace["flat_pool_ids"] = [d.id for d in pool]
            if pool_policy == "page_lexical":
                t = time.perf_counter()
                pool, trace["page_selection"] = self._page_pool(query, pool, excluded)
                trace["latency_seconds"]["page_lexical"] = time.perf_counter() - t
            trace["pool_ids"] = [d.id for d in pool]
            raw = {d.id: d for d in pool}
            scoring = [scoring_document(d, self.parents) for d in pool]
            if any(
                self.ranker.pair_count(rerank_query, d)
                > self.budget.reranker_pair_tokens
                for d in scoring
            ):
                return {
                    "status": "reranker_input_too_long",
                    "evidence": [],
                    "trace": trace,
                }
            ranked, seconds = self.ranker.rank(rerank_query, scoring)
            if (
                len(ranked) != len(pool)
                or {r.document.id for r in ranked} != set(raw)
                or any(r.truncated for r in ranked)
            ):
                raise ValueError("reranker contract mismatch or truncation")
            trace["latency_seconds"]["rerank"] = seconds
            trace["ranking"] = [
                {"id": r.document.id, "score": r.score, "pair_tokens": r.pair_tokens}
                for r in ranked
            ]
            candidates = [raw[r.document.id] for r in ranked]
        evidence: list[dict] = []
        seen: set[str] = set()
        for child in candidates:
            if len(evidence) == self.budget.output_units:
                break
            if isinstance(request, SymbolQuery):
                doc = child
                key = str(doc.id)
                body_scope = "indexed_symbol_card"
            else:
                key = child.metadata["parent_id"]
                if key in seen:
                    continue
                seen.add(key)
                doc = expand_ranked_children([child], self.parents, top_k=1)[0]
                body_scope = "complete_stored_evidence_unit"
            scope = self._scope(key)
            item = {
                "id": key,
                "document_id": doc.metadata["document_id"],
                "title": doc.metadata.get("title", ""),
                "section": doc.metadata.get("section_title", ""),
                "source": doc.metadata.get("source_url", ""),
                "source_sha256": doc.metadata.get("source_sha256", ""),
                "release": doc.metadata.get("release", ""),
                "applicability": {
                    "products": list(scope.products),
                    "origin": scope.origin,
                    "evidence": scope.evidence,
                },
                "body_scope": body_scope,
                "body": doc.page_content,
            }
            if self.delivery_count(item["body"]) > self.budget.unit_body_tokens:
                trace["rejected"].append({"id": key, "reason": "unit_body_budget"})
                continue
            if (
                self.delivery_count(
                    json.dumps(
                        [*evidence, item], ensure_ascii=False, separators=(",", ":")
                    )
                )
                > self.budget.context_tokens
            ):
                trace["rejected"].append(
                    {"id": key, "reason": "serialized_context_budget"}
                )
                continue
            evidence.append(item)
        payload = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
        trace["latency_seconds"]["total"] = time.perf_counter() - started
        return {
            "status": "found" if evidence else "not_found",
            "evidence": evidence,
            "context_tokens": self.delivery_count(payload),
            "delivery_tokenizer": self.delivery_tokenizer,
            "budget_scope": "serialized evidence list incl metadata, excluding host-only trace/envelope; not provider billing",
            "trace": trace,
        }

    def _page_pool(
        self,
        query: str,
        flat: list[Document],
        excluded: frozenset[str],
    ) -> tuple[list[Document], dict]:
        if not isinstance(self.backend, PageLexicalBackend):
            raise ValueError("backend does not support page lexical retrieval")
        base = flat[: max(1, self.budget.candidates // 2)]
        pages = tuple(
            dict.fromkeys(
                self.parents[d.metadata["parent_id"]].document_id for d in flat
            )
        )[:2]
        remaining = self.budget.candidates - len(base)
        quota = min(16, remaining // len(pages)) if pages else 0
        represented = frozenset(d.metadata["parent_id"] for d in base)
        local = (
            self.backend.page_lexical(
                query,
                document_ids=pages,
                limit_per_page=quota,
                excluded_parents=excluded,
                represented_parents=represented,
            )
            if quota
            else []
        )
        counts: dict[str, int] = {}
        parents: set[str] = set()
        for doc in local:
            self._validate_child(doc)
            parent = self.parents[doc.metadata["parent_id"]]
            page = parent.document_id
            counts[page] = counts.get(page, 0) + 1
            if (
                page not in pages
                or counts[page] > quota
                or parent.parent_id in excluded
                or parent.parent_id in represented
                or parent.parent_id in parents
            ):
                raise ValueError(
                    "page backend violated eligibility, quota or unique-parent contract"
                )
            parents.add(parent.parent_id)
        selected = list(base)
        ids = {d.id for d in selected}
        for doc in (*local, *flat[len(base) :]):
            if len(selected) >= self.budget.candidates:
                break
            if doc.id not in ids:
                selected.append(doc)
                ids.add(doc.id)
        return selected, {
            "document_ids": pages,
            "base_ids": [d.id for d in base],
            "represented_parents": sorted(represented),
            "limit_per_page": quota,
            "local_ids": [d.id for d in local],
            "fill_ids": [
                d.id for d in selected if d.id not in {x.id for x in (*base, *local)}
            ],
        }
