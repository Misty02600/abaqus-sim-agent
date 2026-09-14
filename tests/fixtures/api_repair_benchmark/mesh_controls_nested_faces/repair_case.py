"""Broken setMeshControls nested-FaceArray case for the API benchmark."""

import json
import os

from abaqus import mdb
from abaqusConstants import (
    DEFORMABLE_BODY,
    ELEM_SHAPE,
    QUAD,
    STRUCTURED,
    TECHNIQUE,
    TWO_D_PLANAR,
)
from caeModules import *  # noqa: F403

CASE_ID = "mesh_controls_nested_faces"


def main():
    model = mdb.Model(name="MeshControlsNestedFacesCase")
    sketch = model.ConstrainedSketch(name="Profile", sheetSize=20.0)
    sketch.rectangle(point1=(0.0, 0.0), point2=(10.0, 2.0))
    part = model.Part(
        name="Plate",
        dimensionality=TWO_D_PLANAR,
        type=DEFORMABLE_BODY,
    )
    part.BaseShell(sketch=sketch)
    faces = part.faces[:]

    # Apply the requested controls to every face.
    part.setMeshControls(
        regions=(faces,),
        elemShape=QUAD,
        technique=STRUCTURED,
    )

    elem_shape = str(part.getMeshControl(part.faces[0], ELEM_SHAPE))
    technique = str(part.getMeshControl(part.faces[0], TECHNIQUE))
    if (elem_shape, technique) != ("QUAD", "STRUCTURED"):
        raise RuntimeError(f"unexpected mesh controls: {elem_shape}/{technique}")
    write_result(
        {
            "case_id": CASE_ID,
            "elem_shape": elem_shape,
            "technique": technique,
        }
    )


def write_result(payload):
    path = os.path.join(os.getcwd(), CASE_ID + "_result.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)


if __name__ == "__main__":
    main()
