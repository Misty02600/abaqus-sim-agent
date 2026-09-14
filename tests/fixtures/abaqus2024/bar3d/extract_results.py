"""Extract the public 3D bar-tension result summary with Abaqus Python."""

import json
import sys

from odbAccess import openOdb


def _mean(values):
    if not values:
        raise RuntimeError("result selection produced no values")
    return sum(values) / float(len(values))


def _near(value, target, scale):
    tolerance = max(abs(scale) * 1.0e-8, 1.0e-9)
    return abs(value - target) <= tolerance


def main():
    if len(sys.argv) != 3:
        raise RuntimeError("expected ODB path and output JSON path")

    odb_path = sys.argv[1]
    output_path = sys.argv[2]
    odb = openOdb(path=odb_path, readOnly=True)
    try:
        step = odb.steps["Tension"]
        frame = step.frames[-1]
        instance = odb.rootAssembly.instances["BAR-1"]
        nodes = list(instance.nodes)
        x_values = [float(node.coordinates[0]) for node in nodes]
        x_min = min(x_values)
        x_max = max(x_values)
        scale = max(abs(x_min), abs(x_max), 1.0)
        left_labels = {
            node.label
            for node in nodes
            if _near(float(node.coordinates[0]), x_min, scale)
        }
        right_labels = {
            node.label
            for node in nodes
            if _near(float(node.coordinates[0]), x_max, scale)
        }

        u_values = frame.fieldOutputs["U"].getSubset(region=instance).values
        right_u1 = [
            float(value.data[0])
            for value in u_values
            if value.nodeLabel in right_labels
        ]
        rf_values = frame.fieldOutputs["RF"].getSubset(region=instance).values
        left_rf1 = [
            float(value.data[0])
            for value in rf_values
            if value.nodeLabel in left_labels
        ]
        s_values = frame.fieldOutputs["S"].getSubset(region=instance).values
        s11 = [float(value.data[0]) for value in s_values]

        payload = {
            "right_mean_u1": _mean(right_u1),
            "mean_s11": _mean(s11),
            "left_sum_rf1": sum(left_rf1),
        }
        with open(output_path, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
