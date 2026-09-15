"""参考用提取器；只根据实际 ODB 计算，不填参考常数。"""

# pyright: reportMissingImports=false
import json
import math

from abaqusConstants import ELEMENT_NODAL, ON
from odbAccess import openOdb

with open("reference_case.json", encoding="utf-8") as f:
    c = json.load(f)
odb = openOdb("model.odb", readOnly=True)
inst = next(iter(odb.rootAssembly.instances.values()))
coords = {n.label: n.coordinates for n in inst.nodes}
left = {k for k, v in coords.items() if abs(v[0]) < 1e-7}
right = {k for k, v in coords.items() if abs(v[0] - c["length"]) < 1e-7}
steps = list(odb.steps.values())
frame = steps[-1].frames[-1]


def vector(name, comp, labels, frm=None):
    field = (frm or frame).fieldOutputs[name]
    i = list(field.componentLabels).index(comp)
    return [float(v.data[i]) for v in field.values if v.nodeLabel in labels]


def avg(v):
    return sum(v) / len(v)


r = {}
if c["family"] == "truss":
    r.update(
        tip_u1=avg(vector("U", "U1", right)), reaction_x=sum(vector("RF", "RF1", left))
    )
    field = frame.fieldOutputs["S"]
    i = list(field.componentLabels).index("S11")
    if len(c["segments"]) == 1:
        r["mean_s11"] = avg([float(v.data[i]) for v in field.values])
    else:
        mid = {n for n, p in coords.items() if abs(p[0] - 400) < 1e-7}
        r["interface_u1"] = avg(vector("U", "U1", mid))
        for j in (1, 2):
            selected = {
                e.label
                for e in inst.elements
                if (
                    sum(coords[n][0] for n in e.connectivity) / len(e.connectivity)
                    < 400
                )
                == (j == 1)
            }
            r[f"segment_{j}_s11"] = avg(
                [float(v.data[i]) for v in field.values if v.elementLabel in selected]
            )
elif c["family"] == "beam":
    r.update(
        tip_u2=avg(vector("U", "U2", right)),
        tip_ur3=avg(vector("UR", "UR3", right)),
        reaction_y=sum(vector("RF", "RF2", left)),
        reaction_mz=sum(vector("RM", "RM3", left)),
    )
    r["root_bending_stress"] = (
        abs(r["reaction_mz"]) * 6 / (c["width"] * c["height"] ** 2)
    )
elif c["family"] == "hole":
    labels = {
        n
        for n, p in coords.items()
        if abs(math.hypot(p[0] - 7.5, p[1] - 2.5) - 0.5) < 1e-5
    }
    field = frame.fieldOutputs["S"].getSubset(position=ELEMENT_NODAL, readOnly=ON)
    i = list(field.componentLabels).index("S11")
    r.update(
        hole_max_s11=max(
            float(v.data[i]) for v in field.values if v.nodeLabel in labels
        ),
        reaction_x=sum(vector("RF", "RF1", left)),
    )
else:
    for i, axis in enumerate("xyz"):
        f = steps[i].frames[-1]
        r[f"tip_u{i+1}_{axis}"] = max(vector("U", f"U{i+1}", right, f))
        r[f"reaction_{axis}_{axis}"] = sum(
            vector("RF", f"RF{i+1}", left, f)
        )
odb.close()
with open("results.json", "w", encoding="utf-8") as f:
    json.dump(r, f, allow_nan=False, indent=2)
