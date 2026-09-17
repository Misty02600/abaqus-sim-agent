from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from abaqus_sim_agent.api_catalog import ApiCatalog, ApiMethodCard
from abaqus_sim_agent.coding.api_traceback import (
    resolve_odb_runtime_behavior_query,
    resolve_traceback_api_context,
    resolve_traceback_api_query,
)


def _catalog(*names: str) -> ApiCatalog:
    return ApiCatalog(
        [
            ApiMethodCard(
                card_id=f"api:2024:{name}",
                entity_kind="method",
                canonical_name=name,
                declaring_name=name,
                class_name=name.rpartition(".")[0],
                method_name=name.rpartition(".")[2],
            )
            for name in names
        ],
        runtime_release="2024",
    )


def _traceback(script: Path, line: int, error: str) -> str:
    return (
        f"{error}\n"
        f'File "{script.name}", line {line}, in <module>\n'
        "    failing Abaqus call\n"
    )


def test_attribute_error_prefers_exact_runtime_type(tmp_path: Path) -> None:
    catalog = _catalog("Model.FieldOutputRequest")

    query = resolve_traceback_api_query(
        "AttributeError: 'Model' object has no attribute 'FieldOutputRequest'\n",
        workspace=tmp_path,
        catalog=catalog,
    )

    assert query == "Model.FieldOutputRequest"


@pytest.mark.parametrize(
    ("source", "line", "error", "expected"),
    [
        (
            """\
model = mdb.models['Model-1']
model.DisplacementBC(name='BC', region=bad_region, u1=0.0)
""",
            2,
            "TypeError: region; found Edge, expected Region",
            "Model.DisplacementBC",
        ),
        (
            """\
model = mdb.models['Model-1']
assembly = model.rootAssembly
assembly.Set(name='LEFT', edges=left_edge)
""",
            3,
            "TypeError: edges; found Edge, expected tuple",
            "Assembly.Set",
        ),
        (
            """\
model = mdb.models['Model-1']
part = model.Part(name='Plate')
part.edges.findAt(coordinates=((0.0, 0.0, 0.0),))
""",
            3,
            "TypeError: coordinates; invalid coordinates",
            "EdgeArray.findAt",
        ),
    ],
)
def test_type_error_resolves_real_qualified_apis(
    tmp_path: Path,
    source: str,
    line: int,
    error: str,
    expected: str,
) -> None:
    script = tmp_path / "build_model.py"
    script.write_text(source, encoding="utf-8")
    catalog = _catalog(
        "Model.DisplacementBC",
        "Part.Set",
        "Assembly.Set",
        "EdgeArray.findAt",
    )

    query = resolve_traceback_api_query(
        _traceback(script, line, error),
        workspace=tmp_path,
        catalog=catalog,
    )

    assert query == expected


def test_error_parameter_selects_nested_constructor_call(tmp_path: Path) -> None:
    script = tmp_path / "build_model.py"
    script.write_text(
        """\
model = mdb.models['Model-1']
model.DisplacementBC(
    name='BC',
    region=regionToolset.Region(edges=bad_edges),
    u1=0.0,
)
""",
        encoding="utf-8",
    )
    catalog = _catalog("Model.DisplacementBC", "Region.__init__")

    query = resolve_traceback_api_query(
        _traceback(
            script,
            2,
            "TypeError: edges; found Edge, expected tuple",
        ),
        workspace=tmp_path,
        catalog=catalog,
    )

    assert query == "Region.__init__"


def test_part_instance_array_projection_is_conservative(tmp_path: Path) -> None:
    script = tmp_path / "build_model.py"
    script.write_text(
        """\
model = mdb.models['Model-1']
assembly = model.rootAssembly
instance = assembly.instances['Plate-1']
instance.vertices.findAt(coordinates=((0.0, 0.0, 0.0),))
""",
        encoding="utf-8",
    )
    catalog = _catalog("VertexArray.findAt")

    query = resolve_traceback_api_query(
        _traceback(script, 4, "TypeError: coordinates; invalid coordinates"),
        workspace=tmp_path,
        catalog=catalog,
    )

    assert query == "VertexArray.findAt"


