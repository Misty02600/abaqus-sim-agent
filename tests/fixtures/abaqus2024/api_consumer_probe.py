"""Probe difficult Abaqus 2024 API consumer bindings without solving."""

import json
import os
import platform
import traceback

import mesh
import section  # noqa: F401  # Registers profile/section APIs on Model.
import step  # noqa: F401  # Registers step/output APIs on Model.
from abaqus import mdb
from abaqusConstants import (
    B31,
    CARTESIAN,
    CPS4R,
    DEFORMABLE_BODY,
    DURING_ANALYSIS,
    FIXED,
    LINEAR,
    N1_COSINES,
    ON,
    STANDARD,
    THREE_D,
    TWO_D_PLANAR,
)

OUTPUT_PATH = os.path.join(os.getcwd(), "api_consumer_probe_results.json")

results = {
    "schema_version": "1",
    "probe": "Abaqus 2024 difficult API consumer bindings",
    "python": platform.python_version(),
    "cases": [],
    "completed": False,
}


def persist():
    with open(OUTPUT_PATH, "w", encoding="utf-8") as stream:
        json.dump(results, stream, ensure_ascii=True, indent=2, sort_keys=True)


def describe(value):
    description = {
        "type_name": type(value).__name__,
        "type_module": type(value).__module__,
        "repr": repr(value),
    }
    try:
        description["length"] = len(value)
    except Exception as exc:
        description["length_error"] = f"{type(exc).__name__}: {exc}"
    return description


def run_case(name, function):
    record = {"name": name}
    try:
        record["status"] = "ok"
        record["observation"] = function()
    except BaseException as exc:
        record["status"] = "error"
        record["error_type"] = type(exc).__name__
        record["error"] = str(exc)
        record["traceback"] = traceback.format_exc()
    results["cases"].append(record)
    persist()


def fresh_model(name):
    if name in mdb.models:
        del mdb.models[name]
    return mdb.Model(name=name)


def make_shell_part(model, name):
    sketch = model.ConstrainedSketch(name=name + "Sketch", sheetSize=20.0)
    sketch.rectangle(point1=(0.0, 0.0), point2=(10.0, 2.0))
    part = model.Part(
        name=name,
        dimensionality=TWO_D_PLANAR,
        type=DEFORMABLE_BODY,
    )
    part.BaseShell(sketch=sketch)
    del model.sketches[name + "Sketch"]
    return part


def set_element_type_case(model_name, selected_kind):
    model = fresh_model(model_name)
    part = make_shell_part(model, model_name + "Part")
    faces = part.faces[:]
    face_set = part.Set(name="ALL_FACES", faces=faces)
    if selected_kind == "direct_faces":
        selected = faces
    elif selected_kind == "tuple_faces":
        selected = (faces,)
    elif selected_kind == "nested_tuple_faces":
        selected = ((faces,),)
    elif selected_kind == "direct_set":
        selected = face_set
    elif selected_kind == "tuple_set":
        selected = (face_set,)
    else:
        raise ValueError(f"unknown selected_kind: {selected_kind}")
    element_type = mesh.ElemType(elemCode=CPS4R, elemLibrary=STANDARD)
    part.setElementType(regions=selected, elemTypes=(element_type,))
    return {
        "input": describe(selected),
        "face_count": len(part.faces),
    }


def make_beam_part(model, name):
    sketch = model.ConstrainedSketch(name=name + "Sketch", sheetSize=20.0)
    sketch.Line(point1=(0.0, 0.0), point2=(10.0, 0.0))
    part = model.Part(
        name=name,
        dimensionality=THREE_D,
        type=DEFORMABLE_BODY,
    )
    part.BaseWire(sketch=sketch)
    del model.sketches[name + "Sketch"]
    material = model.Material(name="Steel")
    material.Elastic(table=((210000.0, 0.3),))
    model.RectangularProfile(name="Profile", a=1.0, b=1.0)
    model.BeamSection(
        name="Section",
        integration=DURING_ANALYSIS,
        profile="Profile",
        material="Steel",
        poissonRatio=0.3,
        temperatureVar=LINEAR,
    )
    edge_set = part.Set(name="ALL_EDGES", edges=part.edges[:])
    part.SectionAssignment(region=edge_set, sectionName="Section")
    return part, edge_set


def beam_orientation_case(model_name, selected_kind):
    model = fresh_model(model_name)
    part, edge_set = make_beam_part(model, model_name + "Part")
    edges = part.edges[:]
    if selected_kind == "direct_edges":
        selected = edges
    elif selected_kind == "tuple_edges":
        selected = (edges,)
    elif selected_kind == "direct_set":
        selected = edge_set
    elif selected_kind == "single_edge":
        selected = part.edges[0]
    else:
        raise ValueError(f"unknown selected_kind: {selected_kind}")
    part.assignBeamSectionOrientation(
        region=selected,
        method=N1_COSINES,
        n1=(0.0, 1.0, 0.0),
    )
    return {"input": describe(selected)}


