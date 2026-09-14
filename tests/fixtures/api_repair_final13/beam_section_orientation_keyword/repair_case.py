"""Broken fixture: BeamSection receives an orientation constructor keyword."""

import json

import section  # noqa: F401  # Registers profile and section APIs on Model.
from abaqus import mdb
from abaqusConstants import (
    DEFORMABLE_BODY,
    DURING_ANALYSIS,
    LINEAR,
    N1_COSINES,
    THREE_D,
)

MODEL_NAME = "BeamSectionOrientationRepair"
ORIENTATION = (0.0, 1.0, 0.0)

if MODEL_NAME in mdb.models:
    del mdb.models[MODEL_NAME]
model = mdb.Model(name=MODEL_NAME)
sketch = model.ConstrainedSketch(name="ProfileSketch", sheetSize=20.0)
sketch.Line(point1=(0.0, 0.0), point2=(10.0, 0.0))
part = model.Part(
    name="Beam",
    dimensionality=THREE_D,
    type=DEFORMABLE_BODY,
)
part.BaseWire(sketch=sketch)
del model.sketches["ProfileSketch"]

material = model.Material(name="Steel")
material.Elastic(table=((210000.0, 0.3),))
model.RectangularProfile(name="RectProfile", a=1.0, b=1.0)

# Broken on purpose: orientation belongs to assignBeamSectionOrientation.
model.BeamSection(
    name="BeamSection",
    integration=DURING_ANALYSIS,
    profile="RectProfile",
    material="Steel",
    poissonRatio=0.3,
    temperatureVar=LINEAR,
    orientation=ORIENTATION,
)

edge_set = part.Set(name="ALL_EDGES", edges=part.edges[:])
part.SectionAssignment(region=edge_set, sectionName="BeamSection")
part.assignBeamSectionOrientation(
    region=part.edges[:],
    method=N1_COSINES,
    n1=ORIENTATION,
)

payload = {
    "case_id": "beam_section_orientation_keyword",
    "orientation": list(ORIENTATION),
    "section_present": "BeamSection" in model.sections,
}
with open(
    "beam_section_orientation_keyword_result.json",
    "w",
    encoding="utf-8",
) as stream:
    json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
