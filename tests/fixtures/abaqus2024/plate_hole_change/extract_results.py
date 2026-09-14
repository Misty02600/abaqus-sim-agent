"""Extract Agent-visible result fields for the plate-hole change reference."""

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
        coordinates = {
            int(node.label): (
                float(node.coordinates[0]),
                float(node.coordinates[1]),
            )
            for node in nodes
        }
        x_min = min(point[0] for point in coordinates.values())
        x_max = max(point[0] for point in coordinates.values())
        left_labels = {
            label
            for label, point in coordinates.items()
            if abs(point[0] - x_min) <= 1.0e-6
        }
        right_labels = {
            label
            for label, point in coordinates.items()
            if abs(point[0] - x_max) <= 1.0e-6
        }

        edge_owners = {}
        for element in instance.elements:
            connectivity = [int(label) for label in element.connectivity]
            for index, first in enumerate(connectivity):
                second = connectivity[(index + 1) % len(connectivity)]
                edge = tuple(sorted((first, second)))
                edge_owners.setdefault(edge, []).append(int(element.label))
        boundary_edges = [
            edge for edge, owners in edge_owners.items() if len(owners) == 1
        ]
        hole_edges = [
            edge
            for edge in boundary_edges
            if all(
                abs(
                    (
                        (coordinates[label][0] - 50.0) ** 2
                        + (coordinates[label][1] - 10.0) ** 2
                    )
                    ** 0.5
                    - 3.0
                )
                <= 0.15
                for label in edge
            )
        ]
        hole_elements = {edge_owners[edge][0] for edge in hole_edges}

        u_field = frame.fieldOutputs["U"].getSubset(region=instance)
        rf_field = frame.fieldOutputs["RF"].getSubset(region=instance)
        s_field = frame.fieldOutputs["S"].getSubset(region=instance)
        right_u1 = [
            float(value.data[0])
            for value in u_field.values
            if value.nodeLabel in right_labels
        ]
        left_rf1 = [
            float(value.data[0])
            for value in rf_field.values
            if value.nodeLabel in left_labels
        ]
        s11 = [float(value.data[0]) for value in s_field.values]
        hole_s11 = [
            float(value.data[0])
            for value in s_field.values
            if value.elementLabel in hole_elements
        ]
        payload = {
            "right_mean_u1": _mean(right_u1),
            "mean_s11": _mean(s11),
            "left_sum_rf1": sum(left_rf1),
            "hole_boundary_max_s11": max(hole_s11),
        }
        with open(sys.argv[2], "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
