"""Host-only reference for the two-material plate change case."""

# pyright: reportMissingImports=false

import os

import mesh
import step  # noqa: F401  # Registers step/output APIs on the Model object.
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

    profile = model.ConstrainedSketch(name="plate_profile", sheetSize=200.0)
    profile.rectangle(point1=(0.0, 0.0), point2=(100.0, 20.0))
    part = model.Part(
        name=PART_NAME,
        dimensionality=TWO_D_PLANAR,
        type=DEFORMABLE_BODY,
    )
    part.BaseShell(sketch=profile)
    del model.sketches["plate_profile"]

    partition = model.ConstrainedSketch(name="material_partition", sheetSize=200.0)
    partition.Line(point1=(50.0, 0.0), point2=(50.0, 20.0))
    part.PartitionFaceBySketch(faces=part.faces[:], sketch=partition)
    del model.sketches["material_partition"]

    steel = model.Material(name="Steel")
    steel.Elastic(table=((210000.0, 0.3),))
    soft = model.Material(name="SoftAlloy")
    soft.Elastic(table=((70000.0, 0.3),))
    model.HomogeneousSolidSection(
        name="LeftSection",
        material="Steel",
        thickness=1.0,
    )
    model.HomogeneousSolidSection(
        name="RightSection",
        material="SoftAlloy",
        thickness=1.0,
    )
    left_faces = part.faces.findAt(((25.0, 10.0, 0.0),))
    right_faces = part.faces.findAt(((75.0, 10.0, 0.0),))
    left_region = part.Set(name="LEFT_MATERIAL", faces=left_faces)
    right_region = part.Set(name="RIGHT_MATERIAL", faces=right_faces)
    part.SectionAssignment(region=left_region, sectionName="LeftSection")
    part.SectionAssignment(region=right_region, sectionName="RightSection")

    part.seedPart(
        size=5.0,
        deviationFactor=0.1,
        minSizeFactor=0.1,
        constraint=FIXED,
    )
    part.setMeshControls(
        regions=part.faces[:],
        elemShape=QUAD,
        technique=STRUCTURED,
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
        name="Change-Field-Output",
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
