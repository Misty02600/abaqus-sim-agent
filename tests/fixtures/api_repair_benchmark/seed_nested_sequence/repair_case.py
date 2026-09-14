"""Broken seedEdgeByNumber container case for the API-repair benchmark."""

import json
import os

from abaqus import mdb
from abaqusConstants import DEFORMABLE_BODY, FIXED, NUMBER, TWO_D_PLANAR
from caeModules import *  # noqa: F403

CASE_ID = "seed_nested_sequence"


def main():
    model = mdb.Model(name="SeedNestedSequenceCase")
    sketch = model.ConstrainedSketch(name="Profile", sheetSize=20.0)
    sketch.rectangle(point1=(0.0, 0.0), point2=(10.0, 2.0))
    part = model.Part(
        name="Plate",
        dimensionality=TWO_D_PLANAR,
        type=DEFORMABLE_BODY,
    )
    part.BaseShell(sketch=sketch)

    bottom_point = (5.0, 0.0, 0.0)
    top_point = (5.0, 2.0, 0.0)
    native_edges = part.edges.findAt((bottom_point,), (top_point,))

    # Apply the requested seed count to both horizontal edges.
    part.seedEdgeByNumber(
        edges=(native_edges,),
        number=5,
        constraint=FIXED,
    )

    bottom = part.edges.findAt(bottom_point)
    top = part.edges.findAt(top_point)
    seed_numbers = [
        part.getEdgeSeeds(edge=bottom, attribute=NUMBER),
        part.getEdgeSeeds(edge=top, attribute=NUMBER),
    ]
    if seed_numbers != [5, 5]:
        raise RuntimeError(f"seed numbers did not update: {seed_numbers!r}")
    write_result({"case_id": CASE_ID, "seed_numbers": seed_numbers})


def write_result(payload):
    path = os.path.join(os.getcwd(), CASE_ID + "_result.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)


if __name__ == "__main__":
    main()
