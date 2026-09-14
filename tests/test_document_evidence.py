import pytest
from langchain_core.documents import Document
from pydantic import ValidationError

from abaqus_sim_agent.coding.concept_lookup import _evidence_payloads as active_evidence
from abaqus_sim_agent.coding.contracts import AbaqusDocEvidence, AbaqusDocEvidenceBundle
from abaqus_sim_agent.coding.diagnostic_retrieval import (
    _evidence_payloads as advisory_evidence,
)
from abaqus_sim_agent.coding.document_evidence import (
    DOCUMENT_BODY_BUDGET,
    document_body,
    render_document_bodies,
)
from abaqus_sim_agent.retrieval import RetrievalHit


def passage(text: str, *, path: bool = True) -> Document:
    section = "Manual > Conditions" if path else "Conditions"
    metadata: dict[str, object] = {
        "title": "Manual",
        "section_title": "Conditions",
        "entity_kind": "concept_chunk",
        "source_url": "https://example.org/manual#conditions",
        "release": "2026 FD02",
    }
    if path:
        metadata["heading_path"] = ["Manual", "Conditions"]
    return Document(
        id="page#conditions",
        page_content=f"Document: Manual\nSection: {section}\n{text}",
        metadata=metadata,
    )


@pytest.mark.parametrize("path", [True, False])
def test_full_passage_keeps_late_conditions_and_code_indentation(path: bool):
    body = (
        "Introduction.\n" * 80
        + "However, these procedures are exceptions.\nif condition:\n    preserve_indent()"
    )
    doc = passage(body, path=path)
    before = doc.page_content
    result = render_document_bodies([doc])[0]
    assert len(result.text) > 600
    assert result.text == body
    assert result.original_characters == len(body)
    assert not result.truncated
    assert doc.page_content == before


def test_only_recognized_generated_prefix_is_removed():
    doc = passage("Body")
    doc.metadata["title"] = "Different"
    assert document_body(doc).startswith("Document: Manual")
    card = Document(
        page_content="Document: Manual\nSection: Conditions\nSymbol: X",
        metadata={
            "title": "Manual",
            "section_title": "Conditions",
            "entity_kind": "output_variable",
        },
    )
    assert document_body(card) == card.page_content


def test_shared_budget_is_rank_ordered_and_marks_omitted_tails():
    rendered = render_document_bodies(
        [passage("a" * 7000), passage("b" * 7000), passage("tail")]
    )
    assert [len(item.text) for item in rendered] == [7000, 5000, 0]
    assert [item.truncated for item in rendered] == [False, True, True]
    assert [item.original_characters for item in rendered] == [7000, 7000, 4]
    assert sum(len(item.text) for item in rendered) == DOCUMENT_BODY_BUDGET


def test_exact_boundary_empty_and_invalid_budget():
    assert not render_document_bodies([passage("abc")], budget=3)[0].truncated
    assert render_document_bodies([passage("abc")], budget=0)[0].truncated
    assert not render_document_bodies([passage("")], budget=0)[0].truncated
    assert render_document_bodies([]) == ()
    with pytest.raises(ValueError, match="not be negative"):
        render_document_bodies([], budget=-1)


def test_active_and_advisory_share_body_budget_and_preserve_provenance():
    docs = [passage("a" * 7000), passage("b" * 7000), passage("tail")]
    active = active_evidence(
        [RetrievalHit(document=d, match_type="semantic") for d in docs]
    )
    advisory = advisory_evidence(
        tuple(RetrievalHit(document=d, match_type="hybrid") for d in docs)
    )
    for item, other in zip(active, advisory, strict=True):
        for field in (
            "excerpt",
            "original_characters",
            "truncated",
            "truncation_reason",
            "delivery_version",
            "source",
            "release",
        ):
            assert getattr(item, field) == other[field]
        assert item.heading_path == ("Manual", "Conditions")
    assert [x.truncation_reason for x in active] == [None, "body_budget", "body_budget"]
    bundle = AbaqusDocEvidenceBundle(
        status="found",
        question="Question",
        query="Question",
        notice="indexed passages",
        evidence=active,
    )
    assert sum(len(item.excerpt) for item in bundle.evidence) == DOCUMENT_BODY_BUDGET


def test_typed_evidence_rejects_false_completeness_and_oversized_bundle():
    evidence = active_evidence(
        [RetrievalHit(document=passage("a" * 7000), match_type="semantic")]
    )[0]
    payload = evidence.model_dump()
    with pytest.raises(ValidationError, match="truncated flag"):
        AbaqusDocEvidence.model_validate({**payload, "truncated": True})
    with pytest.raises(ValidationError, match="truncation reason"):
        AbaqusDocEvidence.model_validate(
            {**payload, "truncation_reason": "body_budget"}
        )
    with pytest.raises(ValidationError, match="original length"):
        AbaqusDocEvidence.model_validate({**payload, "original_characters": 1})
    with pytest.raises(ValidationError, match="shared body budget"):
        AbaqusDocEvidenceBundle(
            status="found",
            question="Question",
            query="Question",
            notice="notice",
            evidence=(evidence, evidence),
        )
