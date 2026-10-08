import hashlib
import shutil
from pathlib import Path
from typing import Any, cast

import lxml.etree as etree
import pytest

from abaqus_sim_agent.api_catalog import AbaqusScriptingReferenceLoader, ApiCatalog
from abaqus_sim_agent.api_catalog.models import (
    ApiParameter,
    ApiSourceRef,
)
from abaqus_sim_agent.api_catalog.reference_text import reference_text
from abaqus_sim_agent.coding.api_lookup import _api_lookup_payload


def source_catalog(tmp_path):
    root = tmp_path / "official"
    shutil.copytree(Path(__file__).parent / "fixtures/api_catalog/official", root)
    source = root / "English/SIMACAEKERRefMap/test-context.htm"
    shutil.copyfile(
        Path(__file__).parent / "fixtures/api_catalog/reference_context.htm", source
    )
    records = AbaqusScriptingReferenceLoader(root).load()
    selected = [r for r in records if r.source.document_id == "test-context"]
    catalog = ApiCatalog.build(runtime_release="2024", official_records=selected)
    catalog.write(tmp_path / "catalog")
    return catalog, selected, source


def test_context_preserves_conditions_math_order_and_sources(tmp_path):
    catalog, records, source = source_catalog(tmp_path)
    elastic = next(r for r in records if r.callable_name == "Elastic")
    description, table = elastic.context
    assert "connectors" in description.text
    assert "unrelatedMethod" not in description.raw_xhtml
    assert "This page discusses" not in description.text
    assert table.source.section_id == "elastic-table"
    assert table.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert (
        table.text.index("ISOTROPIC")
        < table.text.index("Young")
        < table.text.index("Poisson")
        < table.text.index("ENGINEERING_CONSTANTS")
    )
    assert "ν" in table.text
    assert "E_{1}" in table.text
    assert "(a)/(b)" in table.text
    assert "  -" not in table.text  # One-level list, no invented nesting.
    assert "<msub>" in table.raw_xhtml
    assert "Other private" not in table.text
    assert [
        x.kind
        for x in next(r for r in records if r.callable_name == "setValues").context
    ] == ["object_description"]
    assert ApiCatalog.open(tmp_path / "catalog").cards == catalog.cards


def test_method_and_parameter_reads_preserve_official_context(tmp_path):
    catalog, _, _ = source_catalog(tmp_path)
    normal = _api_lookup_payload(catalog.lookup("Material.Elastic"), error_excerpt=None)
    assert "connectors" in normal["cards"][0]["object_descriptions"][0]["text"]
    assert "table_data" not in normal["cards"][0]
    detail = _api_lookup_payload(
        catalog.lookup("Material.Elastic.table"), error_excerpt=None
    )
    assert "Poisson" in detail["parameter"]["table_data"][0]["text"]
    assert "object_descriptions" not in detail["parameter"]


def test_unknown_math_and_damaged_source_are_explicit():
    node = etree.fromstring(
        "<p>Damaged � <math><mroot><mi>x</mi><mn>3</mn></mroot></math></p>".encode()
    )
    value = reference_text(
        node,
        kind="table_data",
        source=ApiSourceRef(kind="official", release="2026", source_path="fixture"),
        source_sha256="0" * 64,
    )
    assert len(value.text_warnings) == 2
    assert "mroot" in value.raw_xhtml
    assert "mroot" in value.text


@pytest.mark.parametrize("query", ["Region", "Assembly.Surface"])
def test_bundled_multiple_definitions_expose_each_description_and_arguments(query):
    from abaqus_sim_agent.api_catalog.bundled import bundled_catalog_path

    catalog = ApiCatalog.open(bundled_catalog_path())
    lookup = catalog.lookup(query)
    card = lookup.cards[0]
    before = card.model_dump_json()
    payload = cast(dict[str, Any], _api_lookup_payload(lookup, error_excerpt=None))[
        "cards"
    ][0]
    assert len(card.official_records) > 1
    assert "official_variants" in payload["summary"]
    for name in (
        "required_arguments",
        "optional_arguments",
        "required_argument_details",
        "return_description",
    ):
        assert name not in payload
    for variant, record in zip(
        payload["official_variants"], card.official_records, strict=True
    ):
        assert variant["record_id"] == record.record_id
        assert variant["summary"] == record.summary
        assert variant["source_url"] == record.source.source_url
        assert variant["return_description"] == record.return_description
        for required in (True, False):
            key = "required" if required else "optional"
            parameters = (
                record.required_parameters if required else record.optional_parameters
            )
            assert variant[key + "_argument_details"] == [
                {"name": p.name, "description": p.description} for p in parameters
            ]
    assert len(payload["object_descriptions"]) == 1
    if query == "Region":
        set_form, surface_form = payload["official_variants"]
        assert "set-like" in set_form["summary"]
        assert "faces" in set_form["optional_arguments"]
        assert "side1Faces" not in set_form["optional_arguments"]
        assert "surface-like" in surface_form["summary"]
        assert "side1Faces" in surface_form["optional_arguments"]
        assert "faces" not in surface_form["optional_arguments"]
        assert "side1Faces=f[12:14]" in surface_form["summary"]
    assert card.model_dump_json() == before


