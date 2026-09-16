import pytest
from pydantic import ValidationError
from tests.test_evidence_blocks import parent, tokenizer

from abaqus_sim_agent.evaluation.cross_encoder import ScoredPassage
from abaqus_sim_agent.evaluation.unified_retrieval import (
    QUERY_ADAPTER,
    Applicability,
    CandidateBudget,
    QuestionQuery,
    SymbolQuery,
    UnifiedRetrievalCandidate,
)
from abaqus_sim_agent.retrieval.evidence_blocks import TokenBoundChildren
from abaqus_sim_agent.retrieval.types import Document


class Backend:
    def __init__(self, docs):
        self.docs = docs
        self.calls = []
        self.card = Document(
            id="AT",
            page_content="All translational accelerations.",
            metadata={
                "primary_symbol": "AT",
                "entity_kind": "output_variable",
                "document_id": "page",
                "source_sha256": "a" * 64,
                "release": "2026 FD02",
            },
        )

    def dense(self, query, *, limit, excluded_parents):
        self.calls.append(("dense", excluded_parents))
        return [
            d for d in self.docs if d.metadata["parent_id"] not in excluded_parents
        ][:limit]

    def lexical(self, query, *, limit, excluded_parents):
        self.calls.append(("lexical", excluded_parents))
        return [
            d
            for d in reversed(self.docs)
            if d.metadata["parent_id"] not in excluded_parents
        ][:limit]

    def symbols(self, symbol, symbol_kind):
        self.calls.append(("symbol", symbol))
        return [self.card] if symbol == "AT" else []


class Ranker:
    def __init__(self):
        self.inputs = []
        self.too_long = False

    def pair_count(self, query, document):
        return 513 if self.too_long else 20

    def rank(self, query, documents):
        self.inputs.append([d.id for d in documents])
        return [
            ScoredPassage(d, float(len(documents) - i), 20, False)
            for i, d in enumerate(documents)
        ], 0.01


def make(*, budget=None):
    blocks = [
        parent("Rule. " * 50 + "Final exception.", ordinal=i).model_copy(
            update={"source_role": "content"}
        )
        for i in range(3)
    ]
    docs = [
        d
        for p in blocks
        for d in TokenBoundChildren(tokenizer(), max_tokens=24).split(p)
    ]
    backend, ranker = Backend(docs), Ranker()
    scopes = {
        blocks[0].parent_id: Applicability(
            ("Abaqus/Explicit",), "official_page", "Products"
        ),
        blocks[1].parent_id: Applicability(
            ("Abaqus/Standard",), "verified_section", "reviewed source section"
        ),
    }
    service = UnifiedRetrievalCandidate(
        backend,
        ranker,
        {p.parent_id: p for p in blocks},
        scopes,
        {"page": "a" * 64},
        embedding_count=lambda text: len(text.split()) + 2,
        delivery_count=lambda text: len(text.split()),
        delivery_tokenizer="test-whitespace",
        budget=budget or CandidateBudget(),
    )
    return service, backend, ranker, blocks, docs


def test_uppercase_warning_never_activates_exact_route():
    service, backend, _, _, _ = make()
    result = service.search(
        QuestionQuery(question="OUTPUT AT EXACT PREDEFINED TIME POINTS")
    )
    assert result["status"] == "found"
    assert [c[0] for c in backend.calls] == ["dense"]


def test_explicit_real_at_query_still_works_without_dense_or_rerank():
    service, backend, ranker, _, _ = make()
    result = service.search(SymbolQuery(symbol="AT", symbol_kind="output_variable"))
    assert result["evidence"][0]["id"] == "AT"
    assert [c[0] for c in backend.calls] == ["symbol"]
    assert not ranker.inputs


def test_same_eligibility_before_dense_and_lexical_limit_unknown_retained():
    service, backend, _, blocks, _ = make()
    result = service.search(
        QuestionQuery(question="Rule", product="Abaqus/Standard"),
        retrieval_mode="hybrid",
    )
    assert (
        backend.calls[0][1] == backend.calls[1][1] == frozenset({blocks[0].parent_id})
    )
    assert {e["id"] for e in result["evidence"]} == {p.parent_id for p in blocks[1:]}
    assert any(e["applicability"]["origin"] == "unknown" for e in result["evidence"])


