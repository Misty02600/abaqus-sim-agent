"""Extract the public B31 cantilever-bending result summary."""

import json
import math
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

    odb = openOdb(path=sys.argv[1], readOnly=True)
    try:
        step = next(iter(odb.steps.values()))
        frame = step.frames[-1]
        instance = next(iter(odb.rootAssembly.instances.values()))
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
        u2_index = _component_index(u_field, "U2")
        right_u2 = [
            float(value.data[u2_index])
            for value in u_field.values
            if value.nodeLabel in right_labels
        ]
        rf_field = frame.fieldOutputs["RF"].getSubset(region=instance)
        rf2_index = _component_index(rf_field, "RF2")
        left_rf2 = [
            float(value.data[rf2_index])
            for value in rf_field.values
            if value.nodeLabel in left_labels
        ]
        rm_field = frame.fieldOutputs["RM"].getSubset(region=instance)
        rm3_index = _component_index(rm_field, "RM3")
        left_rm3 = [
            float(value.data[rm3_index])
            for value in rm_field.values
            if value.nodeLabel in left_labels
        ]
        sm_field = frame.fieldOutputs["SM"].getSubset(region=instance)
        sm1_index = _component_index(sm_field, "SM1")
        sm2_index = _component_index(sm_field, "SM2")
        sm3_index = _component_index(sm_field, "SM3")
        moment_magnitudes = [
            math.sqrt(
                float(value.data[sm1_index]) ** 2
                + float(value.data[sm2_index]) ** 2
                + float(value.data[sm3_index]) ** 2
            )
            for value in sm_field.values
        ]

        payload = {
            "right_mean_u2": _mean(right_u2),
            "left_sum_rf2": sum(left_rf2),
            "left_sum_rm3": sum(left_rm3),
            "max_section_moment": max(moment_magnitudes),
        }
        with open(sys.argv[2], "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