@pytest.mark.parametrize("failure", ["missing", "escape", "syntax"])
def test_unsafe_or_unreadable_traceback_never_guesses(
    tmp_path: Path,
    failure: str,
) -> None:
    catalog = _catalog("Part.Set")
    if failure == "missing":
        stderr = (
            'TypeError: edges; invalid value\nFile "missing.py", line 1, in <module>\n'
        )
    elif failure == "escape":
        outside = tmp_path.parent / "outside.py"
        outside.write_text("part.Set(name='X', edges=bad)\n", encoding="utf-8")
        stderr = (
            f'TypeError: edges; invalid value\nFile "{outside}", line 1, in <module>\n'
        )
    else:
        script = tmp_path / "broken.py"
        script.write_text("part.Set(\n", encoding="utf-8")
        stderr = _traceback(script, 1, "TypeError: edges; invalid value")

    query = resolve_traceback_api_query(
        stderr,
        workspace=tmp_path,
        catalog=catalog,
    )

    assert query is None


def test_unqualified_or_non_unique_catalog_result_returns_none(tmp_path: Path) -> None:
    script = tmp_path / "build_model.py"
    script.write_text(
        "unknown.Set(name='X', edges=bad)\n",
        encoding="utf-8",
    )
    catalog = _catalog("Part.Set", "Assembly.Set")

    query = resolve_traceback_api_query(
        _traceback(script, 1, "TypeError: edges; invalid value"),
        workspace=tmp_path,
        catalog=catalog,
    )

    assert query is None


@pytest.mark.parametrize(
    ("error", "expected_parameter"),
    [
        ("TypeError: edges[0]; found tuple, expecting Edge", "edges"),
        (
            "TypeError: Set() got an unexpected keyword argument 'regions'",
            "regions",
        ),
    ],
)
def test_context_normalizes_error_parameter_and_exposes_call_keywords(
    tmp_path: Path,
    error: str,
    expected_parameter: str,
) -> None:
    script = tmp_path / "build_model.py"
    script.write_text(
        "model = mdb.models['Model-1']\n"
        "assembly = model.rootAssembly\n"
        "assembly.Set(name='REGION', faces=faces, edges=edges, regions=regions)\n",
        encoding="utf-8",
    )

    resolution = resolve_traceback_api_context(
        _traceback(script, 3, error),
        workspace=tmp_path,
    )

    assert resolution is not None
    assert resolution.qualified_query == "Assembly.Set"
    assert resolution.error_parameter == expected_parameter
    assert resolution.call_keywords == ("name", "faces", "edges", "regions")
    assert resolution.error_kind == "type_error"


def test_context_routes_localized_beam_section_keyword_error(
    tmp_path: Path,
) -> None:
    parameter = "orientation"
    script = tmp_path / "build_model.py"
    script.write_text(
        "model = mdb.models['Model-1']\n"
        "model.BeamSection(name='Section', profile='Profile', "
        f"material='Steel', {parameter}=(0.0, 1.0, 0.0))\n",
        encoding="utf-8",
    )

    resolution = resolve_traceback_api_context(
        _traceback(script, 2, f"TypeError: 关键字错误: {parameter}"),
        workspace=tmp_path,
    )

    assert resolution is not None
    assert resolution.qualified_query == "Model.BeamSection"
    assert resolution.error_parameter == parameter
    assert resolution.call_keywords == (
        "name",
        "profile",
        "material",
        parameter,
    )
    assert resolution.error_kind == "type_error"


def test_abaqus_exception_routes_single_scoped_call(
    tmp_path: Path,
) -> None:
    error = "特征创建失败."
    script = tmp_path / "build_model.py"
    script.write_text(
        "model = mdb.models['Model-1']\n"
        "assembly = model.rootAssembly\n"
        "assembly.Surface(name='SIDE', side1Edges=edges)\n",
        encoding="utf-8",
    )

    resolution = resolve_traceback_api_context(
        _traceback(
            script,
            3,
            error,
        ),
        workspace=tmp_path,
    )

    assert resolution is not None
    assert resolution.qualified_query == "Assembly.Surface"
    assert resolution.error_parameter is None
    assert resolution.call_keywords == ("name", "side1Edges")
    assert resolution.error_kind == "abaqus_exception"


