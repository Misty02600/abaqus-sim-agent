"""Abaqus 2024 standalone-Python oracle for the B31 beam-tension case.

Run with::

    abq2024.bat python beam_tension_oracle.py request.json output.json

This file intentionally uses only the Abaqus embedded Python standard library.
"""

# pyright: reportMissingImports=false

import json
import math
import sys
from itertools import pairwise

from odbAccess import openOdb


def _mean(values):
    if not values:
        raise RuntimeError("oracle selection produced no values")
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


def _layer_count(values, scale):
    tolerance = max(abs(scale) * 1.0e-8, 1.0e-9)
    layers = []
    for value in sorted(values):
        if not layers or abs(value - layers[-1]) > tolerance:
            layers.append(value)
    return len(layers)


def _single(repository, label):
    keys = list(repository.keys())
    if len(keys) != 1:
        raise RuntimeError(
            f"oracle expects exactly one {label}; found {len(keys)}: {keys}"
        )
    name = keys[0]
    return name, repository[name]


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

        instance_name, instance = _single(
            odb.rootAssembly.instances,
            "part instance",
        )
        nodes = list(instance.nodes)
        elements = list(instance.elements)
        if not nodes or not elements:
            raise RuntimeError("ODB instance contains no nodes or elements")

        x_values = [float(node.coordinates[0]) for node in nodes]
        y_values = [float(node.coordinates[1]) for node in nodes]
        z_values = [float(node.coordinates[2]) for node in nodes]
        x_min = min(x_values)
        x_max = max(x_values)
        axis_scales = (
            max(abs(x_min), abs(x_max), 1.0),
            max(max(abs(value) for value in y_values), 1.0),
            max(max(abs(value) for value in z_values), 1.0),
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

        u_field = frame.fieldOutputs["U"].getSubset(region=instance)
        u1_index = _component_index(u_field, "U1")
        u2_index = _component_index(u_field, "U2")
        u3_index = _component_index(u_field, "U3")
        right_u1 = [
            float(value.data[u1_index])
            for value in u_field.values
            if value.nodeLabel in right_labels
        ]
        right_u2 = [
            float(value.data[u2_index])
            for value in u_field.values
            if value.nodeLabel in right_labels
        ]
        transverse_u = [
            component
            for value in u_field.values
            for component in (
                float(value.data[u2_index]),
                float(value.data[u3_index]),
            )
        ]

        rf_field = frame.fieldOutputs["RF"].getSubset(region=instance)
        rf1_index = _component_index(rf_field, "RF1")
        rf2_index = _component_index(rf_field, "RF2")
        left_rf1 = [
            float(value.data[rf1_index])
            for value in rf_field.values
            if value.nodeLabel in left_labels
        ]
        right_rf1 = [
            float(value.data[rf1_index])
            for value in rf_field.values
            if value.nodeLabel in right_labels
        ]
        left_rf2 = [
            float(value.data[rf2_index])
            for value in rf_field.values
            if value.nodeLabel in left_labels
        ]
        right_rf2 = [
            float(value.data[rf2_index])
            for value in rf_field.values
            if value.nodeLabel in right_labels
        ]

        rm_field = frame.fieldOutputs["RM"].getSubset(region=instance)
        rm3_index = _component_index(rm_field, "RM3")
        left_rm3 = [
            float(value.data[rm3_index])
            for value in rm_field.values
            if value.nodeLabel in left_labels
        ]
        right_rm3 = [
            float(value.data[rm3_index])
            for value in rm_field.values
            if value.nodeLabel in right_labels
        ]

        sf_field = frame.fieldOutputs["SF"].getSubset(region=instance)
        sf1_index = _component_index(sf_field, "SF1")
        sf2_index = _component_index(sf_field, "SF2")
        sf3_index = _component_index(sf_field, "SF3")
        sf1 = [float(value.data[sf1_index]) for value in sf_field.values]
        transverse_sf = [
            component
            for value in sf_field.values
            for component in (
                float(value.data[sf2_index]),
                float(value.data[sf3_index]),
            )
        ]
        section_shear = [
            math.sqrt(
                float(value.data[sf2_index]) ** 2 + float(value.data[sf3_index]) ** 2
            )
            for value in sf_field.values
        ]
        sf_element_labels = {
            int(value.elementLabel)
            for value in sf_field.values
            if value.elementLabel is not None
        }
        element_labels = {int(element.label) for element in elements}
        sm_field = frame.fieldOutputs["SM"].getSubset(region=instance)
        sm1_index = _component_index(sm_field, "SM1")
        sm2_index = _component_index(sm_field, "SM2")
        sm3_index = _component_index(sm_field, "SM3")
        section_moment = [
            math.sqrt(
                float(value.data[sm1_index]) ** 2
                + float(value.data[sm2_index]) ** 2
                + float(value.data[sm3_index]) ** 2
            )
            for value in sm_field.values
        ]
        sm_element_labels = {
            int(value.elementLabel)
            for value in sm_field.values
            if value.elementLabel is not None
        }
        nodes_by_x = sorted(nodes, key=lambda node: float(node.coordinates[0]))
        expected_connections = {
            frozenset((int(left.label), int(right.label)))
            for left, right in pairwise(nodes_by_x)
        }
        actual_connections = {
            frozenset(int(label) for label in element.connectivity)
            for element in elements
        }
        connectivity_is_x_chain = (
            all(len(connection) == 2 for connection in actual_connections)
            and actual_connections == expected_connections
        )

        element_type_counts = {}
        for element in elements:
            element_type = str(element.type)
            element_type_counts[element_type] = (
                element_type_counts.get(element_type, 0) + 1
            )

        material_name, material = _single(odb.materials, "material")
        if material.elastic is None or not material.elastic.table:
            raise RuntimeError("oracle material has no elastic table")
        elastic_row = material.elastic.table[0]

        profile_name, profile = _single(odb.profiles, "beam profile")
        section_name, beam_section = _single(odb.sections, "beam section")
        section_assignments = list(instance.sectionAssignments)
        if len(section_assignments) != 1:
            raise RuntimeError("oracle expects exactly one section assignment")
        section_assignment = section_assignments[0]
        assignment_element_labels = {
            int(element.label) for element in section_assignment.region.elements
        }
        orientations = list(instance.beamOrientations)
        if len(orientations) != 1:
            raise RuntimeError("oracle expects exactly one beam orientation")
        orientation = orientations[0]
        orientation_element_labels = {
            int(element.label) for element in orientation.region.elements
        }
        orientation_vector = tuple(float(value) for value in orientation.vector)

        payload = {
            "odb_path": request["odb_path"],
            "step_name": step_name,
            "frame_index": frame_index,
            "analysis_step_count": len(odb.steps),
            "step_procedure": str(step.procedure),
            "step_nlgeom": bool(step.nlgeom),
            "instance_name": instance_name,
            "element_type_counts": element_type_counts,
            "node_count": len(nodes),
            "connectivity_is_x_chain": connectivity_is_x_chain,
            "coordinate_layer_counts": [
                _layer_count(x_values, axis_scales[0]),
                _layer_count(y_values, axis_scales[1]),
                _layer_count(z_values, axis_scales[2]),
            ],
            "x_coordinates": sorted(x_values),
            "x_span": x_max - x_min,
            "max_abs_transverse_coordinate": max(
                max(abs(value) for value in y_values),
                max(abs(value) for value in z_values),
            ),
            "right_mean_u1": _mean(right_u1),
            "right_mean_u2": _mean(right_u2),
            "max_abs_transverse_u": max(abs(value) for value in transverse_u),
            "left_sum_rf1": sum(left_rf1),
            "right_sum_rf1": sum(right_rf1),
            "left_sum_rf2": sum(left_rf2),
            "right_sum_rf2": sum(right_rf2),
            "left_sum_rm3": sum(left_rm3),
            "right_sum_rm3": sum(right_rm3),
            "mean_sf1": _mean(sf1),
            "sf1_min": min(sf1),
            "sf1_max": max(sf1),
            "sf1_value_count": len(sf1),
            "sf1_element_coverage": sf_element_labels == element_labels,
            "sf1_has_mixed_signs": any(value > 0.0 for value in sf1)
            and any(value < 0.0 for value in sf1),
            "max_abs_transverse_sf": max(abs(value) for value in transverse_sf),
            "max_section_shear": max(section_shear),
            "max_section_moment": max(section_moment),
            "sm_value_count": len(section_moment),
            "sm_element_coverage": sm_element_labels == element_labels,
            "material_name": str(material_name),
            "elastic_e": float(elastic_row[0]),
            "elastic_nu": float(elastic_row[1]),
            "profile_name": str(profile_name),
            "profile_a": float(profile.a),
            "profile_b": float(profile.b),
            "section_name": str(section_name),
            "section_material_name": str(beam_section.material),
            "section_profile_name": str(beam_section.profile),
            "section_integration": str(beam_section.integration),
            "section_assignment_name": str(section_assignment.sectionName),
            "section_assignment_covers_all_elements": (
                assignment_element_labels == element_labels
            ),
            "orientation_method": str(orientation.method),
            "orientation_vector": orientation_vector,
            "orientation_covers_all_elements": (
                orientation_element_labels == element_labels
            ),
        }
        for key in (
            "x_span",
            "max_abs_transverse_coordinate",
            "right_mean_u1",
            "right_mean_u2",
            "max_abs_transverse_u",
            "left_sum_rf1",
            "right_sum_rf1",
            "left_sum_rf2",
            "right_sum_rf2",
            "left_sum_rm3",
            "right_sum_rm3",
            "mean_sf1",
            "sf1_min",
            "sf1_max",
            "max_abs_transverse_sf",
            "max_section_shear",
            "max_section_moment",
            "elastic_e",
            "elastic_nu",
            "profile_a",
            "profile_b",
        ):
            if not math.isfinite(payload[key]):
                raise RuntimeError(f"oracle produced a non-finite {key}")
        with open(output_path, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
