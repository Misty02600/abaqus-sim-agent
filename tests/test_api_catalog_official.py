import shutil
from pathlib import Path

import pytest

from abaqus_sim_agent.api_catalog import (
    AbaqusScriptingReferenceLoader,
    AbqpyApiRecord,
    ApiCatalog,
    ApiParameter,
    ApiRuntimeNote,
    ApiSignature,
    ApiSourceRef,
    load_abqpy_records,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "api_catalog" / "official"
ABQPY_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "api_catalog" / "abqpy" / "source"
)


def test_factory_receiver_and_multiline_paths_preserve_actual_owner(tmp_path):
    root = tmp_path / "official"
    shutil.copytree(FIXTURE_ROOT, root)
    guide = root / "English" / "SIMACAEKERRefMap"
    for owner, call, paths in (
        ("Model", "Model", "mdb.Model"),
        ("ModelJob", "Job", "mdb.Job"),
        (
            "Elastic",
            "Elastic",
            "mdb.models[name].materials[name].Elastic\nsession.odbs[name].materials[name].Elastic",
        ),
    ):
        ident = f"test-{owner}"
        (guide / f"{ident}.htm").write_text(
            f"""<html><head>
<meta name="DC.identifier" content="{ident}"/><title>{owner} object</title></head>
<body id="{ident}"><article class="pyconstructsect" id="ctor-{owner}">
<h2><span class="pyctor">{call}(...)</span></h2><p class="abstract">Creates {owner}.</p>
<div class="pypath"><pre class="codeblock">{paths}</pre></div></article></body></html>""",
            encoding="utf-8",
        )
    records = AbaqusScriptingReferenceLoader(root).load()
    by_id = {r.source.document_id: r for r in records}
    assert by_id["test-Model"].canonical_name == "Mdb.Model"
    assert by_id["test-ModelJob"].canonical_name == "Mdb.Job"
    elastic = by_id["test-Elastic"]
    assert elastic.canonical_name == "Material.Elastic"
    assert "Model.Elastic" not in elastic.public_names
    assert "Session.Elastic" not in elastic.public_names
    assert elastic.paths == (
        "mdb.models[name].materials[name].Elastic",
        "session.odbs[name].materials[name].Elastic",
    )
    stub_source = ApiSourceRef(
        kind="abqpy", release="2024", version="2024.test", source_path="factory.py"
    )
    stub_records = tuple(
        AbqpyApiRecord(
            record_id=f"stub:{name}",
            entity_kind="method",
            declaring_name=name,
            class_name=name.rpartition(".")[0],
            method_name=name.rpartition(".")[2],
            public_names=(name,),
            signatures=(ApiSignature(text=name + "()"),),
            source=stub_source,
        )
        for name in ("Mdb.Model", "Mdb.Job", "Material.Elastic")
    )
    catalog = ApiCatalog.build(
        runtime_release="2024", official_records=records, abqpy_records=stub_records
    )
    for name in ("Mdb.Model", "Mdb.Job", "Material.Elastic"):
        result = catalog.lookup(name)
        assert result.status == "found"
        assert result.cards[0].official_records
        assert result.cards[0].abqpy_records
    assert catalog.lookup("Model.Elastic").status == "not_found"


def test_nested_receiver_does_not_become_parent_method(tmp_path):
    root = tmp_path / "official"
    shutil.copytree(FIXTURE_ROOT, root)
    guide = root / "English" / "SIMACAEKERRefMap"
    (guide / "test-nested.htm").write_text(
        """<html><head>
<meta name="DC.identifier" content="test-nested"/><title>FaceArray object</title></head>
<body id="test-nested"><article class="pymethodsect" id="nested-call">
<h2><span class="pymethod">findAt(...)</span></h2>
<div class="pypath"><pre class="codeblock">mdb.models[name].parts[name].faces.findAt</pre></div>
</article></body></html>""",
        encoding="utf-8",
    )
    record = next(
        r
        for r in AbaqusScriptingReferenceLoader(root).load()
        if r.source.document_id == "test-nested"
    )
    assert record.public_names == ("FaceArray.findAt",)


