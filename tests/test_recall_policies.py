from types import SimpleNamespace

import pytest
from tests.test_evidence_blocks import parent, tokenizer
from tests.test_unified_retrieval import Backend, Ranker, make

from abaqus_sim_agent.evaluation.unified_candidate_backend import BoundCandidateBackend
from abaqus_sim_agent.evaluation.unified_retrieval import (
    Applicability,
    CandidateBudget,
    QuestionQuery,
    SymbolQuery,
    UnifiedRetrievalCandidate,
)
from abaqus_sim_agent.retrieval.evidence_blocks import TokenBoundChildren
from abaqus_sim_agent.retrieval.types import Document


def test_question_only_uses_explicit_field_without_splitting_question():
    service, backend, ranker, _, _ = make()
    seen = []
    dense = backend.dense
    ranking = ranker.rank
    backend.dense = lambda query, **kwargs: seen.append(query) or dense(query, **kwargs)
    ranker.rank = lambda query, docs: seen.append(query) or ranking(query, docs)
    request = QuestionQuery(
        question="first line\nsecond line",
        observed_context="context " * 300,
        product="Abaqus/Standard",
    )
    assert service.search(request)["status"] == "query_too_long"
    assert not seen
    result = service.search(request, query_text_mode="question_only")
    assert seen == [request.question, request.question]
    assert result["trace"]["context_omitted"]
    assert result["trace"]["request"]["observed_context"] == request.observed_context
    assert result["trace"]["excluded_parents"] == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"query_text_mode": "unknown"},
        {"pool_policy": "unknown"},
        {"pool_policy": "page_lexical"},
        {"pool_policy": "page_lexical", "retrieval_mode": "hybrid"},
    ],
)
def test_invalid_or_unsupported_policy_fails_before_retrieval(kwargs):
    service, backend, _, _, _ = make()
    with pytest.raises(ValueError, match=r"unknown|requires"):
        service.search(QuestionQuery(question="Rule"), **kwargs)
    assert not backend.calls


def test_symbol_route_does_not_use_query_or_page_strategy():
    service, backend, _, _, _ = make()
    result = service.search(
        SymbolQuery(symbol="AT", symbol_kind="output_variable"),
        query_text_mode="question_only",
        pool_policy="page_lexical",
    )
    assert result["status"] == "found"
    assert backend.calls == [("symbol", "AT")]


def test_rerank_only_preserves_retrieval_text_and_uses_question_for_pair_budget():
    service, backend, ranker, _, _ = make()
    retrieval_queries, ranking_queries, pair_queries = [], [], []
    dense, ranking, pair_count = backend.dense, ranker.rank, ranker.pair_count
    backend.dense = lambda query, **kwargs: (
        retrieval_queries.append(query) or dense(query, **kwargs)
    )
    ranker.rank = lambda query, docs: (
        ranking_queries.append(query) or ranking(query, docs)
    )
    ranker.pair_count = lambda query, doc: (
        pair_queries.append(query) or pair_count(query, doc)
    )
    request = QuestionQuery(
        question="first line\nsecond line", observed_context="exact warning and context"
    )
    result = service.search(request, query_text_mode="rerank_question_only")
    assert retrieval_queries == [request.question + "\n" + request.observed_context]
    assert ranking_queries == [request.question]
    assert set(pair_queries) == {request.question}
    assert result["trace"]["retrieval_query"] == retrieval_queries[0]
    assert result["trace"]["rerank_query"] == request.question
    assert result["trace"]["context_omitted"] is False
    assert result["trace"]["rerank_context_omitted"] is True


def test_rerank_only_does_not_bypass_retrieval_encoder_budget():
    service, backend, ranker, _, _ = make()
    result = service.search(
        QuestionQuery(question="Rule", observed_context="word " * 300),
        query_text_mode="rerank_question_only",
    )
    assert result["status"] == "query_too_long"
    assert not backend.calls
    assert not ranker.inputs


def test_rerank_only_rejects_empty_question_before_retrieval():
    service, backend, _, _, _ = make()
    with pytest.raises(ValueError, match="empty rerank question"):
        service.search(
            QuestionQuery(question="  ", observed_context="Rule"),
            query_text_mode="rerank_question_only",
        )
    assert not backend.calls


class PageBackend(Backend):
    def __init__(self, docs, local):
        super().__init__(docs)
        self.local = local
        self.page_calls = []
        self.override = None

    def lexical(self, query, *, limit, excluded_parents):
        return [
            d for d in self.docs if d.metadata["parent_id"] not in excluded_parents
        ][:limit]

    def page_lexical(self, query, **kwargs):
        self.page_calls.append((query, kwargs))
        if self.override is not None:
            return self.override
        counts = dict.fromkeys(kwargs["document_ids"], 0)
        result = []
        for doc in self.local:
            page, key = doc.metadata["document_id"], doc.metadata["parent_id"]
            if (
                page in counts
                and counts[page] < kwargs["limit_per_page"]
                and key not in kwargs["excluded_parents"]
                and key not in kwargs["represented_parents"]
            ):
                counts[page] += 1
                result.append(doc)
        return result


