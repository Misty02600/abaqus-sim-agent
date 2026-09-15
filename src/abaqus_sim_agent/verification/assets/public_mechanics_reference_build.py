"""宿主参考建模脚本；通过 CAE 工具执行，不提交 Job。"""

# pyright: reportMissingImports=false
import json
import math
import os

import caeModules  # noqa: F401
import mesh
from abaqus import mdb
from abaqusConstants import (
    B31,
    C3D10,
    C3D20R,
    DEFORMABLE_BODY,
    DURING_ANALYSIS,
    FIXED,
    FREE,
    HEX,
    LINEAR,
    N1_COSINES,
    OFF,
    ON,
    STANDARD,
    STRUCTURED,
    T3D2,
    TET,
    THREE_D,
)


def build(c, fine, job_name):
    model = mdb.Model(name=job_name)
    sketch = model.ConstrainedSketch(
        name="Geometry", sheetSize=max(1.0, c["length"] * 3)
    )
    family = c["family"]
    length = c["length"]
    part = model.Part(name="Body", dimensionality=THREE_D, type=DEFORMABLE_BODY)
    if family in ("truss", "beam"):
        if family == "truss":
            x = 0.0
            for size, _ in c["segments"]:
                sketch.Line(point1=(x, 0.0), point2=(x + size, 0.0))
                x += size
        else:
            sketch.Line(point1=(0.0, 0.0), point2=(length, 0.0))
        part.BaseWire(sketch=sketch)
    else:
        sketch.rectangle(point1=(0.0, 0.0), point2=(length, c["width"]))
        if family == "hole":
            sketch.CircleByCenterPerimeter(center=(7.5, 2.5), point1=(8.0, 2.5))
        part.BaseSolidExtrude(sketch=sketch, depth=c["height"])
    material = model.Material(name="Material")
    material.Elastic(table=((c["young"], c["poisson"]),))
    if family == "truss":
        x = 0.0
        for i, (size, area) in enumerate(c["segments"]):
            name = f"Section{i}"
            model.TrussSection(name=name, material="Material", area=area)
            region = part.Set(
                name=name, edges=part.edges.findAt(((x + size / 2, 0.0, 0.0),))
            )
            part.SectionAssignment(region=region, sectionName=name)
            x += size
        part.seedPart(size=length / 40)
        part.setElementType(
            regions=(part.edges,),
            elemTypes=(mesh.ElemType(elemCode=T3D2, elemLibrary=STANDARD),),
        )
    elif family == "beam":
        model.RectangularProfile(name="Profile", a=c["width"], b=c["height"])
        model.BeamSection(
            name="Section",
            integration=DURING_ANALYSIS,
            profile="Profile",
            material="Material",
            poissonRatio=c["poisson"],
            temperatureVar=LINEAR,
        )
        part.SectionAssignment(
            region=part.Set(name="AllEdges", edges=part.edges), sectionName="Section"
        )
        part.assignBeamSectionOrientation(
            region=(part.edges[:],), method=N1_COSINES, n1=(0.0, 0.0, -1.0)
        )
        part.seedEdgeByNumber(edges=part.edges, number=40, constraint=FIXED)
        part.setElementType(
            regions=(part.edges,),
            elemTypes=(mesh.ElemType(elemCode=B31, elemLibrary=STANDARD),),
        )
    else:
        model.HomogeneousSolidSection(name="Section", material="Material")
        part.SectionAssignment(
            region=part.Set(name="AllCells", cells=part.cells), sectionName="Section"
        )
        if family == "hole":
            part.setMeshControls(regions=part.cells, technique=FREE, elemShape=TET)
            part.seedPart(size=0.25 if fine else 0.5)
            for edge in part.edges:
                if abs(edge.getSize() - math.pi) < 1e-5:
                    part.seedEdgeByNumber(
                        edges=(edge,), number=96 if fine else 48, constraint=FIXED
                    )
            part.setElementType(
                regions=(part.cells,),
                elemTypes=(mesh.ElemType(elemCode=C3D10, elemLibrary=STANDARD),),
            )
        else:
            part.setMeshControls(
                regions=part.cells, technique=STRUCTURED, elemShape=HEX
            )
            for edge in part.edges:
                size = edge.getSize()
                n = (
                    120
                    if abs(size - length) < 1e-7
                    else 6
                    if abs(size - c["width"]) < 1e-7
                    else 4
                )
                if not fine:
                    n = max(2, n // 2)
                part.seedEdgeByNumber(edges=(edge,), number=n, constraint=FIXED)
            part.setElementType(
                regions=(part.cells,),
                elemTypes=(mesh.ElemType(elemCode=C3D20R, elemLibrary=STANDARD),),
            )
    part.generateMesh()
    assembly = model.rootAssembly
    inst = assembly.Instance(name="Body-1", part=part, dependent=ON)
    epsilon = length * 1e-8
    left = assembly.Set(
        name="Left", nodes=inst.nodes.getByBoundingBox(xMin=-epsilon, xMax=epsilon)
    )
    right = assembly.Set(
        name="Right",
        nodes=inst.nodes.getByBoundingBox(xMin=length - epsilon, xMax=length + epsilon),
    )
    if family == "truss":
        whole = assembly.Set(name="Whole", nodes=inst.nodes)
        model.DisplacementBC(
            name="Transverse", createStepName="Initial", region=whole, u2=0.0, u3=0.0
        )
        model.DisplacementBC(
            name="Fixed", createStepName="Initial", region=left, u1=0.0
        )
    else:
        model.EncastreBC(name="Fixed", createStepName="Initial", region=left)
    step_names = ("X", "Y", "Z") if family == "solid_beam" else ("Load",)
    last_load = None
    for i, step_name in enumerate(step_names):
        model.StaticStep(
            name=step_name,
            previous="Initial" if i == 0 else step_names[i - 1],
            nlgeom=OFF,
        )
        if last_load:
            model.loads[last_load].deactivate(step_name)
        last_load = f"Load{i}"
        if family == "hole":
            surface = assembly.Surface(
                name="LoadedFace", side1Faces=inst.faces.findAt(((15.0, 2.5, 0.5),))
            )
            model.Pressure(
                name=last_load,
                createStepName=step_name,
                region=surface,
                magnitude=-c["load"],
            )
        elif c["distributed"]:
            model.LineLoad(
                name=last_load,
                createStepName=step_name,
                region=assembly.Set(name="LoadedEdges", edges=inst.edges),
                comp2=-c["load"],
            )
        else:
            component = i + 1 if family == "solid_beam" else 2 if family == "beam" else 1
            kwargs = {f"cf{component}": c["load"] / len(right.nodes) * (-1 if family == "beam" else 1)}
            model.ConcentratedForce(
                name=last_load, createStepName=step_name, region=right, **kwargs
            )
    model.fieldOutputRequests["F-Output-1"].setValues(
        variables=("U", "RF", "S", "SF")
        if family == "beam"
        else ("U", "RF", "S", "EVOL")
    )
    job = mdb.Job(name=job_name, model=job_name, numCpus=1)
    job.writeInput(consistencyChecking=ON)


with open("reference_case.json", encoding="utf-8") as stream:
    case = json.load(stream)
if case["refinement"]:
    build(case, False, "model_coarse")
build(case, True, "model")
mdb.saveAs(pathName=os.path.abspath("model.cae"))
