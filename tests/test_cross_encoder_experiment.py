import pytest
from langchain_core.documents import Document

from abaqus_sim_agent.evaluation.cross_encoder import (
    candidate_union,
    passage_text,
    rank_scores,
)


def document(key: str, text: str = "body") -> Document:
    return Document(id=key, page_content=text, metadata={"document_id": "same-page"})


def test_union_keeps_same_page_passages_and_dedupes_only_identical_ids():
    a, b = document("a"), document("b")
    assert candidate_union([a, b], [b]) == [a, b]


def test_union_rejects_missing_and_conflicting_ids():
    with pytest.raises(ValueError, match="record ID"):
        candidate_union([Document(page_content="body")])
    with pytest.raises(ValueError, match="conflicting"):
        candidate_union([document("a")], [document("a", "different")])


def test_scores_keep_identity_descending_order_and_report_truncation():
    rows = rank_scores(
        [document("b"), document("a"), document("c")], [1.0, 1.0, -1.0], [512, 513, 20]
    )
    assert [row.document.id for row in rows] == ["a", "b", "c"]
    assert [row.truncated for row in rows] == [True, False, False]


def test_bad_scores_fail_instead_of_becoming_a_ranking():
    with pytest.raises(ValueError, match="count"):
        rank_scores([document("a")], [], [])
    with pytest.raises(ValueError, match="finite"):
        rank_scores([document("a")], [float("nan")], [10])


def test_text_retains_heading_context_without_truncating_body_to_tool_budget():
    body = "Long technical body. " * 80
    source = Document(
        id="x",
        page_content=f"Document: Manual\nSection: Manual > Scope\n{body}",
        metadata={
            "title": "Manual",
            "heading_path": ["Manual", "Scope"],
            "section_title": "Scope",
            "entity_kind": "concept_chunk",
        },
    )
    text = passage_text(source)
    assert text.startswith("Title: Manual\nSection: Manual > Scope\n")
    assert "Document: Manual" not in text
    assert len(text) > 600
    assert source.page_content.endswith(body)