def assembly_surface_case(model_name, selected_kind, surface_parameter):
    model = fresh_model(model_name)
    part = make_shell_part(model, model_name + "Part")
    assembly = model.rootAssembly
    assembly.DatumCsysByDefault(coordSysType=CARTESIAN)
    instance = assembly.Instance(
        name="PROBE-1",
        part=part,
        dependent=ON,
    )
    native = instance.edges.findAt(
        ((5.0, 0.0, 0.0),),
        ((5.0, 2.0, 0.0),),
    )
    bottom = instance.edges.findAt((5.0, 0.0, 0.0))
    top = instance.edges.findAt((5.0, 2.0, 0.0))
    if selected_kind == "native":
        selected = native
    elif selected_kind == "flat_tuple":
        selected = (bottom, top)
    elif selected_kind == "flat_list":
        selected = [bottom, top]
    elif selected_kind == "tuple_native":
        selected = (native,)
    else:
        raise ValueError(f"unknown selected_kind: {selected_kind}")
    created = assembly.Surface(name="PROBE_SURFACE", **{surface_parameter: selected})
    return {
        "input": describe(selected),
        "created": describe(created),
        "repository_contains_surface": "PROBE_SURFACE" in assembly.surfaces,
        "surface_count": len(assembly.surfaces),
    }


def beam_section_constructor_case(model_name, selected_kind):
    model = fresh_model(model_name)
    material = model.Material(name="Steel")
    material.Elastic(table=((210000.0, 0.3),))
    model.RectangularProfile(name="Profile", a=1.0, b=1.0)
    arguments = {
        "name": "Section",
        "integration": DURING_ANALYSIS,
        "profile": "Profile",
        "material": "Steel",
        "poissonRatio": 0.3,
        "temperatureVar": LINEAR,
    }
    if selected_kind == "orientation_keyword":
        arguments["orientation"] = (0.0, 1.0, 0.0)
    elif selected_kind == "geometric_type_keyword":
        arguments["geometricType"] = "CONSTANT"
    elif selected_kind != "valid":
        raise ValueError(f"unknown selected_kind: {selected_kind}")
    created = model.BeamSection(**arguments)
    return {
        "created": describe(created),
        "section_names": list(model.sections.keys()),
    }


def field_output_variables_case(model_name, variables):
    model = fresh_model(model_name)
    part, _edge_set = make_beam_part(model, model_name + "Part")
    part.assignBeamSectionOrientation(
        region=part.edges[:],
        method=N1_COSINES,
        n1=(0.0, 1.0, 0.0),
    )
    part.seedEdgeByNumber(edges=part.edges[:], number=2, constraint=FIXED)
    element_type = mesh.ElemType(elemCode=B31, elemLibrary=STANDARD)
    part.setElementType(regions=(part.edges[:],), elemTypes=(element_type,))
    part.generateMesh()
    model.StaticStep(name="Step-1", previous="Initial")
    model.FieldOutputRequest(
        name="ProbeOutput",
        createStepName="Step-1",
        variables=variables,
    )
    return {
        "variables": list(variables),
        "request_names": list(model.fieldOutputRequests.keys()),
    }


def main():
    set_element_cases = (
        ("direct_faces", "SetElementDirectFaces"),
        ("tuple_faces", "SetElementTupleFaces"),
        ("nested_tuple_faces", "SetElementNestedTupleFaces"),
        ("direct_set", "SetElementDirectSet"),
        ("tuple_set", "SetElementTupleSet"),
    )
    for selected_kind, model_name in set_element_cases:
        run_case(
            f"Part.setElementType.{selected_kind}",
            lambda selected_kind=selected_kind, model_name=model_name: (
                set_element_type_case(model_name, selected_kind)
            ),
        )

    orientation_cases = (
        ("direct_edges", "OrientationDirectEdges"),
        ("tuple_edges", "OrientationTupleEdges"),
        ("direct_set", "OrientationDirectSet"),
        ("single_edge", "OrientationSingleEdge"),
    )
    for selected_kind, model_name in orientation_cases:
        run_case(
            f"Part.assignBeamSectionOrientation.{selected_kind}",
            lambda selected_kind=selected_kind, model_name=model_name: (
                beam_orientation_case(model_name, selected_kind)
            ),
        )

    surface_cases = (
        ("native", "SurfaceNative"),
        ("flat_tuple", "SurfaceFlatTuple"),
        ("flat_list", "SurfaceFlatList"),
        ("tuple_native", "SurfaceTupleNative"),
    )
    for surface_parameter in ("side1Edges", "side2Edges"):
        for selected_kind, model_name in surface_cases:
            qualified_model_name = surface_parameter + model_name
            run_case(
                f"Assembly.Surface.{surface_parameter}_{selected_kind}",
                lambda selected_kind=selected_kind, model_name=qualified_model_name, surface_parameter=surface_parameter: (
                    assembly_surface_case(
                        model_name,
                        selected_kind,
                        surface_parameter,
                    )
                ),
            )

    for selected_kind in (
        "valid",
        "orientation_keyword",
        "geometric_type_keyword",
    ):
        model_name = f"BeamSection{selected_kind.title()}"
        run_case(
            f"Model.BeamSection.{selected_kind}",
            lambda selected_kind=selected_kind, model_name=model_name: (
                beam_section_constructor_case(model_name, selected_kind)
            ),
        )

    output_cases = (
        (("U", "RF", "SF"), "OutputURFSF"),
        (("U", "RF", "SF", "RM"), "OutputURFSFRM"),
        (("U", "RF", "SF", "SM"), "OutputURFSFSM"),
        (("U", "RF", "SF", "RM", "SM"), "OutputURFSFRMSM"),
    )
    for variables, model_name in output_cases:
        run_case(
            f"Model.FieldOutputRequest.variables_{'_'.join(variables)}",
            lambda variables=variables, model_name=model_name: (
                field_output_variables_case(model_name, variables)
            ),
        )

    results["completed"] = True
    persist()
    print("Wrote api_consumer_probe_results.json")


if __name__ == "__main__":
    main()
