"""Extract the public B31 beam-tension result summary with Abaqus Python."""

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


def _component_index(field, component_name):
    labels = [str(label) for label in field.componentLabels]
    try:
        return labels.index(component_name)
    except ValueError:
        raise RuntimeError(
            f"field {field.name} does not contain component {component_name}; "
            f"available={labels}"
        ) from None


def main():
    if len(sys.argv) != 3:
        raise RuntimeError("expected ODB path and output JSON path")

    odb_path = sys.argv[1]
    output_path = sys.argv[2]
    odb = openOdb(path=odb_path, readOnly=True)
    try:
        step = odb.steps["Tension"]
        frame = step.frames[-1]
        instance = odb.rootAssembly.instances["BEAM-1"]
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

        u_field = frame.fieldOutputs["U"].getSubset(region=instance)
        u1_index = _component_index(u_field, "U1")
        right_u1 = [
            float(value.data[u1_index])
            for value in u_field.values
            if value.nodeLabel in right_labels
        ]
        rf_field = frame.fieldOutputs["RF"].getSubset(region=instance)
        rf1_index = _component_index(rf_field, "RF1")
        left_rf1 = [
            float(value.data[rf1_index])
            for value in rf_field.values
            if value.nodeLabel in left_labels
        ]
        sf_field = frame.fieldOutputs["SF"].getSubset(region=instance)
        sf1_index = _component_index(sf_field, "SF1")
        sf1 = [float(value.data[sf1_index]) for value in sf_field.values]

        payload = {
            "right_mean_u1": _mean(right_u1),
            "left_sum_rf1": sum(left_rf1),
            "mean_sf1": _mean(sf1),
        }
        with open(output_path, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
