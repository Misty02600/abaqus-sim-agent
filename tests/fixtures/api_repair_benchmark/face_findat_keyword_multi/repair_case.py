"""Broken FaceArray.findAt keyword-multi case for the API benchmark."""

import json
import os

from abaqus import mdb
from abaqusConstants import DEFORMABLE_BODY, TWO_D_PLANAR
from caeModules import *  # noqa: F403

CASE_ID = "face_findat_keyword_multi"


def main():
    model = mdb.Model(name="FaceFindAtKeywordMultiCase")
    sketch = model.ConstrainedSketch(name="Profile", sheetSize=20.0)
    sketch.rectangle(point1=(0.0, 0.0), point2=(2.0, 2.0))
    sketch.rectangle(point1=(4.0, 0.0), point2=(6.0, 2.0))
    part = model.Part(
        name="TwoFaces",
        dimensionality=TWO_D_PLANAR,
        type=DEFORMABLE_BODY,
    )
    part.BaseShell(sketch=sketch)

    coordinates = (((1.0, 1.0, 0.0),), ((5.0, 1.0, 0.0),))
    # Select both faces using their interior coordinates.
    selected = part.faces.findAt(coordinates=coordinates)

    face_count = len(selected)
    if face_count != 2:
        raise RuntimeError(f"expected two selected faces, got {face_count}")
    write_result({"case_id": CASE_ID, "face_count": face_count})


def write_result(payload):
    path = os.path.join(os.getcwd(), CASE_ID + "_result.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)


if __name__ == "__main__":
    main()
