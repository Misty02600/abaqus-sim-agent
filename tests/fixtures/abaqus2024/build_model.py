"""Build the Abaqus 2024 reference model for the plate-tension Gold case.

Run with::

    abq2024.bat cae noGUI=build_model.py

The script writes ``plate_tension.cae`` and ``plate_tension.inp`` only.  Solver
submission intentionally belongs to the separate runner stage.
"""

import os

import mesh
import step  # noqa: F401  # Registers step/output APIs on the Abaqus Model object.
from abaqus import mdb
from abaqusConstants import (
    ANALYSIS,
    CARTESIAN,
    CPS4R,
    DEFAULT,
    DEFORMABLE_BODY,
    FIXED,
    OFF,
    ON,
    PERCENTAGE,
    QUAD,
    SET,
    STANDARD,
    STRUCTURED,
    TWO_D_PLANAR,
    UNSET,
)

MODEL_NAME = "Model-1"
PART_NAME = "Plate"
INSTANCE_NAME = "PLATE-1"
JOB_NAME = "plate_tension"


def main():
    if MODEL_NAME in mdb.models.keys():
        model = mdb.models[MODEL_NAME]
    else:
        model = mdb.Model(name=MODEL_NAME)

    sketch = model.ConstrainedSketch(name="plate_profile", sheetSize=200.0)
    sketch.rectangle(point1=(0.0, 0.0), point2=(100.0, 20.0))
    part = model.Part(
        name=PART_NAME,
        dimensionality=TWO_D_PLANAR,
        type=DEFORMABLE_BODY,
    )
    part.BaseShell(sketch=sketch)
    del model.sketches["plate_profile"]

    material = model.Material(name="Steel")
    material.Elastic(table=((210000.0, 0.3),))
    model.HomogeneousSolidSection(
        name="PlateSection",
        material="Steel",
        thickness=1.0,
    )
    plate_face = part.Set(name="PLATE_FACE", faces=part.faces[:])
    part.SectionAssignment(region=plate_face, sectionName="PlateSection")

    part.setMeshControls(
        regions=part.faces[:],
        elemShape=QUAD,
        technique=STRUCTURED,
    )
    horizontal_edges = part.edges.findAt(
        ((50.0, 0.0, 0.0),),
        ((50.0, 20.0, 0.0),),
    )
    vertical_edges = part.edges.findAt(
        ((0.0, 10.0, 0.0),),
        ((100.0, 10.0, 0.0),),
    )
    part.seedEdgeByNumber(
        edges=horizontal_edges,
        number=20,
        constraint=FIXED,
    )
    part.seedEdgeByNumber(
        edges=vertical_edges,
        number=4,
        constraint=FIXED,
    )
    element_type = mesh.ElemType(elemCode=CPS4R, elemLibrary=STANDARD)
    part.setElementType(regions=(part.faces[:],), elemTypes=(element_type,))
    part.generateMesh()

    assembly = model.rootAssembly
    assembly.DatumCsysByDefault(coordSysType=CARTESIAN)
    instance = assembly.Instance(
        name=INSTANCE_NAME,
        part=part,
        dependent=ON,
    )
    left_edge = assembly.Set(
        name="LEFT_EDGE",
        edges=instance.edges.findAt(((0.0, 10.0, 0.0),)),
    )
    right_edge = assembly.Set(
        name="RIGHT_EDGE",
        edges=instance.edges.findAt(((100.0, 10.0, 0.0),)),
    )
    left_bottom = assembly.Set(
        name="LEFT_BOTTOM",
        vertices=instance.vertices.findAt(((0.0, 0.0, 0.0),)),
    )

    model.StaticStep(name="Tension", previous="Initial", nlgeom=OFF)
    model.DisplacementBC(
        name="BC-Left-U1",
        createStepName="Initial",
        region=left_edge,
        u1=SET,
        u2=UNSET,
        ur3=UNSET,
    )
    model.DisplacementBC(
        name="BC-LeftBottom-U2",
        createStepName="Initial",
        region=left_bottom,
        u1=UNSET,
        u2=SET,
        ur3=UNSET,
    )
    model.DisplacementBC(
        name="BC-Right-U1",
        createStepName="Tension",
        region=right_edge,
        u1=0.1,
        u2=UNSET,
        ur3=UNSET,
    )
    model.FieldOutputRequest(
        name="Gold-Field-Output",
        createStepName="Tension",
        variables=("U", "S", "RF"),
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
    print("Wrote plate_tension.cae and plate_tension.inp without submission")


if __name__ == "__main__":
    main()
