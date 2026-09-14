"""Probe Abaqus 2024 Region, Set, and geometry-array consumer ownership."""

import json
import os
import platform
import traceback

import load  # noqa: F401  # Registers load APIs on Model.
import mesh
import regionToolset
import section  # noqa: F401  # Registers section APIs on Model.
import step  # noqa: F401  # Registers step/BC/output APIs on Model.
from abaqus import mdb
from abaqusConstants import (
    B31,
    C3D8R,
    CARTESIAN,
    DEFORMABLE_BODY,
    DURING_ANALYSIS,
    LINEAR,
    N1_COSINES,
    ON,
    STANDARD,
    THREE_D,
    TWO_D_PLANAR,
)

OUTPUT_PATH = os.path.join(os.getcwd(), "api_region_consumer_probe_results.json")

results = {
    "schema_version": "1",
    "probe": "Abaqus 2024 Region/Set/GeomSequence consumers",
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


def add_material(model):
    material = model.Material(name="Steel")
    material.Elastic(table=((210000.0, 0.3),))


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


def make_solid_part(model, name):
    sketch = model.ConstrainedSketch(name=name + "Sketch", sheetSize=20.0)
    sketch.rectangle(point1=(0.0, 0.0), point2=(10.0, 2.0))
    part = model.Part(
        name=name,
        dimensionality=THREE_D,
        type=DEFORMABLE_BODY,
    )
    part.BaseSolidExtrude(sketch=sketch, depth=1.0)
    del model.sketches[name + "Sketch"]
    return part


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
    add_material(model)
    model.RectangularProfile(name="Profile", a=1.0, b=1.0)
    model.BeamSection(
        name="BeamSection",
        integration=DURING_ANALYSIS,
        profile="Profile",
        material="Steel",
        poissonRatio=0.3,
        temperatureVar=LINEAR,
    )
    edge_set = part.Set(name="ALL_EDGES", edges=part.edges[:])
    part.SectionAssignment(region=edge_set, sectionName="BeamSection")
    part.assignBeamSectionOrientation(
        region=part.edges[:],
        method=N1_COSINES,
        n1=(0.0, 1.0, 0.0),
    )
    return part, edge_set


def set_element_type_case(model_name, dimension, selected_kind):
    model = fresh_model(model_name)
    if dimension == "solid":
        part = make_solid_part(model, model_name + "Part")
        geometry = part.cells[:]
        region_set = part.Set(name="ALL", cells=geometry)
        elem_code = C3D8R
    elif dimension == "beam":
        part, region_set = make_beam_part(model, model_name + "Part")
        geometry = part.edges[:]
        elem_code = B31
    else:
        raise ValueError(f"unknown dimension: {dimension}")
    if selected_kind == "direct_array":
        selected = geometry
    elif selected_kind == "tuple_array":
        selected = (geometry,)
    elif selected_kind == "direct_set":
        selected = region_set
    elif selected_kind == "tuple_set":
        selected = (region_set,)
    else:
        raise ValueError(f"unknown selected_kind: {selected_kind}")
    element_type = mesh.ElemType(elemCode=elem_code, elemLibrary=STANDARD)
    part.setElementType(regions=selected, elemTypes=(element_type,))
    return {"input": describe(selected), "dimension": dimension}


def section_assignment_case(model_name, selected_kind):
    model = fresh_model(model_name)
    part = make_shell_part(model, model_name + "Part")
    add_material(model)
    model.HomogeneousSolidSection(
        name="SolidSection",
        material="Steel",
        thickness=1.0,
    )
    faces = part.faces[:]
    face_set = part.Set(name="ALL_FACES", faces=faces)
    if selected_kind == "direct_set":
        selected = face_set
    elif selected_kind == "tuple_set":
        selected = (face_set,)
    elif selected_kind == "raw_faces":
        selected = faces
    elif selected_kind == "region":
        selected = regionToolset.Region(faces=faces)
    else:
        raise ValueError(f"unknown selected_kind: {selected_kind}")
    assignment = part.SectionAssignment(
        region=selected,
        sectionName="SolidSection",
    )
    return {
        "input": describe(selected),
        "assignment_section": assignment.sectionName,
    }


def make_assembly_vertex_region(model, part, selected_kind):
    assembly = model.rootAssembly
    assembly.DatumCsysByDefault(coordSysType=CARTESIAN)
    instance = assembly.Instance(name="PROBE-1", part=part, dependent=ON)
    native = instance.vertices.findAt(((0.0, 0.0, 0.0),))
    assembly_set = assembly.Set(name="ASSEMBLY_VERTEX", vertices=native)
    part_set = part.Set(
        name="PART_VERTEX",
        vertices=part.vertices.findAt(((0.0, 0.0, 0.0),)),
    )
    if selected_kind == "assembly_set":
        selected = assembly_set
    elif selected_kind == "raw_vertices":
        selected = native
    elif selected_kind == "assembly_region":
        selected = regionToolset.Region(vertices=native)
    elif selected_kind == "part_set":
        selected = part_set
    else:
        raise ValueError(f"unknown selected_kind: {selected_kind}")
    return selected


def displacement_bc_case(model_name, selected_kind):
    model = fresh_model(model_name)
    part = make_shell_part(model, model_name + "Part")
    model.StaticStep(name="Step-1", previous="Initial")
    selected = make_assembly_vertex_region(model, part, selected_kind)
    model.DisplacementBC(
        name="ProbeBC",
        createStepName="Step-1",
        region=selected,
        u1=0.0,
    )
    return {
        "input": describe(selected),
        "bc_names": list(model.boundaryConditions.keys()),
    }


def concentrated_force_case(model_name, selected_kind):
    model = fresh_model(model_name)
    part = make_shell_part(model, model_name + "Part")
    model.StaticStep(name="Step-1", previous="Initial")
    selected = make_assembly_vertex_region(model, part, selected_kind)
    model.ConcentratedForce(
        name="ProbeLoad",
        createStepName="Step-1",
        region=selected,
        cf1=1.0,
    )
    return {
        "input": describe(selected),
        "load_names": list(model.loads.keys()),
    }


def assembly_face_surface_case(model_name, selected_kind, surface_parameter):
    model = fresh_model(model_name)
    part = make_solid_part(model, model_name + "Part")
    assembly = model.rootAssembly
    assembly.DatumCsysByDefault(coordSysType=CARTESIAN)
    instance = assembly.Instance(name="PROBE-1", part=part, dependent=ON)
    native = instance.faces.findAt(((5.0, 1.0, 1.0),))
    face = instance.faces.findAt((5.0, 1.0, 1.0))
    if selected_kind == "native":
        selected = native
    elif selected_kind == "flat_tuple":
        selected = (face,)
    elif selected_kind == "flat_list":
        selected = [face]
    elif selected_kind == "tuple_native":
        selected = (native,)
    else:
        raise ValueError(f"unknown selected_kind: {selected_kind}")
    created = assembly.Surface(name="PROBE_SURFACE", **{surface_parameter: selected})
    return {
        "input": describe(selected),
        "created": describe(created),
        "repository_contains_surface": "PROBE_SURFACE" in assembly.surfaces,
    }


def main():
    for dimension in ("solid", "beam"):
        for selected_kind in (
            "direct_array",
            "tuple_array",
            "direct_set",
            "tuple_set",
        ):
            name = f"Part.setElementType.{dimension}_{selected_kind}"
            model_name = f"SetType{dimension.title()}{selected_kind.title()}"
            run_case(
                name,
                lambda dimension=dimension, selected_kind=selected_kind, model_name=model_name: (
                    set_element_type_case(
                        model_name,
                        dimension,
                        selected_kind,
                    )
                ),
            )

    for selected_kind in ("direct_set", "tuple_set", "raw_faces", "region"):
        model_name = f"SectionAssignment{selected_kind.title()}"
        run_case(
            f"Part.SectionAssignment.{selected_kind}",
            lambda selected_kind=selected_kind, model_name=model_name: (
                section_assignment_case(model_name, selected_kind)
            ),
        )

    for selected_kind in (
        "assembly_set",
        "raw_vertices",
        "assembly_region",
        "part_set",
    ):
        bc_model = f"DisplacementBC{selected_kind.title()}"
        run_case(
            f"Model.DisplacementBC.region_{selected_kind}",
            lambda selected_kind=selected_kind, model_name=bc_model: (
                displacement_bc_case(model_name, selected_kind)
            ),
        )
        load_model = f"ConcentratedForce{selected_kind.title()}"
        run_case(
            f"Model.ConcentratedForce.region_{selected_kind}",
            lambda selected_kind=selected_kind, model_name=load_model: (
                concentrated_force_case(model_name, selected_kind)
            ),
        )

    for surface_parameter in ("side1Faces", "side2Faces"):
        for selected_kind in (
            "native",
            "flat_tuple",
            "flat_list",
            "tuple_native",
        ):
            model_name = f"{surface_parameter}FaceSurface{selected_kind.title()}"
            run_case(
                f"Assembly.Surface.{surface_parameter}_{selected_kind}",
                lambda selected_kind=selected_kind, model_name=model_name, surface_parameter=surface_parameter: (
                    assembly_face_surface_case(
                        model_name,
                        selected_kind,
                        surface_parameter,
                    )
                ),
            )

    results["completed"] = True
    persist()
    print("Wrote api_region_consumer_probe_results.json")


if __name__ == "__main__":
    main()
