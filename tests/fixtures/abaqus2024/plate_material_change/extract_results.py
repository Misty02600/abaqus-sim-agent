"""Extract Agent-visible results for the two-material plate reference."""

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
        coordinates = {
            int(node.label): (
                float(node.coordinates[0]),
                float(node.coordinates[1]),
            )
            for node in instance.nodes
        }
        left_nodes = {
            label for label, point in coordinates.items() if abs(point[0]) <= 1.0e-6
        }
        right_nodes = {
            label
            for label, point in coordinates.items()
            if abs(point[0] - 100.0) <= 1.0e-6
        }
        interface_nodes = {
            label
            for label, point in coordinates.items()
            if abs(point[0] - 50.0) <= 1.0e-6
        }
        element_side = {}
        for element in instance.elements:
            x_mean = _mean(
                [coordinates[int(label)][0] for label in element.connectivity]
            )
            element_side[int(element.label)] = "left" if x_mean < 50.0 else "right"

        u_field = frame.fieldOutputs["U"].getSubset(region=instance)
        rf_field = frame.fieldOutputs["RF"].getSubset(region=instance)
        s_field = frame.fieldOutputs["S"].getSubset(region=instance)
        right_u1 = [
            float(value.data[0])
            for value in u_field.values
            if value.nodeLabel in right_nodes
        ]
        interface_u1 = [
            float(value.data[0])
            for value in u_field.values
            if value.nodeLabel in interface_nodes
        ]
        left_rf1 = [
            float(value.data[0])
            for value in rf_field.values
            if value.nodeLabel in left_nodes
        ]
        s11 = [float(value.data[0]) for value in s_field.values]
        left_s11 = [
            float(value.data[0])
            for value in s_field.values
            if element_side[int(value.elementLabel)] == "left"
        ]
        right_s11 = [
            float(value.data[0])
            for value in s_field.values
            if element_side[int(value.elementLabel)] == "right"
        ]
        payload = {
            "right_mean_u1": _mean(right_u1),
            "interface_mean_u1": _mean(interface_u1),
            "mean_s11": _mean(s11),
            "left_mean_s11": _mean(left_s11),
            "right_mean_s11": _mean(right_s11),
            "left_sum_rf1": sum(left_rf1),
        }
        with open(sys.argv[2], "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
