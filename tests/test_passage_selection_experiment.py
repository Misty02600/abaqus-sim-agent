import pytest
from langchain_core.documents import Document

from abaqus_sim_agent.evaluation.passage_selection import (
    body_first_excerpt,
    fuse_passage_candidates,
)


def doc(key: str, page: str = "shared") -> Document:
    return Document(id=key, page_content=key, metadata={"document_id": page})


def test_fusion_keeps_distinct_passages_from_same_page():
    a, b = doc("a"), doc("b")
    result = fuse_passage_candidates([a, b], [b, a])
    assert [hit.document.id for hit in result] == ["a", "b"]
    assert result[0].score == pytest.approx(1 / 61 + 1 / 62)


def test_fusion_duplicate_in_one_source_is_not_an_extra_vote():
    a, b = doc("a"), doc("b")
    assert fuse_passage_candidates([a, a, b], [b]) == fuse_passage_candidates(
        [a, b], [b]
    )


def test_fusion_does_not_invent_candidates_and_obeys_top_k():
    a, b = doc("a"), doc("b")
    assert fuse_passage_candidates([], []) == []
    assert [hit.document.id for hit in fuse_passage_candidates([a], [b], top_k=1)] == [
        "a"
    ]


@pytest.mark.parametrize(("top_k", "rrf_k"), [(0, 60), (4, 0)])
def test_fusion_rejects_invalid_budgets(top_k: int, rrf_k: int):
    with pytest.raises(ValueError, match="must be positive"):
        fuse_passage_candidates([], [], top_k=top_k, rrf_k=rrf_k)


def test_fusion_rejects_missing_or_conflicting_id():
    with pytest.raises(ValueError, match="record ID"):
        fuse_passage_candidates([Document(page_content="no id")], [])
    with pytest.raises(ValueError, match="conflicting"):
        fuse_passage_candidates([doc("a")], [doc("a", page="other")])


@pytest.mark.parametrize("path", [[], ["Manual", "Chapter", "Scope"]])
def test_body_formatter_removes_only_generated_prefix_and_keeps_source(path: list[str]):
    section = " > ".join(path) if path else "Scope"
    text = f"Document: Manual\nSection: {section}\nAn applicable condition."
    document = Document(
        page_content=text,
        metadata={
            "title": "Manual",
            "section_title": "Scope",
            "entity_kind": "concept_chunk",
            "heading_path": path,
        },
    )
    assert body_first_excerpt(document) == "An applicable condition."
    assert document.page_content == text
    assert document.metadata["heading_path"] == path


@pytest.mark.parametrize("kind", ["keyword", "output_variable", "summary", "unknown"])
def test_formatter_does_not_strip_other_record_types(kind: str):
    document = Document(
        page_content="Document: Manual\nSection: Scope\nBody",
        metadata={"title": "Manual", "section_title": "Scope", "entity_kind": kind},
    )
    assert body_first_excerpt(document) == "Document: Manual Section: Scope Body"


def test_formatter_keeps_unrecognized_prefix_and_body_document_words():
    document = Document(
        page_content="Document: Real text\nSection: Scope\nDocument: body",
        metadata={
            "title": "Manual",
            "section_title": "Scope",
            "entity_kind": "concept_chunk",
        },
    )
    assert body_first_excerpt(document).startswith("Document: Real text")


def test_formatter_obeys_limit_without_claiming_complete_sentences():
    document = Document(page_content="123456789", metadata={})
    assert body_first_excerpt(document, limit=5) == "1234…"
    assert body_first_excerpt(document, limit=1) == "…"
    with pytest.raises(ValueError, match="limit must be positive"):
        body_first_excerpt(document, limit=0)
