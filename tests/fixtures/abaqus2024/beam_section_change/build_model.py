"""Build the 20 x 10 mm B31 section-change reference model."""

import os

import mesh
import section  # noqa: F401  # Registers profile/section APIs on the Model.
import step  # noqa: F401  # Registers step/output APIs on the Model.
from abaqus import mdb
from abaqusConstants import (
    ANALYSIS,
    B31,
    CARTESIAN,
    DEFAULT,
    DEFORMABLE_BODY,
    DURING_ANALYSIS,
    FIXED,
    LINEAR,
    N1_COSINES,
    OFF,
    ON,
    PERCENTAGE,
    STANDARD,
    THREE_D,
    UNSET,
)

MODEL_NAME = "BeamModel"
PART_NAME = "Beam"
INSTANCE_NAME = "BEAM-1"
JOB_NAME = "beam_tension"


def main():
    if MODEL_NAME in mdb.models.keys():
        del mdb.models[MODEL_NAME]
    model = mdb.Model(name=MODEL_NAME)

    sketch = model.ConstrainedSketch(name="beam_path", sheetSize=200.0)
    sketch.Line(point1=(0.0, 0.0), point2=(100.0, 0.0))
    part = model.Part(
        name=PART_NAME,
        dimensionality=THREE_D,
        type=DEFORMABLE_BODY,
    )
    part.BaseWire(sketch=sketch)
    del model.sketches["beam_path"]

    material = model.Material(name="Steel")
    material.Elastic(table=((210000.0, 0.3),))
    model.RectangularProfile(name="BeamProfile", a=20.0, b=10.0)
    model.BeamSection(
        name="BeamSection",
        integration=DURING_ANALYSIS,
        profile="BeamProfile",
        material="Steel",
        poissonRatio=0.3,
        temperatureVar=LINEAR,
    )
    beam_edges = part.Set(name="BEAM_EDGES", edges=part.edges[:])
    part.SectionAssignment(region=beam_edges, sectionName="BeamSection")
    part.assignBeamSectionOrientation(
        region=part.edges[:],
        method=N1_COSINES,
        n1=(0.0, 1.0, 0.0),
    )

    part.seedEdgeByNumber(edges=part.edges[:], number=20, constraint=FIXED)
    element_type = mesh.ElemType(elemCode=B31, elemLibrary=STANDARD)
    part.setElementType(regions=(part.edges[:],), elemTypes=(element_type,))
    part.generateMesh()

    assembly = model.rootAssembly
    assembly.DatumCsysByDefault(coordSysType=CARTESIAN)
    instance = assembly.Instance(name=INSTANCE_NAME, part=part, dependent=ON)
    left_end = assembly.Set(
        name="LEFT_END",
        vertices=instance.vertices.findAt(((0.0, 0.0, 0.0),)),
    )
    right_end = assembly.Set(
        name="RIGHT_END",
        vertices=instance.vertices.findAt(((100.0, 0.0, 0.0),)),
    )

    model.StaticStep(name="Tension", previous="Initial", nlgeom=OFF)
    model.EncastreBC(
        name="BC-Left-Encastre",
        createStepName="Initial",
        region=left_end,
    )
    model.DisplacementBC(
        name="BC-Right-U1",
        createStepName="Tension",
        region=right_end,
        u1=0.1,
        u2=UNSET,
        u3=UNSET,
        ur1=UNSET,
        ur2=UNSET,
        ur3=UNSET,
    )
    model.FieldOutputRequest(
        name="Gold-Field-Output",
        createStepName="Tension",
        variables=("U", "RF", "SF"),
    )

    job = mdb.Job(
        name=JOB_NAME,
        model=MODEL_NAME,
        type=ANALYSIS,
        memory=90,
        memoryUnits=PERCENTAGE,
        getMemoryFromAnalysis=True,
        multiprocessingMode=DEFAULT,
        numCpus=1,
        numGPUs=0,
    )
    workspace = os.getcwd()
    mdb.saveAs(pathName=os.path.join(workspace, JOB_NAME + ".cae"))
    job.writeInput(consistencyChecking=OFF)
    print("Wrote 20 x 10 mm beam_tension.cae and beam_tension.inp")


if __name__ == "__main__":
    main()