def test_cpp_reference_article_is_not_a_python_constructor(tmp_path):
    root = tmp_path / "official"
    shutil.copytree(FIXTURE_ROOT, root)
    guide = root / "English" / "SIMACAEKERRefMap"
    (guide / "test-cpp.htm").write_text(
        """<html><head>
<meta name="DC.identifier" content="test-cpp"/><title>Elastic object</title></head>
<body id="test-cpp"><div class="cppaccess"><pre>#include &lt;odb_API.h&gt;</pre></div>
<article class="pyconstructsect" id="cpp-ctor"><h2><span class="pyctor">Elastic(...)</span></h2>
<p class="abstract">C++ constructor.</p><div class="cpppath"><pre>odb_Elastic</pre></div>
<div class="cppproto"><pre>odb_Elastic&amp; Elastic(...)</pre></div></article>
<article class="pymethodsect" id="cpp-set"><h2><span class="pymethod">setValues(...)</span></h2>
<p class="abstract">A C++ method without its own Path section.</p></article></body></html>""",
        encoding="utf-8",
    )
    records = AbaqusScriptingReferenceLoader(root).load()
    assert len(records) == len(AbaqusScriptingReferenceLoader(FIXTURE_ROOT).load())
    assert all(r.source.document_id != "test-cpp" for r in records)


def test_loader_emits_atomic_method_constructor_and_command_records() -> None:
    loader = AbaqusScriptingReferenceLoader(FIXTURE_ROOT)

    records = loader.load()

    assert loader.release == "2026 FD02"
    assert len(records) == 7
    by_name = {record.canonical_name: record for record in records}

    seed = by_name["Part.seedEdgeByNumber"]
    assert seed.entity_kind == "method"
    assert seed.access == ("import mesh",)
    assert [parameter.name for parameter in seed.required_parameters] == [
        "edges",
        "number",
    ]
    assert [parameter.name for parameter in seed.optional_parameters] == ["constraint"]
    assert seed.return_description == "None."
    assert seed.exceptions == ()
    assert seed.source.document_id == "simaker-c-partmgnpyc"
    assert seed.source.section_id == "simaker-partseededgebynumberpyc"
    assert seed.source.source_url is not None
    assert seed.source.source_url.endswith(
        "simaker-c-partmgnpyc.htm#simaker-partseededgebynumberpyc"
    )

    find_at = by_name["EdgeArray.findAt"]
    assert find_at.return_description == "An Edge or a sequence of Edge objects."
    elem_type = by_name["ElemType.__init__"]
    assert elem_type.entity_kind == "constructor"
    assert elem_type.access == ("import mesh",)
    assert elem_type.public_names == (
        "ElemType.__init__",
        "ElemType.ElemType",
    )
    assert elem_type.aliases == ("ElemType",)
    assert elem_type.required_parameters[0].description == (
        "A SymbolicConstant specifying the Abaqus element code."
    )
    assert by_name["Model.DisplacementBC"].entity_kind == "constructor"
    assembly_set = next(
        record for record in records if "Assembly.Set" in record.public_names
    )
    assert assembly_set.paths == (
        "mdb.models[name].parts[name].Set",
        "mdb.models[name].rootAssembly.Set",
    )
    assert by_name["highlight"].entity_kind == "command"


def test_loader_accepts_language_or_guide_root() -> None:
    language_root = FIXTURE_ROOT / "English"
    guide_root = language_root / "SIMACAEKERRefMap"

    from_language = AbaqusScriptingReferenceLoader(language_root).load()
    from_guide = AbaqusScriptingReferenceLoader(guide_root).load()

    assert [record.record_id for record in from_language] == [
        record.record_id for record in from_guide
    ]


