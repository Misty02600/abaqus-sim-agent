import hashlib
import json
import shutil
from pathlib import Path

import lxml.etree as etree

from abaqus_sim_agent.api_catalog import AbaqusScriptingReferenceLoader, ApiCatalog
from abaqus_sim_agent.api_catalog.cli import main
from abaqus_sim_agent.api_catalog.discovery import (
    PROJECTION_V2,
    CatalogDiscovery,
    make_views,
)
from abaqus_sim_agent.api_catalog.models import (
    ApiParameter,
    ApiSourceRef,
    OfficialApiRecord,
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


def test_context_preserves_conditions_math_order_sources_and_old_records(tmp_path):
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
    old = elastic.model_dump(mode="json")
    old.pop("context")
    old_record = OfficialApiRecord.model_validate_json(json.dumps(old))
    assert old_record.context is None
    assert "context" not in old_record.model_dump()
    assert ApiCatalog.open(tmp_path / "catalog").cards == catalog.cards


def test_existing_reads_and_versioned_discovery_are_usable(tmp_path):
    catalog, _, _ = source_catalog(tmp_path)
    normal = _api_lookup_payload(catalog.lookup("Material.Elastic"), error_excerpt=None)
    assert "connectors" in normal["cards"][0]["object_descriptions"][0]["text"]
    assert "table_data" not in normal["cards"][0]
    detail = _api_lookup_payload(
        catalog.lookup("Material.Elastic.table"), error_excerpt=None
    )
    assert "Poisson" in detail["parameter"]["table_data"][0]["text"]
    assert "object_descriptions" not in detail["parameter"]
    original = CatalogDiscovery.build(catalog, tmp_path / "v1")
    improved = CatalogDiscovery.build(
        catalog, tmp_path / "v2", projection=PROJECTION_V2
    )
    assert CatalogDiscovery.open(catalog, tmp_path / "v1").views == original.views
    assert improved.manifest.projection == PROJECTION_V2
    target = next(v for v in improved.views if v.name == "Material.Elastic")
    assert "Poisson" in target.text
    assert "ISOTROPIC" in target.text
    assert (
        "Poisson"
        not in next(v for v in make_views(catalog) if v.name == "Material.Elastic").text
    )
    assert improved.read(target.card_id).official_records[0].context


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


def test_v2_keeps_variant_parameters_separate(tmp_path):
    catalog, records, _ = source_catalog(tmp_path)
    record = next(r for r in records if r.callable_name == "Elastic")
    variants = tuple(
        record.model_copy(
            update={
                "record_id": str(i),
                "context": (),
                "summary": "Create an instance.",
                "required_parameters": (
                    ApiParameter(name=name, required=True, description=description),
                ),
            }
        )
        for i, name, description in [
            (1, "part", "A Part to be instanced."),
            (2, "model", "A Model to be instanced."),
        ]
    )
    card = (
        catalog.lookup("Material.Elastic")
        .cards[0]
        .model_copy(update={"official_records": variants})
    )
    view = make_views(ApiCatalog([card], runtime_release="2024"), PROJECTION_V2)[0]
    assert "Official variant 1 part: A Part" in view.text
    assert "Official variant 2 model: A Model" in view.text
    assert "Official variant 1 model:" not in view.text


def test_cli_can_explicitly_build_v2(tmp_path, capsys):
    source_catalog(tmp_path)
    assert (
        main(
            [
                "index",
                "--catalog-dir",
                str(tmp_path / "catalog"),
                "--index-dir",
                str(tmp_path / "index"),
                "--projection",
                PROJECTION_V2,
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["projection"] == PROJECTION_V2
