"""Abaqus 2024 standalone-Python oracle for the plate-tension Gold case.

Run with::

    abq2024.bat python extract_plate_tension_oracle.py request.json output.json

This file intentionally uses only the Abaqus embedded Python standard library.
"""

# pyright: reportMissingImports=false

import json
import math
import sys

from odbAccess import openOdb


def _mean(values):
    if not values:
        raise RuntimeError("oracle selection produced no values")
    return sum(values) / float(len(values))


def _near(value, target, scale):
    tolerance = max(abs(scale) * 1.0e-8, 1.0e-9)
    return abs(value - target) <= tolerance


def _select_analysis_step(steps, requested_name=None):
    if requested_name is not None:
        try:
            return requested_name, steps[requested_name]
        except KeyError:
            raise RuntimeError(
                f"requested analysis step {requested_name!r} is not present in the ODB"
            ) from None

    step_names = list(steps.keys())
    if len(step_names) != 1:
        raise RuntimeError(
            "oracle requires exactly one analysis step when step_name is omitted; "
            f"found {len(step_names)}: {step_names}"
        )
    step_name = step_names[0]
    return step_name, steps[step_name]


def main():
    if len(sys.argv) != 3:
        raise RuntimeError("expected request.json and output.json arguments")
    request_path = sys.argv[1]
    output_path = sys.argv[2]
    with open(request_path, encoding="utf-8") as stream:
        request = json.load(stream)

    odb = openOdb(path=request["odb_path"], readOnly=True)
    try:
        step_name, step = _select_analysis_step(
            odb.steps,
            request.get("step_name"),
        )
        if not step.frames:
            raise RuntimeError("selected step contains no frames")
        frame_index = len(step.frames) - 1
        frame = step.frames[frame_index]

        instances = odb.rootAssembly.instances
        if len(instances) != 1:
            raise RuntimeError("oracle expects exactly one part instance")
        instance_name = next(iter(instances.keys()))
        instance = instances[instance_name]
        nodes = list(instance.nodes)
        if not nodes:
            raise RuntimeError("ODB instance contains no nodes")

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
        right_u1 = [
            float(value.data[0])
            for value in u_field.values
            if value.nodeLabel in right_labels
        ]
        rf_field = frame.fieldOutputs["RF"].getSubset(region=instance)
        left_rf1 = [
            float(value.data[0])
            for value in rf_field.values
            if value.nodeLabel in left_labels
        ]
        s_field = frame.fieldOutputs["S"].getSubset(region=instance)
        s11 = [float(value.data[0]) for value in s_field.values]

        element_type_counts = {}
        for element in instance.elements:
            element_type = str(element.type)
            element_type_counts[element_type] = (
                element_type_counts.get(element_type, 0) + 1
            )

        payload = {
            "odb_path": request["odb_path"],
            "step_name": step_name,
            "frame_index": frame_index,
            "instance_name": instance_name,
            "element_type_counts": element_type_counts,
            "right_mean_u1": _mean(right_u1),
            "mean_s11": _mean(s11),
            "left_sum_rf1": sum(left_rf1),
        }
        for key in ("right_mean_u1", "mean_s11", "left_sum_rf1"):
            if not math.isfinite(payload[key]):
                raise RuntimeError(f"oracle produced a non-finite {key}")
        with open(output_path, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