def test_catalog_merges_public_part_alias_with_abqpy_declaring_mixin(
    tmp_path: Path,
) -> None:
    official_records = AbaqusScriptingReferenceLoader(FIXTURE_ROOT).load()
    seed_record = AbqpyApiRecord(
        record_id="abqpy:2024.10:abaqus.Mesh.MeshPart.MeshPart.seedEdgeByNumber",
        entity_kind="method",
        declaring_name="MeshPart.seedEdgeByNumber",
        class_name="MeshPart",
        method_name="seedEdgeByNumber",
        public_names=(
            "MeshPart.seedEdgeByNumber",
            "Part.seedEdgeByNumber",
        ),
        aliases=("abaqus.Mesh.MeshPart.MeshPart.seedEdgeByNumber",),
        signatures=(
            ApiSignature(
                text=(
                    "seedEdgeByNumber(edges: Sequence[Edge], number: int, "
                    "constraint: SymbolicConstant = ...)"
                ),
                parameters=(
                    ApiParameter(
                        name="edges",
                        annotation="Sequence[Edge]",
                        required=True,
                    ),
                    ApiParameter(name="number", annotation="int", required=True),
                    ApiParameter(
                        name="constraint",
                        annotation="SymbolicConstant",
                        default="...",
                        required=False,
                    ),
                ),
            ),
        ),
        bases=("PartBase",),
        source=ApiSourceRef(
            kind="abqpy",
            release="2024",
            version="2024.10",
            source_path="abaqus/Mesh/MeshPart.py",
        ),
    )
    catalog = ApiCatalog.build(
        runtime_release="2024",
        official_records=official_records,
        abqpy_records=(seed_record,),
    )

    result = catalog.lookup("Part.seedEdgeByNumber")

    assert result.status == "found"
    card = result.cards[0]
    assert card.declaring_name == "MeshPart.seedEdgeByNumber"
    assert len(card.official_records) == 1
    assert len(card.abqpy_records) == 1
    assert card.release_alignment == "mismatch"
    assert card.signatures[0].parameters[0].annotation == "Sequence[Edge]"

    output_dir = tmp_path / "api-catalog"
    manifest = catalog.write(output_dir)
    reopened = ApiCatalog.open(output_dir)
    assert manifest.card_count == len(catalog.cards)
    assert {(source.kind, source.source_path) for source in manifest.sources} == {
        ("abqpy", "abqpy-2024.10"),
        ("official", "SIMACAEKERRefMap"),
    }
    assert reopened.lookup("Part.seedEdgeByNumber").status == "found"
    with pytest.raises(FileExistsError):
        catalog.write(output_dir)


def test_public_receiver_overloads_from_sibling_mixins_merge() -> None:
    source = ApiSourceRef(
        kind="abqpy",
        release="2024",
        version="2024.test",
        source_path="abaqus/mesh.py",
    )
    records = (
        AbqpyApiRecord(
            record_id="abqpy:2024.test:MeshEditPart.generateMesh",
            entity_kind="method",
            declaring_name="MeshEditPart.generateMesh",
            class_name="MeshEditPart",
            method_name="generateMesh",
            public_names=("Part.generateMesh", "MeshEditPart.generateMesh"),
            signatures=(ApiSignature(text="generateMesh(elemShape=None)"),),
            bases=("abaqus.Part.PartBase.PartBase",),
            source=source,
        ),
        AbqpyApiRecord(
            record_id="abqpy:2024.test:MeshPart.generateMesh",
            entity_kind="method",
            declaring_name="MeshPart.generateMesh",
            class_name="MeshPart",
            method_name="generateMesh",
            public_names=("Part.generateMesh", "MeshPart.generateMesh"),
            signatures=(ApiSignature(text="generateMesh(regions=())"),),
            bases=("abaqus.Part.PartBase.PartBase",),
            source=source,
        ),
    )

    catalog = ApiCatalog.build(runtime_release="2024", abqpy_records=records)
    result = catalog.lookup("Part.generateMesh")

    assert result.status == "found"
    card = result.cards[0]
    assert card.card_id == "api:2024:public-overload:Part.generateMesh"
    assert card.declaring_name == "Part.generateMesh"
    assert card.class_name == "Part"
    assert len(card.abqpy_records) == 2
    assert [signature.text for signature in card.signatures] == [
        "generateMesh(elemShape=None)",
        "generateMesh(regions=())",
    ]


def test_lookup_reports_ambiguous_bare_method_and_case_sensitive_suggestion() -> None:
    official_records = AbaqusScriptingReferenceLoader(FIXTURE_ROOT).load()
    catalog = ApiCatalog.build(
        runtime_release="2026",
        official_records=official_records,
    )

    assert catalog.lookup("EdgeArray.findAt").status == "found"
    bare = catalog.lookup("findAt")
    assert bare.status == "ambiguous"
    assert {card.canonical_name for card in bare.cards} == {
        "EdgeArray.findAt",
        "VertexArray.findAt",
    }
    missing = catalog.lookup("edgearray.findat")
    assert missing.status == "not_found"
    assert missing.suggestions == ("EdgeArray.findAt",)


