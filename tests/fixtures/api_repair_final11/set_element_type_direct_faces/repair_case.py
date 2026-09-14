"""Broken fixture: setElementType receives a bare FaceArray."""

import json

import mesh
from abaqus import mdb
from abaqusConstants import (
    CPS4R,
    DEFORMABLE_BODY,
    FREE,
    QUAD,
    STANDARD,
    STRUCTURED,
    TWO_D_PLANAR,
)

MODEL_NAME = "SetElementTypeRepair"

if MODEL_NAME in mdb.models:
    del mdb.models[MODEL_NAME]
model = mdb.Model(name=MODEL_NAME)
sketch = model.ConstrainedSketch(name="Profile", sheetSize=20.0)
sketch.rectangle(point1=(0.0, 0.0), point2=(10.0, 2.0))
part = model.Part(
    name="Plate",
    dimensionality=TWO_D_PLANAR,
    type=DEFORMABLE_BODY,
)
part.BaseShell(sketch=sketch)
del model.sketches["Profile"]

part.seedPart(size=1.0, constraint=FREE)
part.setMeshControls(
    regions=part.faces[:],
    elemShape=QUAD,
    technique=STRUCTURED,
)
element_type = mesh.ElemType(elemCode=CPS4R, elemLibrary=STANDARD)

# Broken on purpose: the geometry-array branch needs its consumer wrapper.
part.setElementType(regions=part.faces[:], elemTypes=(element_type,))
part.generateMesh()

payload = {
    "case_id": "set_element_type_direct_faces",
    "element_count": len(part.elements),
    "element_types": sorted({str(element.type) for element in part.elements}),
}
with open(
    "set_element_type_direct_faces_result.json",
    "w",
    encoding="utf-8",
) as stream:
    json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
