# -*- coding: ascii -*-

import json
import os

from abaqus import mdb
from abaqusConstants import DEFORMABLE_BODY, FIXED, NUMBER, TWO_D_PLANAR
from caeModules import *


CASE_ID = "api_container_repair_seed_edges"
MODEL_NAME = "ContainerRepair"
RESULT_PATH = os.path.join(os.getcwd(), "api_container_repair_result.json")
SEED_NUMBER = 7


if MODEL_NAME in mdb.models:
    del mdb.models[MODEL_NAME]

model = mdb.Model(name=MODEL_NAME)
sketch = model.ConstrainedSketch(name="Profile", sheetSize=200.0)
sketch.rectangle(point1=(0.0, 0.0), point2=(100.0, 20.0))
part = model.Part(
    name="Plate",
    dimensionality=TWO_D_PLANAR,
    type=DEFORMABLE_BODY,
)
part.BaseShell(sketch=sketch)

bottom_point = (50.0, 0.0, 0.0)
top_point = (50.0, 20.0, 0.0)
selected_edges = part.edges.findAt(
    (bottom_point,),
    (top_point,),
)
edges_for_seeding = (selected_edges,)

part.seedEdgeByNumber(
    edges=edges_for_seeding,
    number=SEED_NUMBER,
    constraint=FIXED,
)

bottom_edge = part.edges.findAt(bottom_point)
top_edge = part.edges.findAt(top_point)
bottom_seed_number = part.getEdgeSeeds(edge=bottom_edge, attribute=NUMBER)
top_seed_number = part.getEdgeSeeds(edge=top_edge, attribute=NUMBER)

if len(selected_edges) != 2:
    raise RuntimeError("expected two selected edges")
if bottom_seed_number != SEED_NUMBER or top_seed_number != SEED_NUMBER:
    raise RuntimeError("the requested edge seeds were not applied")

with open(RESULT_PATH, "w") as stream:
    json.dump(
        {
            "case_id": CASE_ID,
            "status": "passed",
            "selected_edge_count": len(selected_edges),
            "bottom_seed_number": bottom_seed_number,
            "top_seed_number": top_seed_number,
        },
        stream,
        indent=2,
        sort_keys=True,
    )
    stream.write("\n")
