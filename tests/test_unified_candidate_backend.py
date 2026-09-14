from types import SimpleNamespace

import pytest
from langchain_core.documents import Document

from abaqus_sim_agent.evaluation import evidence_candidate as driver
from abaqus_sim_agent.evaluation.unified_candidate_backend import BoundCandidateBackend


def test_dense_applies_scope_predicate_before_topk():
    seen = {}

    def search(query, **kwargs):
        seen.update(kwargs)
        return []

    backend = BoundCandidateBackend.__new__(BoundCandidateBackend)
    backend.vector = SimpleNamespace(similarity_search_with_score=search)
    backend.dense("question", limit=2, excluded_parents=frozenset({"explicit"}))
    assert seen == {
        "k": 2,
        "filter": {
            "$and": [
                {"retrieval_role": "semantic"},
                {"parent_id": {"$nin": ["explicit"]}},
            ]
        },
    }


def test_lexical_does_not_filter_only_a_shallow_topk():
    requested = []
    docs = [
        Document(
            id=str(i),
            page_content="body",
            metadata={"parent_id": "blocked" if i < 4 else "allowed"},
        )
        for i in range(5)
    ]

    def search(query, *, top_k):
        requested.append(top_k)
        return [SimpleNamespace(document=d) for d in docs[:top_k]]

    backend = BoundCandidateBackend.__new__(BoundCandidateBackend)
    backend.sparse = SimpleNamespace(
        manifest=SimpleNamespace(documents_indexed=5), search_passages=search
    )
    result = backend.lexical(
        "question", limit=1, excluded_parents=frozenset({"blocked"})
    )
    assert requested == [5]
    assert [d.id for d in result] == ["4"]


@pytest.mark.parametrize("variant", ["heading", "duplicate", "hash", "product"])
def test_bad_scope_declaration_fails_before_opening_index(
    tmp_path, monkeypatch, variant
):
    source = tmp_path / "page.htm"
    source.write_text(
        '<html><body id="page"><article id="scope"><h2>Output in Abaqus/Explicit</h2><p>Body.</p></article></body></html>',
        encoding="utf-8",
    )
    manifest = {
        "source_root": str(tmp_path),
        "sources": {"page.htm": driver.sha(source)},
    }
    rule = {
        "source": "page.htm",
        "source_sha256": driver.sha(source),
        "anchor": "scope",
        "heading": "Output in Abaqus/Explicit",
        "products": ["Abaqus/Explicit"],
    }
    rules = [rule]
    if variant == "heading":
        rule["heading"] = "Wrong heading"
    elif variant == "duplicate":
        rules.append(dict(rule))
    elif variant == "hash":
        rule["source_sha256"] = "bad"
    else:
        rule["products"] = ["Abaqus/Standard"]
    path = tmp_path / "rules.json"
    driver.write_new(path, {"sections": rules})
    monkeypatch.setattr(driver, "_check_binding", lambda _: manifest)
    monkeypatch.setattr(driver, "_load", lambda _: ([], {}))
    with pytest.raises(ValueError, match=r"heading|duplicate|source|conflicts"):
        BoundCandidateBackend(tmp_path, tmp_path, path)