def test_attribute_error_context_preserves_nonexistent_qualified_query(
    tmp_path: Path,
) -> None:
    resolution = resolve_traceback_api_context(
        "AttributeError: 'Part' object has no attribute 'BaseSolidBox'\n",
        workspace=tmp_path,
    )

    assert resolution is not None
    assert resolution.qualified_query == "Part.BaseSolidBox"
    assert resolution.error_parameter is None
    assert resolution.call_keywords == ()
    assert resolution.error_kind == "attribute_error"
    assert (
        resolve_traceback_api_query(
            "AttributeError: 'Part' object has no attribute 'BaseSolidBox'\n",
            workspace=tmp_path,
            catalog=_catalog("Part.BaseSolidExtrude"),
        )
        is None
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            "AttributeError: 'FieldOutput' object has no attribute 'position'",
            "FieldOutput.getSubset",
        ),
        (
            "AttributeError: 'FieldValue' object has no attribute 'componentLabels'",
            "FieldOutput.getSubset",
        ),
        (
            "OdbError: Slice for an FieldValue Sequence  is not supported.",
            "FieldOutput.getSubset",
        ),
        (
            "AttributeError: 'OdbMeshNodeArray' object has no attribute 'label'",
            "openOdb",
        ),
    ],
)
def test_verified_odb_behavior_errors_route_to_exact_cards(
    error: str,
    expected: str,
) -> None:
    assert resolve_odb_runtime_behavior_query(error) == expected


def test_unrelated_odb_error_has_no_behavior_route() -> None:
    assert resolve_odb_runtime_behavior_query("KeyError: 'RIGHT_END'") is None
    assert (
        resolve_odb_runtime_behavior_query(
            "AttributeError: 'MeshNodeArray' object has no attribute 'findAt'"
        )
        is None
    )


def test_untyped_runtime_failure_routes_only_with_matching_executed_source(tmp_path):
    script = tmp_path / "build_model.py"
    script.write_text(
        "model = mdb.Model(name='M')\npart = model.Part(name='P')\n"
        "part.setElementType(regions=(part.cells,), elemTypes=())\n",
        encoding="utf-8",
    )
    fingerprints = {script.name: hashlib.sha256(script.read_bytes()).hexdigest()}
    stderr = _traceback(
        script,
        3,
        "Error: regions must have the same dimension for element type assignment",
    )
    assert resolve_traceback_api_context(stderr, workspace=tmp_path) is None
    resolution = resolve_traceback_api_context(
        stderr, workspace=tmp_path, runtime_failed=True, input_fingerprints=fingerprints
    )
    assert resolution is not None
    assert resolution.qualified_query == "Part.setElementType"
    assert resolution.call_keywords == ("regions", "elemTypes")
    assert resolution.error_kind == "runtime_failure"
    for kwargs in (
        {"runtime_failed": False, "input_fingerprints": fingerprints},
        {"runtime_failed": True},
        {"runtime_failed": True, "input_fingerprints": {script.name: "0" * 64}},
    ):
        assert (
            resolve_traceback_api_context(stderr, workspace=tmp_path, **kwargs) is None
        )


@pytest.mark.parametrize(
    ("source", "frame"),
    [
        ("part.setElementType(regions=()); part.Set(name='x')", "build_model.py"),
        ("part.setElementType(regions=())", "../outside.py"),
    ],
)
def test_untyped_failure_does_not_guess_ambiguous_unknown_or_external_calls(
    tmp_path, source, frame
):
    script = tmp_path / "build_model.py"
    script.write_text(
        "model = mdb.Model(name='M')\npart = model.Part(name='P')\n" + source,
        encoding="utf-8",
    )
    stderr = f'错误: operation failed\nFile "{frame}", line 3, in <module>\n'
    assert (
        resolve_traceback_api_context(
            stderr,
            workspace=tmp_path,
            runtime_failed=True,
            input_fingerprints={
                script.name: hashlib.sha256(script.read_bytes()).hexdigest()
            },
        )
        is None
    )


def test_untyped_multiline_frame_uses_call_on_reported_line_not_later_constructor(
    tmp_path,
):
    script = tmp_path / "build_model.py"
    script.write_text(
        "model = mdb.Model(name='M')\npart = model.Part(name='P')\n"
        "part.setElementType(\n    regions=(part.cells,),\n"
        "    elemTypes=(mesh.ElemType(elemCode=CPS4R),),\n)\n",
        encoding="utf-8",
    )
    fingerprints = {script.name: hashlib.sha256(script.read_bytes()).hexdigest()}
    result = resolve_traceback_api_context(
        _traceback(script, 3, "错误: dimension mismatch"),
        workspace=tmp_path,
        runtime_failed=True,
        input_fingerprints=fingerprints,
    )
    assert result is not None
    assert result.qualified_query == "Part.setElementType"
    # On the nested-call line there is not enough location information to choose.
    assert (
        resolve_traceback_api_context(
            _traceback(script, 5, "错误: operation failed"),
            workspace=tmp_path,
            runtime_failed=True,
            input_fingerprints=fingerprints,
        )
        is None
    )
