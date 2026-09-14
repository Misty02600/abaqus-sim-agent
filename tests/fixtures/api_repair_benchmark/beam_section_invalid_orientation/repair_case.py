# ruff: noqa: F403, I001

import json

from abaqus import mdb
from abaqusConstants import DURING_ANALYSIS
from caeModules import *


model = mdb.Model(name="RepairModel")
material = model.Material(name="Steel")
material.Elastic(table=((210000.0, 0.3),))
model.RectangularProfile(name="RectProfile", a=10.0, b=10.0)
model.BeamSection(
    name="ProbeBeamSection",
    integration=DURING_ANALYSIS,
    profile="RectProfile",
    material="Steel",
    poissonRatio=0.3,
    orientation=(0.0, 0.0, 1.0),
)

section = model.sections["ProbeBeamSection"]
with open(
    "beam_section_invalid_orientation_result.json", "w", encoding="utf-8"
) as stream:
    json.dump(
        {
            "case_id": "beam_section_invalid_orientation",
            "section_name": "ProbeBeamSection",
            "material": str(section.material),
            "profile": str(section.profile),
            "integration": str(section.integration),
        },
        stream,
        ensure_ascii=True,
        sort_keys=True,
    )
