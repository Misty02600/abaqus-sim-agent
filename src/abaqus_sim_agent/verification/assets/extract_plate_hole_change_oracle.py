"""Host-owned Abaqus 2024 ODB oracle for the seeded plate-hole change case."""

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


def _polygon_area(points):
    area = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        area += point[0] * next_point[1] - next_point[0] * point[1]
    return abs(area) * 0.5


def _boundary_components(boundary_edges):
    adjacency = {}
    for first, second in boundary_edges:
        adjacency.setdefault(first, set()).add(second)
        adjacency.setdefault(second, set()).add(first)
    if any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise RuntimeError("mesh boundary is not a collection of closed loops")

    remaining = set(adjacency)
    components = []
    while remaining:
        start = next(iter(remaining))
        stack = [start]
        component = set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency[node] - component)
        remaining.difference_update(component)
        components.append(component)
    return components


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
            )
            for node in nodes
        }
        x_values = [point[0] for point in coordinates.values()]
        y_values = [point[1] for point in coordinates.values()]
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        scale = max(abs(x_min), abs(x_max), abs(y_min), abs(y_max), 1.0)

        edge_owners = {}
        element_centroids = {}
        element_type_counts = {}
        mesh_area = 0.0
        for element in elements:
            element_type = str(element.type)
            element_type_counts[element_type] = (
                element_type_counts.get(element_type, 0) + 1
            )
            connectivity = [int(label) for label in element.connectivity]
            if len(connectivity) != 4:
                raise RuntimeError("oracle requires four-node quadrilateral elements")
            points = [coordinates[label] for label in connectivity]
            mesh_area += _polygon_area(points)
            element_centroids[int(element.label)] = (
                _mean([point[0] for point in points]),
                _mean([point[1] for point in points]),
            )
            for index, first in enumerate(connectivity):
                second = connectivity[(index + 1) % len(connectivity)]
                edge = tuple(sorted((first, second)))
                edge_owners.setdefault(edge, []).append(int(element.label))

        boundary_edges = [
            edge for edge, owners in edge_owners.items() if len(owners) == 1
        ]
        components = _boundary_components(boundary_edges)
        if len(components) != 2:
            raise RuntimeError(
                "oracle requires exactly an outer boundary and one inner boundary"
            )
        components.sort(
            key=lambda labels: (
                (
                    max(coordinates[label][0] for label in labels)
                    - min(coordinates[label][0] for label in labels)
                )
                * (
                    max(coordinates[label][1] for label in labels)
                    - min(coordinates[label][1] for label in labels)
                )
            ),
            reverse=True,
        )
        hole_nodes = components[1]
        hole_x = [coordinates[label][0] for label in hole_nodes]
        hole_y = [coordinates[label][1] for label in hole_nodes]
        hole_center_x = (min(hole_x) + max(hole_x)) * 0.5
        hole_center_y = (min(hole_y) + max(hole_y)) * 0.5
        requested_center_x = float(request["hole_center_x"])
        requested_center_y = float(request["hole_center_y"])
        requested_radius = float(request["hole_radius"])
        hole_radii = [
            math.hypot(
                coordinates[label][0] - requested_center_x,
                coordinates[label][1] - requested_center_y,
            )
            for label in hole_nodes
        ]
        hole_edges = [
            edge
            for edge in boundary_edges
            if edge[0] in hole_nodes and edge[1] in hole_nodes
        ]
        hole_element_labels = {edge_owners[edge][0] for edge in hole_edges}

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
        s11_values = [float(value.data[0]) for value in s_field.values]
        hole_s11 = [
            float(value.data[0])
            for value in s_field.values
            if value.elementLabel in hole_element_labels
        ]
        far_element_labels = {
            label
            for label, point in element_centroids.items()
            if point[0] <= x_min + 0.2 * (x_max - x_min)
            or point[0] >= x_max - 0.2 * (x_max - x_min)
        }
        far_s11 = [
            float(value.data[0])
            for value in s_field.values
            if value.elementLabel in far_element_labels
        ]

        payload = {
            "odb_path": request["odb_path"],
            "step_name": step_name,
            "frame_index": frame_index,
            "instance_name": instance_name,
            "node_count": len(nodes),
            "element_type_counts": element_type_counts,
            "x_min": x_min,
            "x_max": x_max,
            "y_min": y_min,
            "y_max": y_max,
            "mesh_area": mesh_area,
            "hole_min_node_radius": min(hole_radii),
            "boundary_loop_count": len(components),
            "hole_boundary_edge_count": len(hole_edges),
            "hole_center_x": hole_center_x,
            "hole_center_y": hole_center_y,
            "hole_max_radius_error": max(
                abs(radius - requested_radius) for radius in hole_radii
            ),
            "field_outputs": field_outputs,
            "right_mean_u1": _mean(right_u1),
            "left_mean_u1": _mean(left_u1),
            "pinned_u2": _mean(pinned_u2),
            "left_sum_rf1": sum(left_rf1),
            "right_sum_rf1": sum(right_rf1),
            "mean_s11": _mean(s11_values),
            "far_field_mean_s11": _mean(far_s11),
            "hole_boundary_max_s11": max(hole_s11),
        }
        numeric_values = [
            value
            for key, value in payload.items()
            if key
            not in {
                "odb_path",
                "step_name",
                "instance_name",
                "element_type_counts",
                "field_outputs",
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
