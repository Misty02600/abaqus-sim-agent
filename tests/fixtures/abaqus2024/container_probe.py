"""Probe Abaqus 2024 native geometry-container behavior without solving."""

import json
import os
import platform
import traceback

from abaqus import mdb
from abaqusConstants import (
    CARTESIAN,
    DEFORMABLE_BODY,
    ELEM_SHAPE,
    FIXED,
    NUMBER,
    ON,
    QUAD,
    STRUCTURED,
    TECHNIQUE,
    TWO_D_PLANAR,
)
from caeModules import *  # noqa: F403

MODEL_NAME = "ContainerProbe"
OUTPUT_PATH = os.path.join(os.getcwd(), "container_probe_results.json")

results = {
    "schema_version": "1",
    "probe": "Abaqus 2024 geometry-container consumers",
    "python": platform.python_version(),
    "cases": [],
    "completed": False,
}


def persist():
    with open(OUTPUT_PATH, "w", encoding="utf-8") as stream:
        json.dump(results, stream, ensure_ascii=True, indent=2, sort_keys=True)


def describe_container(value):
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
        observation = function()
        record["status"] = "ok"
        record["observation"] = observation
    except BaseException as exc:
        record["status"] = "error"
        record["error_type"] = type(exc).__name__
        record["error"] = str(exc)
        record["traceback"] = traceback.format_exc()
    results["cases"].append(record)
    persist()


def make_rectangle_part(model, name):
    sketch_name = name + "Profile"
    sketch = model.ConstrainedSketch(name=sketch_name, sheetSize=20.0)
    sketch.rectangle(point1=(0.0, 0.0), point2=(10.0, 2.0))
    part = model.Part(
        name=name,
        dimensionality=TWO_D_PLANAR,
        type=DEFORMABLE_BODY,
    )
    part.BaseShell(sketch=sketch)
    del model.sketches[sketch_name]
    return part


def edge_selection(owner):
    bottom_point = (5.0, 0.0, 0.0)
    top_point = (5.0, 2.0, 0.0)
    native = owner.edges.findAt((bottom_point,), (top_point,))
    bottom = owner.edges.findAt(bottom_point)
    top = owner.edges.findAt(top_point)
    return native, bottom, top


def assembly_set_native(assembly, instance):
    native, _, _ = edge_selection(instance)
    created = assembly.Set(name="ASSEMBLY_NATIVE", edges=native)
    return {
        "input": describe_container(native),
        "created_edge_count": len(created.edges),
    }


def assembly_set_flat_tuple(assembly, instance):
    _, bottom, top = edge_selection(instance)
    assembly.Set(name="ASSEMBLY_FLAT_TUPLE", edges=(bottom, top))


def assembly_set_flat_list(assembly, instance):
    _, bottom, top = edge_selection(instance)
    assembly.Set(name="ASSEMBLY_FLAT_LIST", edges=[bottom, top])


def seed_case(model, name, selected_kind, number):
    part = make_rectangle_part(model, name)
    native, bottom, top = edge_selection(part)
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

    part.seedEdgeByNumber(edges=selected, number=number, constraint=FIXED)
    return {
        "input": describe_container(selected),
        "requested_number": number,
        "bottom_number": part.getEdgeSeeds(edge=bottom, attribute=NUMBER),
        "top_number": part.getEdgeSeeds(edge=top, attribute=NUMBER),
    }


def mesh_controls_direct(model):
    part = make_rectangle_part(model, "MeshControlsDirect")
    faces = part.faces[:]
    part.setMeshControls(
        regions=faces,
        elemShape=QUAD,
        technique=STRUCTURED,
    )
    return {
        "input": describe_container(faces),
        "elem_shape": str(
            part.getMeshControl(region=part.faces[0], attribute=ELEM_SHAPE)
        ),
        "technique": str(
            part.getMeshControl(region=part.faces[0], attribute=TECHNIQUE)
        ),
    }


def mesh_controls_tuple_native(model):
    part = make_rectangle_part(model, "MeshControlsTupleNative")
    faces = part.faces[:]
    part.setMeshControls(
        regions=(faces,),
        elemShape=QUAD,
        technique=STRUCTURED,
    )


def main():
    if MODEL_NAME in mdb.models:
        del mdb.models[MODEL_NAME]
    model = mdb.Model(name=MODEL_NAME)

    assembly_part = make_rectangle_part(model, "AssemblySetPart")
    assembly = model.rootAssembly
    assembly.DatumCsysByDefault(coordSysType=CARTESIAN)
    instance = assembly.Instance(
        name="ASSEMBLY-SET-1",
        part=assembly_part,
        dependent=ON,
    )

    run_case(
        "Assembly.Set.native_sequence",
        lambda: assembly_set_native(assembly, instance),
    )
    run_case(
        "Assembly.Set.flat_edge_tuple",
        lambda: assembly_set_flat_tuple(assembly, instance),
    )
    run_case(
        "Assembly.Set.flat_edge_list",
        lambda: assembly_set_flat_list(assembly, instance),
    )

    run_case(
        "Part.seedEdgeByNumber.native_sequence",
        lambda: seed_case(model, "SeedNative", "native", 3),
    )
    run_case(
        "Part.seedEdgeByNumber.flat_edge_tuple",
        lambda: seed_case(model, "SeedTuple", "flat_tuple", 4),
    )
    run_case(
        "Part.seedEdgeByNumber.flat_edge_list",
        lambda: seed_case(model, "SeedList", "flat_list", 5),
    )
    run_case(
        "Part.seedEdgeByNumber.tuple_native_sequence",
        lambda: seed_case(model, "SeedTupleNative", "tuple_native", 6),
    )

    run_case(
        "Part.setMeshControls.direct_face_array",
        lambda: mesh_controls_direct(model),
    )
    run_case(
        "Part.setMeshControls.tuple_face_array",
        lambda: mesh_controls_tuple_native(model),
    )

    results["completed"] = True
    persist()
    print("Wrote container_probe_results.json")


if __name__ == "__main__":
    main()
