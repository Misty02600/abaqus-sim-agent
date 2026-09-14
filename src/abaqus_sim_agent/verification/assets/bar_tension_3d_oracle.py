"""Abaqus 2024 standalone-Python oracle for the 3D bar-tension Gold case.

Run with::

    abq2024.bat python bar_tension_3d_oracle.py request.json output.json

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


def _layer_count(values, scale):
    tolerance = max(abs(scale) * 1.0e-8, 1.0e-9)
    layers = []
    for value in sorted(values):
        if not layers or abs(value - layers[-1]) > tolerance:
            layers.append(value)
    return len(layers)


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
        y_values = [float(node.coordinates[1]) for node in nodes]
        z_values = [float(node.coordinates[2]) for node in nodes]
        x_min = min(x_values)
        x_max = max(x_values)
        y_min = min(y_values)
        y_max = max(y_values)
        z_min = min(z_values)
        z_max = max(z_values)
        axis_scales = (
            max(abs(x_min), abs(x_max), 1.0),
            max(abs(y_min), abs(y_max), 1.0),
            max(abs(z_min), abs(z_max), 1.0),
        )
        left_labels = {
            node.label
            for node in nodes
            if _near(float(node.coordinates[0]), x_min, axis_scales[0])
        }
        right_labels = {
            node.label
            for node in nodes
            if _near(float(node.coordinates[0]), x_max, axis_scales[0])
        }
        y_min_labels = {
            node.label
            for node in nodes
            if _near(float(node.coordinates[1]), y_min, axis_scales[1])
        }
        y_max_labels = {
            node.label
            for node in nodes
            if _near(float(node.coordinates[1]), y_max, axis_scales[1])
        }
        z_min_labels = {
            node.label
            for node in nodes
            if _near(float(node.coordinates[2]), z_min, axis_scales[2])
        }
        z_max_labels = {
            node.label
            for node in nodes
            if _near(float(node.coordinates[2]), z_max, axis_scales[2])
        }

        u_field = frame.fieldOutputs["U"].getSubset(region=instance)
        right_u1 = [
            float(value.data[0])
            for value in u_field.values
            if value.nodeLabel in right_labels
        ]
        y_min_u2 = [
            float(value.data[1])
            for value in u_field.values
            if value.nodeLabel in y_min_labels
        ]
        y_max_u2 = [
            float(value.data[1])
            for value in u_field.values
            if value.nodeLabel in y_max_labels
        ]
        z_min_u3 = [
            float(value.data[2])
            for value in u_field.values
            if value.nodeLabel in z_min_labels
        ]
        z_max_u3 = [
            float(value.data[2])
            for value in u_field.values
            if value.nodeLabel in z_max_labels
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
            "node_count": len(nodes),
            "axis_spans": [x_max - x_min, y_max - y_min, z_max - z_min],
            "coordinate_layer_counts": [
                _layer_count(x_values, axis_scales[0]),
                _layer_count(y_values, axis_scales[1]),
                _layer_count(z_values, axis_scales[2]),
            ],
            "right_mean_u1": _mean(right_u1),
            "mean_s11": _mean(s11),
            "left_sum_rf1": sum(left_rf1),
            "y_face_mean_u2_delta": _mean(y_max_u2) - _mean(y_min_u2),
            "z_face_mean_u3_delta": _mean(z_max_u3) - _mean(z_min_u3),
        }
        for key in (
            "right_mean_u1",
            "mean_s11",
            "left_sum_rf1",
            "y_face_mean_u2_delta",
            "z_face_mean_u3_delta",
        ):
            if not math.isfinite(payload[key]):
                raise RuntimeError(f"oracle produced a non-finite {key}")
        with open(output_path, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
