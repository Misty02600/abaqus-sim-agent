"""Extract the unchanged public plate result fields under traction control."""

# pyright: reportMissingImports=false

import json
import sys

from odbAccess import openOdb


def _mean(values):
    if not values:
        raise RuntimeError("result selection produced no values")
    return sum(values) / float(len(values))


def main():
    if len(sys.argv) != 3:
        raise RuntimeError("expected ODB and output JSON arguments")
    odb = openOdb(path=sys.argv[1], readOnly=True)
    try:
        instance = next(iter(odb.rootAssembly.instances.values()))
        step = next(iter(odb.steps.values()))
        frame = step.frames[-1]
        nodes = list(instance.nodes)
        x_values = [float(node.coordinates[0]) for node in nodes]
        x_min, x_max = min(x_values), max(x_values)
        left_labels = {
            int(node.label)
            for node in nodes
            if abs(float(node.coordinates[0]) - x_min) <= 1.0e-6
        }
        right_labels = {
            int(node.label)
            for node in nodes
            if abs(float(node.coordinates[0]) - x_max) <= 1.0e-6
        }
        u_field = frame.fieldOutputs["U"].getSubset(region=instance)
        rf_field = frame.fieldOutputs["RF"].getSubset(region=instance)
        s_field = frame.fieldOutputs["S"].getSubset(region=instance)
        payload = {
            "right_mean_u1": _mean(
                [
                    float(value.data[0])
                    for value in u_field.values
                    if value.nodeLabel in right_labels
                ]
            ),
            "mean_s11": _mean([float(value.data[0]) for value in s_field.values]),
            "left_sum_rf1": sum(
                float(value.data[0])
                for value in rf_field.values
                if value.nodeLabel in left_labels
            ),
        }
        with open(sys.argv[2], "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
