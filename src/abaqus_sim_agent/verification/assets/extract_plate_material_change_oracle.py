"""Host-owned Abaqus 2024 ODB oracle for the two-material plate change."""

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


def _component_index(field, label):
    labels = list(field.componentLabels)
    try:
        return labels.index(label)
    except ValueError:
        raise RuntimeError(
            f"required component {label} is missing from {labels}"
        ) from None


def _connected_elements(elements):
    labels_by_node = {}
    element_labels = set()
    for element in elements:
        label = int(element.label)
        element_labels.add(label)
        for node_label in element.connectivity:
            labels_by_node.setdefault(int(node_label), set()).add(label)
    remaining = set(element_labels)
    if not remaining:
        return False
    stack = [next(iter(remaining))]
    visited = set()
    while stack:
        label = stack.pop()
        if label in visited:
            continue
        visited.add(label)
        element = next(item for item in elements if int(item.label) == label)
        for node_label in element.connectivity:
            stack.extend(labels_by_node[int(node_label)] - visited)
    return visited == element_labels


def main():
    if len(sys.argv) != 3:
        raise RuntimeError("expected request.json and output.json arguments")
    with open(sys.argv[1], encoding="utf-8") as stream:
        request = json.load(stream)

    odb = openOdb(path=request["odb_path"], readOnly=True)
    try:
        step_names = list(odb.steps.keys())
        if len(step_names) != 1:
            raise RuntimeError("oracle requires exactly one analysis step")
        step_name = step_names[0]
        step = odb.steps[step_name]
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
        elements = list(instance.elements)
        if not nodes or not elements:
            raise RuntimeError("ODB instance contains no mesh")

        coordinates = {
            int(node.label): (
                float(node.coordinates[0]),
                float(node.coordinates[1]),
                float(node.coordinates[2]),
            )
            for node in nodes
        }
        x_values = [point[0] for point in coordinates.values()]
        y_values = [point[1] for point in coordinates.values()]
        z_values = [point[2] for point in coordinates.values()]
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        scale = max(abs(x_min), abs(x_max), abs(y_min), abs(y_max), 1.0)
        left_nodes = {
            label
            for label, point in coordinates.items()
            if _near(point[0], x_min, scale)
        }
        right_nodes = {
            label
            for label, point in coordinates.items()
            if _near(point[0], x_max, scale)
        }
        interface_nodes = {
            label
            for label, point in coordinates.items()
            if _near(point[0], float(request["interface_x"]), scale)
        }
        pinned_nodes = {
            label
            for label, point in coordinates.items()
            if _near(point[0], x_min, scale) and _near(point[1], y_min, scale)
        }
        if len(pinned_nodes) != 1:
            raise RuntimeError("oracle could not identify one lower-left node")

        element_type_counts = {}
        element_centroid_x = {}
        element_nodes = {}
        for element in elements:
            label = int(element.label)
            element_type = str(element.type)
            element_type_counts[element_type] = (
                element_type_counts.get(element_type, 0) + 1
            )
            connectivity = tuple(int(value) for value in element.connectivity)
            element_nodes[label] = connectivity
            element_centroid_x[label] = _mean(
                [coordinates[node_label][0] for node_label in connectivity]
            )
        left_elements = {
            label
            for label, centroid_x in element_centroid_x.items()
            if centroid_x < float(request["interface_x"])
        }
        right_elements = set(element_centroid_x) - left_elements
        interface_is_shared = all(
            any(
                label in left_elements
                for label, conn in element_nodes.items()
                if node in conn
            )
            and any(
                label in right_elements
                for label, conn in element_nodes.items()
                if node in conn
            )
            for node in interface_nodes
        )

        property_by_element = {}
        assignment_counts = {}
        for assignment in instance.sectionAssignments:
            section = odb.sections[assignment.sectionName]
            material = odb.materials[section.material]
            if material.elastic is None or not material.elastic.table:
                raise RuntimeError("assigned material has no elastic table")
            elastic_row = material.elastic.table[0]
            properties = (
                float(elastic_row[0]),
                float(elastic_row[1]),
                float(section.thickness),
            )
            for element in assignment.region.elements:
                label = int(element.label)
                assignment_counts[label] = assignment_counts.get(label, 0) + 1
                property_by_element[label] = properties
        element_labels = set(element_nodes)
        assignments_cover_once = (
            set(property_by_element) == element_labels
            and set(assignment_counts) == element_labels
            and all(count == 1 for count in assignment_counts.values())
        )
        left_properties = sorted(
            {property_by_element[label] for label in left_elements}
        )
        right_properties = sorted(
            {property_by_element[label] for label in right_elements}
        )

        field_outputs = tuple(sorted(str(name) for name in frame.fieldOutputs.keys()))
        u_field = frame.fieldOutputs["U"].getSubset(region=instance)
        rf_field = frame.fieldOutputs["RF"].getSubset(region=instance)
        s_field = frame.fieldOutputs["S"].getSubset(region=instance)
        s11_index = _component_index(s_field, "S11")
        s22_index = _component_index(s_field, "S22")
        s12_index = _component_index(s_field, "S12")
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
        left_u1 = [
            float(value.data[0])
            for value in u_field.values
            if value.nodeLabel in left_nodes
        ]
        pinned_u2 = [
            float(value.data[1])
            for value in u_field.values
            if value.nodeLabel in pinned_nodes
        ]
        left_rf1 = [
            float(value.data[0])
            for value in rf_field.values
            if value.nodeLabel in left_nodes
        ]
        right_rf1 = [
            float(value.data[0])
            for value in rf_field.values
            if value.nodeLabel in right_nodes
        ]
        s_values = [
            (int(value.elementLabel), tuple(float(item) for item in value.data))
            for value in s_field.values
        ]
        left_s11 = [
            data[s11_index] for label, data in s_values if label in left_elements
        ]
        right_s11 = [
            data[s11_index] for label, data in s_values if label in right_elements
        ]
        all_s11 = [data[s11_index] for _, data in s_values]
        all_s22 = [data[s22_index] for _, data in s_values]
        all_s12 = [data[s12_index] for _, data in s_values]

        payload = {
            "odb_path": request["odb_path"],
            "step_name": step_name,
            "frame_index": frame_index,
            "analysis_step_count": len(odb.steps),
            "step_procedure": str(step.procedure),
            "step_nlgeom": bool(step.nlgeom),
            "instance_name": instance_name,
            "node_count": len(nodes),
            "element_type_counts": element_type_counts,
            "x_min": x_min,
            "x_max": x_max,
            "y_min": y_min,
            "y_max": y_max,
            "max_abs_z": max(abs(value) for value in z_values),
            "x_layer_count": _layer_count(x_values, scale),
            "y_layer_count": _layer_count(y_values, scale),
            "interface_node_count": len(interface_nodes),
            "interface_is_shared": interface_is_shared,
            "connectivity_is_single_component": _connected_elements(elements),
            "left_element_count": len(left_elements),
            "right_element_count": len(right_elements),
            "assignments_cover_each_element_once": assignments_cover_once,
            "left_properties": left_properties,
            "right_properties": right_properties,
            "field_outputs": field_outputs,
            "u_node_coverage": {int(value.nodeLabel) for value in u_field.values}
            == set(coordinates),
            "s_element_coverage": {int(value.elementLabel) for value in s_field.values}
            == element_labels,
            "right_mean_u1": _mean(right_u1),
            "interface_mean_u1": _mean(interface_u1),
            "max_abs_left_u1": max(abs(value) for value in left_u1),
            "pinned_u2": _mean(pinned_u2),
            "left_sum_rf1": sum(left_rf1),
            "right_sum_rf1": sum(right_rf1),
            "mean_s11": _mean(all_s11),
            "left_mean_s11": _mean(left_s11),
            "right_mean_s11": _mean(right_s11),
            "max_abs_s22": max(abs(value) for value in all_s22),
            "max_abs_s12": max(abs(value) for value in all_s12),
        }
        numeric_values = [
            value
            for key, value in payload.items()
            if key
            not in {
                "odb_path",
                "step_name",
                "step_procedure",
                "instance_name",
                "element_type_counts",
                "left_properties",
                "right_properties",
                "field_outputs",
                "interface_is_shared",
                "connectivity_is_single_component",
                "assignments_cover_each_element_once",
                "u_node_coverage",
                "s_element_coverage",
            }
        ]
        if not all(math.isfinite(float(value)) for value in numeric_values):
            raise RuntimeError("oracle produced a non-finite measurement")
        with open(sys.argv[2], "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