def page_service(candidates=8):
    pages = ["a", "a", "b", "b", "c", "c", "a", "b", "a", "a", "b", "b", "c"]
    parents = [
        parent("Rule.", ordinal=i).model_copy(
            update={"source_role": "content", "document_id": page}
        )
        for i, page in enumerate(pages)
    ]
    docs = [TokenBoundChildren(tokenizer()).split(p)[0] for p in parents]
    backend, ranker = PageBackend(docs[:8], docs[8:]), Ranker()
    service = UnifiedRetrievalCandidate(
        backend,
        ranker,
        {p.parent_id: p for p in parents},
        {},
        dict.fromkeys(pages, "a" * 64),
        embedding_count=lambda s: len(s.split()),
        delivery_count=lambda s: len(s.split()),
        delivery_tokenizer="test",
        budget=CandidateBudget(candidates=candidates),
    )
    return service, backend, ranker, docs


def test_page_pool_preserves_budget_base_and_uses_unrepresented_parents():
    service, backend, ranker, docs = page_service()
    result = service.search(
        QuestionQuery(question="Rule"),
        retrieval_mode="hybrid",
        pool_policy="page_lexical",
    )
    selection = result["trace"]["page_selection"]
    assert selection["document_ids"] == ("a", "b")
    assert selection["limit_per_page"] == 2
    assert selection["base_ids"] == [d.id for d in docs[:4]]
    assert selection["local_ids"] == [d.id for d in docs[8:12]]
    assert ranker.inputs == [[d.id for d in (*docs[:4], *docs[8:12])]]
    assert len(result["trace"]["pool_ids"]) == 8
    assert backend.page_calls[0][1]["represented_parents"] == frozenset(
        d.metadata["parent_id"] for d in docs[:4]
    )


def test_sparse_page_results_fill_from_original_pool():
    service, backend, ranker, docs = page_service()
    backend.local = docs[8:9]
    service.search(
        QuestionQuery(question="Rule"),
        retrieval_mode="hybrid",
        pool_policy="page_lexical",
    )
    assert ranker.inputs == [[d.id for d in (*docs[:4], docs[8], *docs[4:7])]]


@pytest.mark.parametrize(
    "violation",
    ["wrong_page", "represented", "duplicate_parent", "quota", "scope", "source"],
)
def test_page_backend_contract_violations_are_fail_closed(violation):
    service, backend, ranker, docs = page_service()
    if violation == "wrong_page":
        backend.override = docs[12:13]
    elif violation == "represented":
        backend.override = docs[:1]
    elif violation == "duplicate_parent":
        backend.override = [docs[8], docs[8]]
    elif violation == "quota":
        backend.override = [docs[6], docs[8], docs[9]]
    elif violation == "scope":
        key = docs[8].metadata["parent_id"]
        service.scopes[key] = Applicability(
            ("Abaqus/Explicit",), "official_page", "Products"
        )
        backend.override = docs[8:9]
    else:
        backend.override = [docs[8].model_copy(deep=True)]
        backend.override[0].metadata["source_sha256"] = "bad"
    with pytest.raises(ValueError, match=r"violated|mismatch|verified|match"):
        service.search(
            QuestionQuery(question="Rule", product="Abaqus/Standard"),
            retrieval_mode="hybrid",
            pool_policy="page_lexical",
        )
    assert not ranker.inputs


def test_one_candidate_has_no_page_expansion_capacity():
    service, backend, ranker, docs = page_service(candidates=1)
    service.search(
        QuestionQuery(question="Rule"),
        retrieval_mode="hybrid",
        pool_policy="page_lexical",
    )
    assert not backend.page_calls
    assert ranker.inputs == [[docs[0].id]]


def test_real_page_adapter_filters_full_bm25_before_quota():
    raw = [
        ("other", "x"),
        ("chosen", "blocked"),
        ("chosen", "seen"),
        ("chosen", "new1"),
        ("chosen", "new1"),
        ("chosen", "new2"),
        ("chosen", "new3"),
    ]
    docs = [
        Document(
            id=str(i),
            page_content="Rule",
            metadata={"document_id": page, "parent_id": key},
        )
        for i, (page, key) in enumerate(raw)
    ]
    requested = []

    def search(_query, *, top_k):
        requested.append(top_k)
        return [SimpleNamespace(document=d) for d in docs[:top_k]]

    backend = BoundCandidateBackend.__new__(BoundCandidateBackend)
    backend.sparse = SimpleNamespace(
        manifest=SimpleNamespace(documents_indexed=len(docs)), search_passages=search
    )
    result = backend.page_lexical(
        "Rule",
        document_ids=("chosen",),
        limit_per_page=2,
        excluded_parents=frozenset({"blocked"}),
        represented_parents=frozenset({"seen"}),
    )
    assert requested == [len(docs)]
    assert [d.id for d in result] == ["3", "5"]
