"""Build the Abaqus 2024 reference model for the 3D bar-tension Gold case.

Run with::

    abq2024.bat cae noGUI=build_model.py

The script writes ``bar_tension.cae`` and ``bar_tension.inp`` only. Solver
submission intentionally belongs to the separate runner stage.
"""

import os

import mesh
import step  # noqa: F401  # Registers step/output APIs on the Abaqus Model object.
from abaqus import mdb
from abaqusConstants import (
    ANALYSIS,
    C3D8R,
    CARTESIAN,
    DEFAULT,
    DEFORMABLE_BODY,
    FIXED,
    HEX,
    OFF,
    ON,
    PERCENTAGE,
    SET,
    STANDARD,
    STRUCTURED,
    THREE_D,
    UNSET,
)

MODEL_NAME = "BarModel"
PART_NAME = "Bar"
INSTANCE_NAME = "BAR-1"
JOB_NAME = "bar_tension"


def main():
    if MODEL_NAME in mdb.models.keys():
        del mdb.models[MODEL_NAME]
    model = mdb.Model(name=MODEL_NAME)

    sketch = model.ConstrainedSketch(name="bar_profile", sheetSize=200.0)
    sketch.rectangle(point1=(0.0, 0.0), point2=(100.0, 10.0))
    part = model.Part(
        name=PART_NAME,
        dimensionality=THREE_D,
        type=DEFORMABLE_BODY,
    )
    part.BaseSolidExtrude(sketch=sketch, depth=10.0)
    del model.sketches["bar_profile"]

    material = model.Material(name="Steel")
    material.Elastic(table=((210000.0, 0.3),))
    model.HomogeneousSolidSection(name="BarSection", material="Steel")
    bar_cells = part.Set(name="BAR_CELLS", cells=part.cells[:])
    part.SectionAssignment(region=bar_cells, sectionName="BarSection")

    part.setMeshControls(
        regions=part.cells[:],
        elemShape=HEX,
        technique=STRUCTURED,
    )
    x_edges = part.edges.findAt(
        ((50.0, 0.0, 0.0),),
        ((50.0, 10.0, 0.0),),
        ((50.0, 0.0, 10.0),),
        ((50.0, 10.0, 10.0),),
    )
    y_edges = part.edges.findAt(
        ((0.0, 5.0, 0.0),),
        ((100.0, 5.0, 0.0),),
        ((0.0, 5.0, 10.0),),
        ((100.0, 5.0, 10.0),),
    )
    z_edges = part.edges.findAt(
        ((0.0, 0.0, 5.0),),
        ((100.0, 0.0, 5.0),),
        ((0.0, 10.0, 5.0),),
        ((100.0, 10.0, 5.0),),
    )
    part.seedEdgeByNumber(edges=x_edges, number=20, constraint=FIXED)
    part.seedEdgeByNumber(edges=y_edges, number=2, constraint=FIXED)
    part.seedEdgeByNumber(edges=z_edges, number=2, constraint=FIXED)
    element_type = mesh.ElemType(elemCode=C3D8R, elemLibrary=STANDARD)
    part.setElementType(regions=(part.cells[:],), elemTypes=(element_type,))
    part.generateMesh()

    assembly = model.rootAssembly
    assembly.DatumCsysByDefault(coordSysType=CARTESIAN)
    instance = assembly.Instance(name=INSTANCE_NAME, part=part, dependent=ON)
    left_face = assembly.Set(
        name="LEFT_FACE",
        faces=instance.faces.findAt(((0.0, 5.0, 5.0),)),
    )
    right_face = assembly.Set(
        name="RIGHT_FACE",
        faces=instance.faces.findAt(((100.0, 5.0, 5.0),)),
    )
    anchor = assembly.Set(
        name="ANCHOR",
        vertices=instance.vertices.findAt(((0.0, 0.0, 0.0),)),
    )
    guide = assembly.Set(
        name="GUIDE",
        vertices=instance.vertices.findAt(((0.0, 10.0, 0.0),)),
    )

    model.StaticStep(name="Tension", previous="Initial", nlgeom=OFF)
    model.DisplacementBC(
        name="BC-Left-U1",
        createStepName="Initial",
        region=left_face,
        u1=SET,
        u2=UNSET,
        u3=UNSET,
    )
    model.DisplacementBC(
        name="BC-Anchor-U2-U3",
        createStepName="Initial",
        region=anchor,
        u1=UNSET,
        u2=SET,
        u3=SET,
    )
    model.DisplacementBC(
        name="BC-Guide-U3",
        createStepName="Initial",
        region=guide,
        u1=UNSET,
        u2=UNSET,
        u3=SET,
    )
    model.DisplacementBC(
        name="BC-Right-U1",
        createStepName="Tension",
        region=right_face,
        u1=0.1,
        u2=UNSET,
        u3=UNSET,
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
    print("Wrote bar_tension.cae and bar_tension.inp without submission")


if __name__ == "__main__":
    main()
