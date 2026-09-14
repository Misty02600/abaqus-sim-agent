"""Probe Abaqus 2024 ODB field, value, set, and node-array behavior."""

import json
import sys

from odbAccess import openOdb


def attempt(function):
    try:
        value = function()
        return {"status": "ok", "value": value}
    except BaseException as exc:
        return {
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def describe_sequence(value):
    description = {
        "type_name": type(value).__name__,
        "type_module": type(value).__module__,
    }
    description["length"] = attempt(lambda: len(value))
    description["first_type"] = attempt(lambda: type(value[0]).__name__)
    description["first_has_label"] = attempt(lambda: hasattr(value[0], "label"))
    description["first_label"] = attempt(lambda: int(value[0].label))
    description["slice_one"] = attempt(lambda: len(value[:1]))
    description["iteration_count"] = attempt(lambda: sum(1 for _item in value))
    return description


def describe_set(set_object):
    nodes = set_object.nodes
    description = {
        "set_type": type(set_object).__name__,
        "nodes": describe_sequence(nodes),
    }
    description["first_nested_length"] = attempt(lambda: len(nodes[0]))
    description["first_nested_label"] = attempt(lambda: int(nodes[0][0].label))
    return description


def main():
    if len(sys.argv) != 3:
        raise RuntimeError("expected ODB path and output JSON path")
    odb = openOdb(path=sys.argv[1], readOnly=True)
    try:
        step_name = list(odb.steps.keys())[-1]
        frame = odb.steps[step_name].frames[-1]
        instance_name = next(iter(odb.rootAssembly.instances.keys()))
        instance = odb.rootAssembly.instances[instance_name]
        first_node = instance.nodes[0]

        fields = {}
        for key in sorted(frame.fieldOutputs.keys()):
            field = frame.fieldOutputs[key]
            values = field.values
            first = values[0]
            raw_scalar = first.data[0] if hasattr(first.data, "__len__") else first.data
            fields[key] = {
                "type_name": type(field).__name__,
                "field_type_name": type(field.type).__name__,
                "field_type_text": str(field.type),
                "field_type_json": attempt(lambda field=field: json.dumps(field.type)),
                "has_position": hasattr(field, "position"),
                "component_labels": [str(label) for label in field.componentLabels],
                "values": describe_sequence(values),
                "first_value_type": type(first).__name__,
                "first_value_has_component_labels": hasattr(
                    first,
                    "componentLabels",
                ),
                "first_value_position": str(first.position),
                "first_value_section_point_is_none": first.sectionPoint is None,
                "raw_scalar_type": type(raw_scalar).__name__,
                "raw_scalar_json": attempt(
                    lambda raw_scalar=raw_scalar: json.dumps(raw_scalar)
                ),
                "float_scalar_json": attempt(
                    lambda raw_scalar=raw_scalar: json.dumps(float(raw_scalar))
                ),
            }

        u_field = frame.fieldOutputs["U"]
        node_subset = u_field.getSubset(region=first_node)
        instance_sets = {
            name: describe_set(set_object)
            for name, set_object in instance.nodeSets.items()
        }
        assembly_sets = {
            name: describe_set(set_object)
            for name, set_object in odb.rootAssembly.nodeSets.items()
        }
        payload = {
            "schema_version": "1",
            "completed": True,
            "step_name": step_name,
            "instance_name": instance_name,
            "field_keys": sorted(frame.fieldOutputs.keys()),
            "fields": fields,
            "instance_nodes": describe_sequence(instance.nodes),
            "instance_nodes_has_findAt": hasattr(instance.nodes, "findAt"),
            "instance_nodes_public_methods": sorted(
                name
                for name in dir(instance.nodes)
                if not name.startswith("_") and callable(getattr(instance.nodes, name))
            ),
            "instance_node_sets": instance_sets,
            "assembly_node_sets": assembly_sets,
            "u_subset": {
                "component_labels": [
                    str(label) for label in node_subset.componentLabels
                ],
                "value_count": len(node_subset.values),
                "node_label": int(node_subset.values[0].nodeLabel),
            },
        }
        with open(sys.argv[2], "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
    finally:
        odb.close()


if __name__ == "__main__":
    main()
