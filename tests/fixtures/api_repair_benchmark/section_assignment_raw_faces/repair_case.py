"""Broken SectionAssignment raw-FaceArray case for the API benchmark."""

import json
import os

from abaqus import mdb
from abaqusConstants import DEFORMABLE_BODY, TWO_D_PLANAR
from caeModules import *  # noqa: F403

CASE_ID = "section_assignment_raw_faces"


def main():
    model = mdb.Model(name="SectionAssignmentRawFacesCase")
    sketch = model.ConstrainedSketch(name="Profile", sheetSize=20.0)
    sketch.rectangle(point1=(0.0, 0.0), point2=(10.0, 2.0))
    part = model.Part(
        name="Plate",
        dimensionality=TWO_D_PLANAR,
        type=DEFORMABLE_BODY,
    )
    part.BaseShell(sketch=sketch)

    material = model.Material(name="Steel")
    material.Elastic(table=((210000.0, 0.3),))
    model.HomogeneousSolidSection(
        name="ProbeSection",
        material="Steel",
        thickness=1.0,
    )

    # Assign the requested section to every plate face.
    assignment = part.SectionAssignment(
        region=part.faces[:],
        sectionName="ProbeSection",
    )

    if len(part.sectionAssignments) != 1:
        raise RuntimeError("expected exactly one section assignment")
    if assignment.sectionName != "ProbeSection":
        raise RuntimeError(f"wrong assigned section: {assignment.sectionName}")
    write_result(
        {
            "case_id": CASE_ID,
            "assignment_count": len(part.sectionAssignments),
            "section_name": assignment.sectionName,
        }
    )


def write_result(payload):
    path = os.path.join(os.getcwd(), CASE_ID + "_result.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)


if __name__ == "__main__":
    main()