def test_lookup_resolves_full_and_short_method_parameter_queries() -> None:
    catalog = ApiCatalog.build(
        runtime_release="2026",
        official_records=AbaqusScriptingReferenceLoader(FIXTURE_ROOT).load(),
    )

    full = catalog.lookup("Part.seedEdgeByNumber.edges")
    short = catalog.lookup("seedEdgeByNumber.edges")
    wrong_case = catalog.lookup("Part.seedEdgeByNumber.Edges")

    assert full.status == "found"
    assert full.parameter_name == "edges"
    assert full.cards[0].canonical_name == "Part.seedEdgeByNumber"
    assert short.status == "found"
    assert short.parameter_name == "edges"
    assert short.cards == full.cards
    assert wrong_case.status == "not_found"
    assert wrong_case.parameter_name is None
    assert wrong_case.suggestions == ("Part.seedEdgeByNumber.edges",)
    assert catalog.lookup("Part.seedEdgeByNumber").parameter_name is None


def test_shared_official_set_article_merges_into_part_and_assembly_cards() -> None:
    catalog = ApiCatalog.build(
        runtime_release="2024",
        official_records=AbaqusScriptingReferenceLoader(FIXTURE_ROOT).load(),
        abqpy_records=load_abqpy_records(
            ABQPY_FIXTURE_ROOT,
            version="2024.test",
            release="2024",
        ),
    )

    part = catalog.lookup("Part.Set")
    assembly = catalog.lookup("Assembly.Set")

    assert part.status == "found"
    assert part.cards[0].declaring_name == "RegionPart.Set"
    assert len(part.cards[0].official_records) == 1
    assert assembly.status == "found"
    assert assembly.cards[0].declaring_name == "RegionAssembly.Set"
    assert len(assembly.cards[0].official_records) == 1


def test_direct_official_constructor_merges_with_abqpy_init_card() -> None:
    catalog = ApiCatalog.build(
        runtime_release="2024",
        official_records=AbaqusScriptingReferenceLoader(FIXTURE_ROOT).load(),
        abqpy_records=load_abqpy_records(
            ABQPY_FIXTURE_ROOT,
            version="2024.test",
            release="2024",
        ),
    )

    by_init = catalog.lookup("ElemType.__init__")
    by_owner = catalog.lookup("ElemType")

    assert by_init.status == "found"
    card = by_init.cards[0]
    assert card.declaring_name == "ElemType.__init__"
    assert len(card.official_records) == 1
    assert len(card.abqpy_records) == 1
    assert card.official_records[0].access == ("import mesh",)
    assert by_owner.status == "found"
    assert by_owner.cards == (card,)
    assert catalog.lookup("Model.DisplacementBC").cards[0].declaring_name == (
        "BoundaryConditionModel.DisplacementBC"
    )


def test_same_short_class_name_in_different_modules_stays_distinct() -> None:
    records = tuple(
        AbqpyApiRecord(
            record_id=f"abqpy:2024.test:{qualified_name}.__init__",
            entity_kind="constructor",
            declaring_name="Spot.__init__",
            class_name="Spot",
            method_name="__init__",
            public_names=("Spot.__init__",),
            aliases=(f"{qualified_name}.__init__",),
            signatures=(ApiSignature(text="__init__(name: str)"),),
            source=ApiSourceRef(
                kind="abqpy",
                release="2024",
                version="2024.test",
                source_path=source_path,
            ),
        )
        for qualified_name, source_path in (
            (
                "abaqus.Sketcher.ConstrainedSketchGeometry.Spot.Spot",
                "abaqus/Sketcher/ConstrainedSketchGeometry/Spot.py",
            ),
            (
                "abaqus.Sketcher.ConstrainedSketchVertex.Spot.Spot",
                "abaqus/Sketcher/ConstrainedSketchVertex/Spot.py",
            ),
        )
    )
    catalog = ApiCatalog.build(runtime_release="2024", abqpy_records=records)

    assert catalog.lookup("Spot.__init__").status == "ambiguous"
    for record in records:
        result = catalog.lookup(record.aliases[0])
        assert result.status == "found"
        assert result.cards[0].abqpy_records == (record,)


