"""Host-owned Abaqus 2024 ODB oracle for displacement-to-traction change."""

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


def _surface_nodes(surface):
    return [node for node_array in surface.nodes for node in node_array]


def _surface_elements(surface):
    return [element for element_array in surface.elements for element in element_array]


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
        coordinates = {
            int(node.label): (
                float(node.coordinates[0]),
                float(node.coordinates[1]),
            )
            for node in nodes
        }
        x_values = [point[0] for point in coordinates.values()]
        y_values = [point[1] for point in coordinates.values()]
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        scale = max(abs(x_min), abs(x_max), abs(y_min), abs(y_max), 1.0)
        left_labels = {
            label
            for label, point in coordinates.items()
            if _near(point[0], x_min, scale)
        }
        right_labels = {
            label
            for label, point in coordinates.items()
            if _near(point[0], x_max, scale)
        }
        pinned_labels = {
            label
            for label, point in coordinates.items()
            if _near(point[0], x_min, scale) and _near(point[1], y_min, scale)
        }
        if len(pinned_labels) != 1:
            raise RuntimeError("oracle could not identify one lower-left node")

        surface_name = request["surface_name"]
        surfaces = odb.rootAssembly.surfaces
        matching_surface_names = [
            str(name)
            for name in surfaces.keys()
            if str(name).casefold() == str(surface_name).casefold()
        ]
        if len(matching_surface_names) != 1:
            raise RuntimeError(f"loaded surface {surface_name!r} is absent from ODB")
        loaded_surface = surfaces[matching_surface_names[0]]
        surface_nodes = _surface_nodes(loaded_surface)
        surface_node_labels = {int(node.label) for node in surface_nodes}
        surface_elements = _surface_elements(loaded_surface)
        surface_y = [float(node.coordinates[1]) for node in surface_nodes]
        surface_is_full_right_edge = (
            surface_node_labels == right_labels
            and all(
                _near(float(node.coordinates[0]), x_max, scale)
                for node in surface_nodes
            )
            and _near(min(surface_y), y_min, scale)
            and _near(max(surface_y), y_max, scale)
        )

        element_type_counts = {}
        for element in elements:
            element_type = str(element.type)
            element_type_counts[element_type] = (
                element_type_counts.get(element_type, 0) + 1
            )
        material_name = next(iter(odb.materials.keys()))
        material = odb.materials[material_name]
        if material.elastic is None or not material.elastic.table:
            raise RuntimeError("oracle material has no elastic table")
        elastic_row = material.elastic.table[0]
        section_name = next(iter(odb.sections.keys()))
        section = odb.sections[section_name]
        assignments = list(instance.sectionAssignments)
        element_labels = {int(element.label) for element in elements}
        assignment_labels = {
            int(element.label)
            for assignment in assignments
            for element in assignment.region.elements
        }

        field_outputs = tuple(sorted(str(name) for name in frame.fieldOutputs.keys()))
        u_field = frame.fieldOutputs["U"].getSubset(region=instance)
        rf_field = frame.fieldOutputs["RF"].getSubset(region=instance)
        s_field = frame.fieldOutputs["S"].getSubset(region=instance)
        right_u1 = [
            float(value.data[0])
            for value in u_field.values
            if value.nodeLabel in right_labels
        ]
        left_u1 = [
            float(value.data[0])
            for value in u_field.values
            if value.nodeLabel in left_labels
        ]
        pinned_u2 = [
            float(value.data[1])
            for value in u_field.values
            if value.nodeLabel in pinned_labels
        ]
        left_rf1 = [
            float(value.data[0])
            for value in rf_field.values
            if value.nodeLabel in left_labels
        ]
        right_rf1 = [
            float(value.data[0])
            for value in rf_field.values
            if value.nodeLabel in right_labels
        ]
        s11 = [float(value.data[0]) for value in s_field.values]

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
            "surface_name": surface_name,
            "surface_is_full_right_edge": surface_is_full_right_edge,
            "surface_unique_node_count": len(surface_node_labels),
            "surface_element_count": len(
                {int(item.label) for item in surface_elements}
            ),
            "elastic_modulus": float(elastic_row[0]),
            "poisson_ratio": float(elastic_row[1]),
            "section_thickness": float(section.thickness),
            "section_material_name": str(section.material),
            "material_name": str(material_name),
            "section_assignment_count": len(assignments),
            "section_assignment_covers_all_elements": assignment_labels
            == element_labels,
            "field_outputs": field_outputs,
            "right_mean_u1": _mean(right_u1),
            "max_abs_left_u1": max(abs(value) for value in left_u1),
            "pinned_u2": _mean(pinned_u2),
            "left_sum_rf1": sum(left_rf1),
            "right_sum_rf1": sum(right_rf1),
            "mean_s11": _mean(s11),
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
                "surface_name",
                "section_material_name",
                "material_name",
                "element_type_counts",
                "field_outputs",
                "surface_is_full_right_edge",
                "section_assignment_covers_all_elements",
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
