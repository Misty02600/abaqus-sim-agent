"""Broken fixture: Assembly.Surface receives a flat tuple of Edge objects."""

import json

from abaqus import mdb
from abaqusConstants import CARTESIAN, DEFORMABLE_BODY, ON, TWO_D_PLANAR

MODEL_NAME = "AssemblySurfaceRepair"

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

assembly = model.rootAssembly
assembly.DatumCsysByDefault(coordSysType=CARTESIAN)
instance = assembly.Instance(name="PLATE-1", part=part, dependent=ON)
bottom = instance.edges.findAt((5.0, 0.0, 0.0))
top = instance.edges.findAt((5.0, 2.0, 0.0))

# Broken on purpose: this consumer rejects a flat host-Python Edge tuple.
assembly.Surface(name="SIDE1", side1Edges=(bottom, top))

payload = {
    "case_id": "assembly_surface_flat_edges",
    "surface_count": len(assembly.surfaces),
    "surface_present": "SIDE1" in assembly.surfaces,
}
with open(
    "assembly_surface_flat_edges_result.json",
    "w",
    encoding="utf-8",
) as stream:
    json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
