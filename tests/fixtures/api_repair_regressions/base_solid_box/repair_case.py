"""Broken Part.BaseSolidBox case for the API-repair regression suite."""

import json
import os

from abaqus import mdb
from abaqusConstants import DEFORMABLE_BODY, THREE_D
from caeModules import *  # noqa: F403

CASE_ID = "base_solid_box"


def main():
    model = mdb.Model(name="BaseSolidBoxCase")
    part = model.Part(
        name="Block",
        dimensionality=THREE_D,
        type=DEFORMABLE_BODY,
    )

    # Create the requested 10 x 2 x 1 solid block.
    part.BaseSolidBox(length=10.0, width=2.0, height=1.0)

    cell_count = len(part.cells)
    if cell_count != 1:
        raise RuntimeError(f"expected one solid cell, got {cell_count}")
    bounding_box = part.cells.getBoundingBox()
    low = bounding_box["low"]
    high = bounding_box["high"]
    dimensions = [round(high[index] - low[index], 6) for index in range(3)]
    if dimensions != [10.0, 2.0, 1.0]:
        raise RuntimeError(f"unexpected block dimensions: {dimensions!r}")
    write_result(
        {
            "case_id": CASE_ID,
            "cell_count": cell_count,
            "dimensions": dimensions,
        }
    )


def write_result(payload):
    path = os.path.join(os.getcwd(), CASE_ID + "_result.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)


if __name__ == "__main__":
    main()