def test_deduplication_keeps_distinct_sources_text_and_warnings(tmp_path):
    catalog, records, _ = source_catalog(tmp_path)
    record = next(r for r in records if r.callable_name == "Elastic")
    assert record.context is not None
    context = record.context[0]
    contexts = [
        context,
        context.model_copy(update={"raw_xhtml": "<p>Equivalent rendered text</p>"}),
        context.model_copy(update={"source_sha256": "1" * 64}),
        context.model_copy(
            update={"source": context.source.model_copy(update={"release": "2025"})}
        ),
        context.model_copy(
            update={"text": context.text + " An additional limitation."}
        ),
        context.model_copy(update={"text_warnings": ("Fixture warning",)}),
    ]
    variants = tuple(
        record.model_copy(update={"record_id": f"variant-{i}", "context": (value,)})
        for i, value in enumerate(contexts)
    )
    card = (
        catalog.lookup("Material.Elastic")
        .cards[0]
        .model_copy(update={"official_records": variants})
    )
    candidate = ApiCatalog([card], runtime_release="2024")
    descriptions = cast(
        dict[str, Any],
        _api_lookup_payload(candidate.lookup("Material.Elastic"), error_excerpt=None),
    )["cards"][0]["object_descriptions"]
    assert len(descriptions) == 5
    assert descriptions[0]["record_id"] == "variant-0"
    assert any(d["source_sha256"] == "1" * 64 for d in descriptions)
    assert any(d["source"]["release"] == "2025" for d in descriptions)
    assert any(d["text"].endswith("An additional limitation.") for d in descriptions)
    assert any(d["text_warnings"] == ["Fixture warning"] for d in descriptions)


@pytest.mark.parametrize("official_present", [True, False])
def test_single_or_absent_official_definition_retains_flat_format(
    tmp_path, official_present
):
    catalog, records, _ = source_catalog(tmp_path)
    record = next(r for r in records if r.callable_name == "Elastic")
    card = (
        catalog.lookup("Material.Elastic")
        .cards[0]
        .model_copy(update={"official_records": (record,) if official_present else ()})
    )
    candidate = ApiCatalog([card], runtime_release="2024")
    payload = cast(
        dict[str, Any],
        _api_lookup_payload(candidate.lookup("Material.Elastic"), error_excerpt=None),
    )["cards"][0]
    assert payload["summary"] == (record.summary if official_present else "")
    assert payload["required_arguments"] == (
        [p.name for p in record.required_parameters] if official_present else []
    )
    assert payload["optional_arguments"] == (
        [p.name for p in record.optional_parameters] if official_present else []
    )
    assert payload["return_description"] == (
        record.return_description if official_present else ""
    )
    assert len(payload["official_variants"]) == int(official_present)


def test_variant_required_parameters_are_not_flattened(tmp_path):
    catalog, records, _ = source_catalog(tmp_path)
    record = next(r for r in records if r.callable_name == "Elastic")
    variants = tuple(
        record.model_copy(
            update={
                "record_id": name,
                "summary": f"Construct from a {name}.",
                "required_parameters": (
                    ApiParameter(
                        name=name, required=True, description=f"The input {name}."
                    ),
                ),
                "optional_parameters": (),
            }
        )
        for name in ("part", "model")
    )
    card = (
        catalog.lookup("Material.Elastic")
        .cards[0]
        .model_copy(update={"official_records": variants})
    )
    payload = cast(
        dict[str, Any],
        _api_lookup_payload(
            ApiCatalog([card], runtime_release="2024").lookup("Material.Elastic"),
            error_excerpt=None,
        ),
    )["cards"][0]
    first, second = payload["official_variants"]
    assert first["required_argument_details"] == [
        {"name": "part", "description": "The input part."}
    ]
    assert second["required_argument_details"] == [
        {"name": "model", "description": "The input model."}
    ]
    assert "required_argument_details" not in payload
