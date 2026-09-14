"""Probe the region binding required by Model.SurfaceTraction in Abaqus 2024."""

import json

from abaqus import mdb
from abaqusConstants import (
    DEFORMABLE_BODY,
    GENERAL,
    OFF,
    ON,
    STANDARD_EXPLICIT,
    TWO_D_PLANAR,
)
from caeModules import *  # noqa: F403


def _error_payload(exc):
    return {
        "status": "error",
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


payload = {
    "schema_version": "1",
    "runtime_release": "2024",
    "completed": False,
    "cases": {},
}

try:
    model = mdb.Model(name="SurfaceTractionRegionProbe", modelType=STANDARD_EXPLICIT)
    sketch = model.ConstrainedSketch(name="__profile__", sheetSize=200.0)
    sketch.rectangle(point1=(0.0, 0.0), point2=(100.0, 20.0))
    part = model.Part(
        name="Plate",
        dimensionality=TWO_D_PLANAR,
        type=DEFORMABLE_BODY,
    )
    part.BaseShell(sketch=sketch)
    assembly = model.rootAssembly
    instance = assembly.Instance(name="Plate-1", part=part, dependent=ON)
    right_edges = instance.edges.findAt(((100.0, 10.0, 0.0),))
    edge_set = assembly.Set(name="RightEdgeSet", edges=right_edges)
    model.StaticStep(name="Step-1", previous="Initial", nlgeom=OFF)

    try:
        model.SurfaceTraction(
            name="SetRegionLoad",
            createStepName="Step-1",
            region=edge_set,
            magnitude=210.0,
            directionVector=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            traction=GENERAL,
            follower=OFF,
            resultant=OFF,
        )
    except Exception as exc:
        payload["cases"]["assembly_set_region"] = _error_payload(exc)
    else:
        payload["cases"]["assembly_set_region"] = {
            "status": "ok",
            "load_created": "SetRegionLoad" in model.loads,
        }

    side_surface = assembly.Surface(name="RightSideSurface", side1Edges=right_edges)
    try:
        model.SurfaceTraction(
            name="SurfaceRegionLoad",
            createStepName="Step-1",
            region=side_surface,
            magnitude=210.0,
            directionVector=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            traction=GENERAL,
            follower=OFF,
            resultant=OFF,
        )
    except Exception as exc:
        payload["cases"]["assembly_surface_region"] = _error_payload(exc)
    else:
        payload["cases"]["assembly_surface_region"] = {
            "status": "ok",
            "load_created": "SurfaceRegionLoad" in model.loads,
            "surface_created": "RightSideSurface" in assembly.surfaces,
            "surface_side": "SIDE1",
        }
    payload["completed"] = True
except Exception as exc:
    payload["setup_error"] = _error_payload(exc)

with open("surface_traction_region_probe_results.json", "w") as stream:
    json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)

print("SURFACE TRACTION REGION PROBE DONE")