def test_same_method_name_without_owner_evidence_does_not_cross_merge() -> None:
    official_records = tuple(
        record
        for record in AbaqusScriptingReferenceLoader(FIXTURE_ROOT).load()
        if record.callable_name == "findAt"
    )
    vertex_record = AbqpyApiRecord(
        record_id="abqpy:2024.test:abaqus.BasicGeometry.VertexArray.findAt",
        entity_kind="method",
        declaring_name="VertexArray.findAt",
        class_name="VertexArray",
        method_name="findAt",
        public_names=("VertexArray.findAt",),
        aliases=("abaqus.BasicGeometry.VertexArray.findAt",),
        signatures=(ApiSignature(text="findAt(coordinates)"),),
        source=ApiSourceRef(
            kind="abqpy",
            release="2024",
            version="2024.test",
            source_path="abaqus/BasicGeometry/VertexArray.py",
        ),
    )
    catalog = ApiCatalog.build(
        runtime_release="2024",
        official_records=official_records,
        abqpy_records=(vertex_record,),
    )

    edge = catalog.lookup("EdgeArray.findAt")
    vertex = catalog.lookup("VertexArray.findAt")

    assert edge.status == "found"
    assert edge.cards[0].abqpy_records == ()
    assert [record.owner_name for record in edge.cards[0].official_records] == [
        "EdgeArray"
    ]
    assert vertex.status == "found"
    assert [record.owner_name for record in vertex.cards[0].official_records] == [
        "VertexArray"
    ]


def test_unknown_source_release_does_not_claim_alignment() -> None:
    record = AbaqusScriptingReferenceLoader(FIXTURE_ROOT).load()[0]
    unknown_source = record.source.model_copy(update={"release": "unknown"})
    catalog = ApiCatalog.build(
        runtime_release="2024",
        official_records=(record.model_copy(update={"source": unknown_source}),),
    )

    assert catalog.cards[0].release_alignment == "unknown"


def test_catalog_rejects_runtime_note_for_another_release() -> None:
    note = ApiRuntimeNote(
        note_id="runtime:2026:Part.Set:test",
        qualified_name="Part.Set",
        runtime_release="2026",
        summary="A 2026-only observation.",
        guidance="Do not use this note for 2024.",
        evidence_ref="evidence/2026.json",
        verified=True,
        source=ApiSourceRef(
            kind="runtime",
            release="2026",
            source_path="evidence/2026.json",
        ),
    )

    with pytest.raises(ValueError, match="runtime_release"):
        ApiCatalog.build(runtime_release="2024", runtime_notes=(note,))


def test_parameter_scoped_runtime_note_merges_and_round_trips(tmp_path: Path) -> None:
    note = ApiRuntimeNote(
        note_id="runtime:2024:Part.Set:faces",
        qualified_name="Part.Set",
        runtime_release="2024",
        summary="Verified for faces only.",
        guidance="Pass the native face sequence.",
        evidence_ref="evidence/faces.json",
        applies_to_parameters=("faces",),
        verified=True,
        source=ApiSourceRef(
            kind="runtime",
            release="2024",
            source_path="evidence/faces.json",
        ),
    )
    catalog = ApiCatalog.build(
        runtime_release="2024",
        official_records=AbaqusScriptingReferenceLoader(FIXTURE_ROOT).load(),
        abqpy_records=load_abqpy_records(
            ABQPY_FIXTURE_ROOT,
            version="2024.test",
            release="2024",
        ),
        runtime_notes=(note,),
    )

    assert catalog.lookup("Part.Set").cards[0].runtime_notes == (note,)
    output_dir = tmp_path / "catalog"
    catalog.write(output_dir)
    reopened_note = (
        ApiCatalog.open(output_dir).lookup("Part.Set").cards[0].runtime_notes[0]
    )
    assert reopened_note.applies_to_parameters == ("faces",)
