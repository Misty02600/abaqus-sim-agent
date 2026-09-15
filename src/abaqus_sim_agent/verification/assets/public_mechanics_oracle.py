"""宿主独立 ODB 读取器；通过 trusted Abaqus Python 调用。"""

# pyright: reportMissingImports=false
import json
import math
import sys

from abaqusConstants import ELEMENT_NODAL, INTEGRATION_POINT, ON
from odbAccess import openOdb


def component(frame, field_name, label, node_labels=None):
    field = frame.fieldOutputs[field_name]
    index = list(field.componentLabels).index(label)
    return [
        float(v.data[index])
        for v in field.values
        if node_labels is None or v.nodeLabel in node_labels
    ]


def mean(values):
    if not values:
        raise ValueError("empty oracle selection")
    return sum(values) / len(values)


def inspect(path, c):
    odb = openOdb(path=path, readOnly=True)
    try:
        if len(odb.rootAssembly.instances) != 1:
            raise ValueError("one deformable instance required")
        instance = next(iter(odb.rootAssembly.instances.values()))
        nodes = {
            int(n.label): [float(x) for x in n.coordinates] for n in instance.nodes
        }
        elements = {
            int(e.label): {"type": str(e.type), "nodes": list(e.connectivity)}
            for e in instance.elements
        }
        eps = max(c["length"] * 1e-7, 1e-9)
        left = {n for n, p in nodes.items() if abs(p[0]) < eps}
        right = {n for n, p in nodes.items() if abs(p[0] - c["length"]) < eps}
        if not left or not right:
            raise ValueError("missing end nodes")
        frames = [s.frames[-1] for s in odb.steps.values()]
        values = {}
        if c["family"] == "truss":
            frame = frames[-1]
            values["tip_u1"] = mean(component(frame, "U", "U1", right))
            values["reaction_x"] = sum(component(frame, "RF", "RF1", left))
            stress = frame.fieldOutputs["S"].getSubset(position=INTEGRATION_POINT)
            index = list(stress.componentLabels).index("S11")
            if len(c["segments"]) == 1:
                values["mean_s11"] = mean([float(v.data[index]) for v in stress.values])
            else:
                interface = {n for n, p in nodes.items() if abs(p[0] - 400) < eps}
                values["interface_u1"] = mean(component(frame, "U", "U1", interface))
                for i in (1, 2):
                    labels = {
                        e
                        for e, d in elements.items()
                        if (mean([nodes[n][0] for n in d["nodes"]]) < 400) == (i == 1)
                    }
                    values[f"segment_{i}_s11"] = mean(
                        [
                            float(v.data[index])
                            for v in stress.values
                            if v.elementLabel in labels
                        ]
                    )
        elif c["family"] == "beam":
            frame = frames[-1]
            values.update(
                tip_u2=mean(component(frame, "U", "U2", right)),
                tip_ur3=mean(component(frame, "UR", "UR3", right)),
                reaction_y=sum(component(frame, "RF", "RF2", left)),
                reaction_mz=sum(component(frame, "RM", "RM3", left)),
            )
            inertia = c["width"] * c["height"] ** 3 / 12
            values["root_bending_stress"] = (
                abs(values["reaction_mz"]) * c["height"] / 2 / inertia
            )
        elif c["family"] == "hole":
            frame = frames[-1]
            surface = {
                n
                for n, p in nodes.items()
                if abs(math.hypot(p[0] - 7.5, p[1] - 2.5) - 0.5) < 1e-5
            }
            field = frame.fieldOutputs["S"].getSubset(
                position=ELEMENT_NODAL, readOnly=ON
            )
            index = list(field.componentLabels).index("S11")
            values["hole_max_s11"] = max(
                float(v.data[index]) for v in field.values if v.nodeLabel in surface
            )
            values["reaction_x"] = sum(component(frame, "RF", "RF1", left))
        else:
            if len(frames) != 3:
                raise ValueError("three independent ordered load steps required")
            for i, axis in enumerate("xyz"):
                values[f"tip_u{i+1}_{axis}"] = max(
                    component(frames[i], "U", f"U{i+1}", right)
                )
                values[f"reaction_{axis}_{axis}"] = sum(
                    component(frames[i], "RF", f"RF{i+1}", left)
                )
        elastic = []
        for material in odb.materials.values():
            elastic.append([float(x) for x in material.elastic.table[0]])
        profiles = (
            [{"a": float(p.a), "b": float(p.b)} for p in odb.profiles.values()]
            if c["family"] == "beam"
            else []
        )
        orientations = (
            [list(o.vector) for o in instance.beamOrientations]
            if c["family"] == "beam"
            else []
        )
        volume = None
        if c["family"] in ("hole", "solid_beam"):
            volume = sum(float(v.data) for v in frames[-1].fieldOutputs["EVOL"].values)
        if not all(math.isfinite(v) for v in values.values()):
            raise ValueError("nonfinite oracle measurement")
        return {
            "metrics": values,
            "nodes": nodes,
            "elements": elements,
            "volume": volume,
            "step_names": list(odb.steps.keys()),
            "elastic": elastic,
            "profiles": profiles,
            "beam_orientations": orientations,
            "assembly_node_count": len(getattr(odb.rootAssembly, "nodes", ())),
        }
    finally:
        odb.close()


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as stream:
        request = json.load(stream)
    result = inspect(request["odb_path"], request["case"])
    with open(sys.argv[2], "w", encoding="utf-8") as stream:
        json.dump(result, stream, allow_nan=False)
