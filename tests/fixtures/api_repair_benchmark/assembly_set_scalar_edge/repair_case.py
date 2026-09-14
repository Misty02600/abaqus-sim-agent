"""Broken Assembly.Set scalar-edge case for the API-repair benchmark."""

import json
import os

from abaqus import mdb
from abaqusConstants import CARTESIAN, DEFORMABLE_BODY, ON, TWO_D_PLANAR
from caeModules import *  # noqa: F403

CASE_ID = "assembly_set_scalar_edge"


def main():
    model = mdb.Model(name="AssemblySetScalarEdgeCase")
    sketch = model.ConstrainedSketch(name="Profile", sheetSize=20.0)
    sketch.rectangle(point1=(0.0, 0.0), point2=(10.0, 2.0))
    part = model.Part(
        name="Plate",
        dimensionality=TWO_D_PLANAR,
        type=DEFORMABLE_BODY,
    )
    part.BaseShell(sketch=sketch)

    assembly = model.rootAssembly
    assembly.DatumCsysByDefault(coordSysType=CARTESIAN)
    instance = assembly.Instance(name="PLATE-1", part=part, dependent=ON)
    left_edge = instance.edges.findAt((0.0, 1.0, 0.0))

    # Create the requested assembly-level edge region.
    created = assembly.Set(name="LEFT_EDGE", edges=left_edge)

    edge_count = len(created.edges)
    if edge_count != 1:
        raise RuntimeError(f"LEFT_EDGE contains {edge_count} edges")
    write_result({"case_id": CASE_ID, "edge_count": edge_count})


def write_result(payload):
    path = os.path.join(os.getcwd(), CASE_ID + "_result.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)


if __name__ == "__main__":
    main()
