from pathlib import Path

import pytest

from abaqus_sim_agent.retrieval import AbaqusDocumentError, AbaqusDocumentLoader

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "abaqus_docs"
GUIDES = [
    "SIMACAEANLRefMap",
    "SIMACAEKEYRefMap",
    "SIMACAEELMRefMap",
    "SIMACAEOUTRefMap",
]


def test_loader_emits_concept_and_atomic_reference_documents() -> None:
    loader = AbaqusDocumentLoader(FIXTURE_ROOT, guide_ids=GUIDES)

    documents = list(loader.lazy_load())

    assert loader.release == "2026 FD02"
    assert loader.stats.files_seen == 4
    assert loader.stats.files_loaded == 4
    assert loader.stats.files_skipped == 0
    assert loader.stats.documents_emitted == len(documents)
    assert all(document.id for document in documents)
    assert all(
        str(document.metadata["source_url"]).startswith(
            "http://localhost:4040/English/"
        )
        for document in documents
    )
    assert not any(
        "HEADER NAVIGATION MUST NOT BE INDEXED" in document.page_content
        for document in documents
    )
    assert any(
        document.metadata["section_id"] == "simaanl-c-static-linear"
        and "inertia effects" in document.page_content
        for document in documents
    )
    assert any(
        document.metadata["section_id"] == "simakey-r-static-parameters"
        and "ALLSDTOL" in document.page_content
        and document.metadata["retrieval_role"] == "semantic"
        for document in documents
    )

    by_symbol = {
        document.metadata.get("primary_symbol"): document
        for document in documents
        if document.metadata.get("primary_symbol")
    }
    assert by_symbol["*STATIC"].metadata["entity_kind"] == "keyword"
    assert by_symbol["ALLSDTOL"].metadata["entity_kind"] == "keyword_parameter"
    assert by_symbol["CPS4R"].metadata["entity_kind"] == "element"
    assert by_symbol["U"].metadata["entity_kind"] == "output_variable"
    assert by_symbol["U"].metadata["products"] == ["Abaqus/Standard"]
    assert by_symbol["RF"].metadata["document_id"] == ("simaout-c-std-nodalvariables")


def test_loader_rejects_page_without_stable_identifier(tmp_path: Path) -> None:
    guide = tmp_path / "English" / "SIMACAEANLRefMap"
    guide.mkdir(parents=True)
    (guide / "invalid.htm").write_text(
        "<html><head><title>Invalid</title></head>"
        '<body id="different"><div class="body">text</div></body></html>',
        encoding="utf-8",
    )
    loader = AbaqusDocumentLoader(
        tmp_path,
        guide_ids=["SIMACAEANLRefMap"],
    )

    with pytest.raises(AbaqusDocumentError, match="stable identifier"):
        list(loader.lazy_load())


def test_loader_rejects_invalid_chunk_size() -> None:
    with pytest.raises(ValueError, match="max_chunk_size"):
        AbaqusDocumentLoader(FIXTURE_ROOT, max_chunk_size=0)


def test_scope_metadata_preserves_inline_headings_and_separates_page_from_section() -> (
    None
):
    source = Path(__file__).parent / "fixtures/abaqus_docs_scope"
    page = source / "English/SIMACAEOUTRefMap/scope-demo.htm"
    original = page.read_bytes()
    documents = AbaqusDocumentLoader(source, guide_ids=["SIMACAEOUTRefMap"]).load()
    assert page.read_bytes() == original
    standard = next(
        doc for doc in documents if "Standard timing fixture" in doc.page_content
    )
    explicit = next(
        doc for doc in documents if "Explicit timing fixture" in doc.page_content
    )
    for doc in (standard, explicit):
        assert doc.metadata["products"] == ["Abaqus/Standard", "Abaqus/Explicit"]
        assert doc.metadata["products_scope"] == "page"
        assert doc.metadata["products_source"] == "official_page_header"
        assert doc.metadata["document_context_version"] == "abaqus-document-context-v2"
    assert standard.metadata["heading_path"] == [
        "Output timing",
        "Output in Abaqus/Standard",
        "Time points",
    ]
    assert standard.metadata["section_product_mentions"] == ["Abaqus/Standard"]
    assert standard.metadata["section_product_heading"] == "Output in Abaqus/Standard"
    assert "Output in Abaqus/Standard > Time points" in standard.page_content
    assert explicit.metadata["section_product_mentions"] == ["Abaqus/Explicit"]
    assert explicit.metadata["section_id"] == "explicit-output"
    reference = next(
        doc for doc in documents if doc.metadata.get("primary_symbol") == "U"
    )
    assert reference.metadata["section_id"] == "example-u"
    assert reference.metadata["heading_path"] == standard.metadata["heading_path"]
    assert reference.metadata["section_product_mentions"] == ["Abaqus/Standard"]
    comparison = next(
        doc for doc in documents if "comparison discusses" in doc.page_content
    )
    assert comparison.metadata["section_product_mentions"] == [
        "Abaqus/Standard",
        "Abaqus/Explicit",
    ]
    common = next(doc for doc in documents if "General fixture" in doc.page_content)
    assert "section_product_mentions" not in common.metadata
    assert common.metadata["products"] == ["Abaqus/Standard", "Abaqus/Explicit"]


def test_product_provenance_distinguishes_inference_from_unknown_and_prose() -> None:
    source = Path(__file__).parent / "fixtures/abaqus_docs_scope"
    documents = AbaqusDocumentLoader(source, guide_ids=["SIMACAEOUTRefMap"]).load()
    inferred = next(
        doc
        for doc in documents
        if doc.metadata["document_id"] == "simaout-c-std-scope-example"
    )
    unknown = next(
        doc for doc in documents if doc.metadata["document_id"] == "scope-unknown"
    )
    assert inferred.metadata["products_source"] == "document_id_inference"
    assert inferred.metadata["products"] == ["Abaqus/Standard"]
    assert unknown.metadata["products_source"] == "unknown"
    assert "products" not in unknown.metadata
    assert "section_product_mentions" not in unknown.metadata