def test_rerank_gets_siblings_before_parent_dedup_and_same_page_units_survive():
    service, _, ranker, blocks, docs = make()
    result = service.search(QuestionQuery(question="Rule"))
    assert len(ranker.inputs[0]) == len(docs)
    assert len(result["evidence"]) == 3
    assert [e["body"] for e in result["evidence"]] == [p.body for p in blocks]


def test_delivery_budget_is_independent_of_embedding_limit():
    service, _, _, blocks, _ = make(
        budget=CandidateBudget(embedding_tokens=4, unit_body_tokens=1200)
    )
    result = service.search(QuestionQuery(question="Rule"))
    assert len(blocks[0].body.split()) > 4
    assert len(result["evidence"]) == 3


def test_500_token_parent_is_not_rejected_by_child_encoder_budget():
    block = parent("word " * 500).model_copy(update={"source_role": "content"})
    children = TokenBoundChildren(tokenizer(), max_tokens=24).split(block)
    backend = Backend(children)
    service = UnifiedRetrievalCandidate(
        backend,
        Ranker(),
        {block.parent_id: block},
        {},
        {"page": "a" * 64},
        embedding_count=lambda value: len(value.split()) + 2,
        delivery_count=lambda value: len(value.split()),
        delivery_tokenizer="test-whitespace",
        budget=CandidateBudget(),
    )
    result = service.search(QuestionQuery(question="Explain the rule"))
    assert result["evidence"][0]["body"] == block.body
    assert len(result["evidence"][0]["body"].split()) == 500


def test_source_release_constraint_does_not_silently_fallback():
    service, backend, _, _, _ = make()
    result = service.search(QuestionQuery(question="Rule", source_release="2024"))
    assert result["status"] == "not_found"
    assert not backend.calls


def test_long_query_or_pair_rejected_before_truncating_inference():
    service, backend, ranker, _, _ = make()
    assert (
        service.search(QuestionQuery(question="word " * 300))["status"]
        == "query_too_long"
    )
    assert not backend.calls
    ranker.too_long = True
    assert (
        service.search(QuestionQuery(question="Rule"))["status"]
        == "reranker_input_too_long"
    )
    assert not ranker.inputs


def test_budget_rejections_never_clip_and_metadata_counts():
    service, _, _, _, _ = make(budget=CandidateBudget(context_tokens=10))
    result = service.search(QuestionQuery(question="Rule"))
    assert not result["evidence"]
    assert all(
        r["reason"] == "serialized_context_budget" for r in result["trace"]["rejected"]
    )
    service.budget = CandidateBudget(unit_body_tokens=2)
    result = service.search(QuestionQuery(question="Rule"))
    assert all(r["reason"] == "unit_body_budget" for r in result["trace"]["rejected"])


@pytest.mark.parametrize(
    "field", ["source_url", "source_sha256", "section_id", "release", "search_start"]
)
def test_corrupt_mapping_is_fail_closed(field):
    service, backend, _, _, _ = make()
    backend.docs[0] = backend.docs[0].model_copy(deep=True)
    backend.docs[0].metadata[field] = "wrong"
    with pytest.raises(ValueError, match=r"match|offset|verified"):
        service.search(QuestionQuery(question="Rule"))


def test_question_contract_cannot_smuggle_implicit_symbols():
    with pytest.raises(ValidationError, match="Extra inputs"):
        QUERY_ADAPTER.validate_python(
            {"kind": "question", "question": "AT", "symbol": "AT"}
        )


def test_untrusted_product_hint_is_not_used_as_scope():
    service, backend, _, blocks, _ = make()
    for d in backend.docs:
        d.metadata["section_product_mentions"] = ["Abaqus/Explicit"]
    result = service.search(QuestionQuery(question="Rule", product="Abaqus/Standard"))
    assert blocks[2].parent_id in [e["id"] for e in result["evidence"]]
