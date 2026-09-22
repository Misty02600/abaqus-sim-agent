"""宿主独立 ODB 读取器；通过 trusted Abaqus Python 调用。"""

# pyright: reportMissingImports=false
import json
import math
import re
import sys

from abaqusConstants import ELEMENT_NODAL, INTEGRATION_POINT, ON
from odbAccess import openOdb


def component(frame, field_name, label, node_labels=None):
    field = frame.fieldOutputs[field_name]
    index = list(field.componentLabels).index(label)
    return [
        float(v.data[index])
        for v in field.values
        if node_labels is None or v.nodeLabel in node_labels
    ]


def mean(values):
    if not values:
        raise ValueError("empty oracle selection")
    return sum(values) / len(values)


def polygon_area(points):
    if len(points) < 3:
        raise ValueError("polygon needs at least three points")
    twice_area = 0.0
    for i, point in enumerate(points):
        next_point = points[(i + 1) % len(points)]
        twice_area += point[0] * next_point[1] - next_point[0] * point[1]
    area = abs(twice_area) / 2.0
    if area <= 0.0:
        raise ValueError("nonpositive element cross-section area")
    return area


def area_weighted_axisymmetric_stress(stress, elements, nodes, component_index):
    by_element = {}
    for value in stress.values:
        by_element.setdefault(int(value.elementLabel), []).append(
            float(value.data[component_index])
        )

    weighted_sum = 0.0
    total_area = 0.0
    for label, values in by_element.items():
        element = elements[label]
        element_type = element["type"]
        corner_count = 4 if element_type in {"CAX4", "CAX4R", "CAX8", "CAX8R"} else 0
        if not corner_count:
            raise ValueError(f"unsupported axisymmetric element type: {element_type}")
        corners = [nodes[n][:2] for n in element["nodes"][:corner_count]]
        area = polygon_area(corners)
        weighted_sum += area * mean(values)
        total_area += area
    if total_area <= 0.0:
        raise ValueError("empty axisymmetric stress selection")
    return weighted_sum / total_area


def in_plane_max_principal(s11, s22, s12):
    center = 0.5 * (s11 + s22)
    radius = math.hypot(0.5 * (s11 - s22), s12)
    return center + radius


def shell_midsurface_component(values, component_index):
    section_values = [
        value for value in values if getattr(value, "sectionPoint", None) is not None
    ]
    if not section_values:
        raise ValueError("shell section-point stress output required")

    midpoint = []
    negative = []
    positive = []
    by_number = {}
    for value in section_values:
        point = value.sectionPoint
        number = int(point.number)
        by_number.setdefault(number, []).append(value)
        description = str(getattr(point, "description", "")).lower()
        match = re.search(r"fraction\s*=\s*([-+0-9.eE]+)", description)
        fraction = float(match.group(1)) if match else None
        if "smid" in description or (fraction is not None and abs(fraction) < 1e-12):
            midpoint.append(value)
        elif "sneg" in description or (fraction is not None and fraction < 0.0):
            negative.append(value)
        elif "spos" in description or (fraction is not None and fraction > 0.0):
            positive.append(value)

    if midpoint:
        return mean([float(value.data[component_index]) for value in midpoint])
    if not negative or not positive:
        numbers = sorted(by_number)
        if len(numbers) < 2:
            raise ValueError("shell midsurface stress needs two surface section points")
        negative = by_number[numbers[0]]
        positive = by_number[numbers[-1]]
    negative_mean = mean([float(value.data[component_index]) for value in negative])
    positive_mean = mean([float(value.data[component_index]) for value in positive])
    return 0.5 * (negative_mean + positive_mean)


def shell_outer_component(values, component_index):
    outer = []
    for value in values:
        point = getattr(value, "sectionPoint", None)
        if point is None:
            continue
        description = str(getattr(point, "description", "")).lower()
        match = re.search(r"fraction\s*=\s*([-+0-9.eE]+)", description)
        fraction = float(match.group(1)) if match else None
        if "spos" in description or (fraction is not None and fraction > 0.0):
            outer.append(value)
    if not outer:
        raise ValueError("explicit SPOS/fraction + shell stress output required")
    return mean([float(value.data[component_index]) for value in outer])


def shell_gauss_surface_component(values, component_index, target_fraction):
    by_fraction = {}
    for value in values:
        point = getattr(value, "sectionPoint", None)
        if point is None:
            continue
        description = str(getattr(point, "description", "")).lower()
        match = re.search(r"fraction\s*=\s*([-+0-9.eE]+)", description)
        if not match:
            continue
        fraction = float(match.group(1))
        by_fraction.setdefault(fraction, []).append(float(value.data[component_index]))
    if len(by_fraction) < 2:
        raise ValueError(
            "Gauss shell stress needs at least two section-point fractions"
        )
    points = sorted(
        (fraction, mean(samples)) for fraction, samples in by_fraction.items()
    )
    mean_fraction = mean([fraction for fraction, _ in points])
    mean_value = mean([value for _, value in points])
    denominator = sum((fraction - mean_fraction) ** 2 for fraction, _ in points)
    if denominator <= 0.0:
        raise ValueError("degenerate shell section-point fractions")
    slope = (
        sum(
            (fraction - mean_fraction) * (value - mean_value)
            for fraction, value in points
        )
        / denominator
    )
    return mean_value + slope * (target_fraction - mean_fraction)


def skew_coordinates(point, angle_degrees):
    angle = math.radians(angle_degrees)
    sine = math.sin(angle)
    if abs(sine) < 1e-12:
        raise ValueError("degenerate skew angle")
    eta = point[1] / sine
    xi = point[0] - eta * math.cos(angle)
    return xi, eta


def inspect(path, c):
    odb = openOdb(path=path, readOnly=True)
    try:
        if len(odb.rootAssembly.instances) != 1:
            raise ValueError("one deformable instance required")
        instance = next(iter(odb.rootAssembly.instances.values()))
        nodes = {}
        for n in instance.nodes:
            coordinates = [float(x) for x in n.coordinates]
            if len(coordinates) == 2:
                coordinates.append(0.0)
            if len(coordinates) != 3:
                raise ValueError("unsupported node coordinate dimension")
            nodes[int(n.label)] = coordinates
        elements = {
            int(e.label): {"type": str(e.type), "nodes": list(e.connectivity)}
            for e in instance.elements
        }
        eps = max(c["length"] * 1e-7, 1e-9)
        if c["family"] == "axisymmetric_shell":
            if c.get("axisymmetric_shell_profile") == "hyperbolic":
                left = {
                    n
                    for n, p in nodes.items()
                    if abs(p[0] - c["radius"]) < eps and abs(p[1]) < eps
                }
                right = {
                    n
                    for n, p in nodes.items()
                    if abs(p[0] - c["width"]) < 2e-4 and abs(p[1] - c["length"]) < eps
                }
            elif c.get("axisymmetric_shell_profile") == "sphere_cylinder":
                left = {
                    n
                    for n, p in nodes.items()
                    if abs(p[0] - c["radius"]) < eps
                    and abs(p[1] - c["sample_axial_position"]) < eps
                }
                right = {
                    n
                    for n, p in nodes.items()
                    if abs(p[0] - c["radius"]) < eps and abs(p[1]) < eps
                }
            elif c.get("axisymmetric_shell_profile") == "crown_knuckle_cylinder":
                left = {
                    n
                    for n, p in nodes.items()
                    if abs(p[0] - c["sample_radius"]) < eps
                    and abs(p[1] - c["sample_axial_position"]) < eps
                }
                right = {
                    n
                    for n, p in nodes.items()
                    if abs(p[0] - c["radius"]) < eps and abs(p[1]) < eps
                }
            elif c.get("axisymmetric_shell_profile") == "branched_sphere_cylinder":
                junction = {
                    n
                    for n, p in nodes.items()
                    if abs(p[0] - c["sample_radius"]) < eps
                    and abs(p[1] - c["sample_axial_position"]) < eps
                }
                if len(junction) != 2:
                    raise ValueError("two coincident branched-shell C nodes required")
                cylinder_nodes = set()
                sphere_nodes = set()
                for node in junction:
                    incident = [
                        element
                        for element in elements.values()
                        if node in element["nodes"]
                    ]
                    if any(
                        all(
                            abs(nodes[int(label)][0] - c["radius"]) < 2e-4
                            for label in element["nodes"]
                        )
                        for element in incident
                    ):
                        cylinder_nodes.add(node)
                    if any(
                        all(
                            abs(
                                nodes[int(label)][0] ** 2
                                + (
                                    nodes[int(label)][1]
                                    - c["branch_center_axial_position"]
                                )
                                ** 2
                                - c["branch_sphere_radius"] ** 2
                            )
                            < 2e-4
                            for label in element["nodes"]
                        )
                        for element in incident
                    ):
                        sphere_nodes.add(node)
                if len(cylinder_nodes) != 1 or len(sphere_nodes) != 1:
                    raise ValueError(
                        "branched-shell C nodes do not separate the two branches"
                    )
                left = cylinder_nodes
                right = {
                    n
                    for n, p in nodes.items()
                    if abs(p[0] - c["radius"]) < eps and abs(p[1]) < eps
                }
            else:
                raise ValueError("unsupported axisymmetric-shell profile")
        elif c["family"] == "cylindrical_shell_patch":
            angle_eps = max(c["radius"] * 2e-5, eps)
            max_angle = math.radians(c["arc_angle_degrees"])
            left = {
                n for n, p in nodes.items() if abs(math.atan2(p[1], p[0])) < angle_eps
            }
            right = {
                n
                for n, p in nodes.items()
                if abs(math.atan2(p[1], p[0]) - max_angle) < angle_eps
            }
        else:
            x_min = (
                c.get("inner_radius", 0.0)
                if c["family"] == "axisymmetric_pressure"
                else 0.0
            )
            left = {n for n, p in nodes.items() if abs(p[0] - x_min) < eps}
            right = {n for n, p in nodes.items() if abs(p[0] - c["length"]) < eps}
        if not left or not right:
            raise ValueError("missing end nodes")
        frames = [s.frames[-1] for s in odb.steps.values()]
        values = {}
        if c["family"] == "truss":
            frame = frames[-1]
            values["tip_u1"] = mean(component(frame, "U", "U1", right))
            values["reaction_x"] = sum(component(frame, "RF", "RF1", left))
            stress = frame.fieldOutputs["S"].getSubset(position=INTEGRATION_POINT)
            index = list(stress.componentLabels).index("S11")
            if len(c["segments"]) == 1:
                values["mean_s11"] = mean([float(v.data[index]) for v in stress.values])
            else:
                interface = {n for n, p in nodes.items() if abs(p[0] - 400) < eps}
                values["interface_u1"] = mean(component(frame, "U", "U1", interface))
                for i in (1, 2):
                    labels = {
                        e
                        for e, d in elements.items()
                        if (mean([nodes[n][0] for n in d["nodes"]]) < 400) == (i == 1)
                    }
                    values[f"segment_{i}_s11"] = mean(
                        [
                            float(v.data[index])
                            for v in stress.values
                            if v.elementLabel in labels
                        ]
                    )
        elif c["family"] == "beam":
            frame = frames[-1]
            values.update(
                tip_u2=mean(component(frame, "U", "U2", right)),
                tip_ur3=mean(component(frame, "UR", "UR3", right)),
                reaction_y=sum(component(frame, "RF", "RF2", left)),
                reaction_mz=sum(component(frame, "RM", "RM3", left)),
            )
            inertia = c["width"] * c["height"] ** 3 / 12
            values["root_bending_stress"] = (
                abs(values["reaction_mz"]) * c["height"] / 2 / inertia
            )
        elif c["family"] == "shaft":
            frame = frames[-1]
            values.update(
                tip_ur1=mean(component(frame, "UR", "UR1", right)),
                reaction_mx=sum(component(frame, "RM", "RM1", left)),
            )
            diameter = c["width"]
            polar_moment = math.pi * diameter**4 / 32.0
            values["surface_shear_nominal"] = (
                abs(values["reaction_mx"]) * diameter / 2.0 / polar_moment
            )
        elif c["family"] == "plane_stress":
            frame = frames[-1]
            stress = frame.fieldOutputs["S"].getSubset(position=INTEGRATION_POINT)
            index = list(stress.componentLabels).index("S11")
            values.update(
                tip_u1=mean(component(frame, "U", "U1", right)),
                reaction_x=sum(component(frame, "RF", "RF1", left)),
                mean_s11=mean([float(v.data[index]) for v in stress.values]),
            )
        elif c["family"] == "axisymmetric_pressure":
            frame = frames[-1]
            stress = frame.fieldOutputs["S"].getSubset(position=INTEGRATION_POINT)
            radial_index = list(stress.componentLabels).index("S11")
            hoop_index = list(stress.componentLabels).index("S33")
            values.update(
                inner_u1=mean(component(frame, "U", "U1", left)),
                outer_u1=mean(component(frame, "U", "U1", right)),
                area_mean_s11=area_weighted_axisymmetric_stress(
                    stress, elements, nodes, radial_index
                ),
                area_mean_s33=area_weighted_axisymmetric_stress(
                    stress, elements, nodes, hoop_index
                ),
            )
        elif c["family"] == "axisymmetric_shell":
            frame = frames[-1]
            if len(left) != 1 or len(right) != 1:
                raise ValueError(
                    "one exact axisymmetric-shell QoI/support node required"
                )
            stress = frame.fieldOutputs["S"].getSubset(
                position=ELEMENT_NODAL, readOnly=ON
            )
            labels = list(stress.componentLabels)
            s11_index = labels.index("S11")
            selected_values = [
                value for value in stress.values if value.nodeLabel in left
            ]
            if c.get("axisymmetric_shell_profile") == "branched_sphere_cylinder":
                upper_cylinder_elements = {
                    label
                    for label, element in elements.items()
                    if any(node in left for node in element["nodes"])
                    and all(
                        abs(nodes[int(node)][0] - c["radius"]) < 2e-4
                        for node in element["nodes"]
                    )
                    and mean([nodes[int(node)][1] for node in element["nodes"]])
                    > c["sample_axial_position"] + eps
                }
                if not upper_cylinder_elements:
                    raise ValueError("upper-cylinder element at C required")
                selected_values = [
                    value
                    for value in selected_values
                    if int(value.elementLabel) in upper_cylinder_elements
                ]
            if c.get("axisymmetric_shell_profile") == "hyperbolic":
                s22_index = labels.index("S22")
                values.update(
                    k_meridional_s11=shell_midsurface_component(
                        selected_values, s11_index
                    ),
                    k_hoop_s22=shell_midsurface_component(selected_values, s22_index),
                    reaction_z=sum(component(frame, "RF", "RF2", left)),
                )
            elif c.get("axisymmetric_shell_profile") == "sphere_cylinder":
                values.update(
                    d_outer_s11=shell_outer_component(selected_values, s11_index),
                    reaction_z=sum(component(frame, "RF", "RF2", right)),
                )
            elif c.get("axisymmetric_shell_profile") == "crown_knuckle_cylinder":
                s22_index = labels.index("S22")
                values.update(
                    d_outer_s22=shell_outer_component(selected_values, s22_index),
                    reaction_z=sum(component(frame, "RF", "RF2", right)),
                )
            elif c.get("axisymmetric_shell_profile") == "branched_sphere_cylinder":
                values.update(
                    c_outer_s11=shell_gauss_surface_component(
                        selected_values, s11_index, -1.0
                    ),
                    reaction_z=sum(component(frame, "RF", "RF2", right)),
                )
            else:
                raise ValueError("unsupported axisymmetric-shell profile")
        elif c["family"] == "skew_plate":
            frame = frames[-1]
            center_point = (
                0.5
                * (
                    c["length"]
                    + c["width"] * math.cos(math.radians(c["skew_angle_degrees"]))
                ),
                0.5 * c["width"] * math.sin(math.radians(c["skew_angle_degrees"])),
            )
            center_nodes = {
                n
                for n, p in nodes.items()
                if math.hypot(p[0] - center_point[0], p[1] - center_point[1]) < eps
            }
            if len(center_nodes) != 1:
                raise ValueError("one exact skew-plate center node required")
            boundary_nodes = set()
            for n, p in nodes.items():
                xi, eta = skew_coordinates(p, c["skew_angle_degrees"])
                if (
                    abs(xi) < eps
                    or abs(xi - c["length"]) < eps
                    or abs(eta) < eps
                    or abs(eta - c["width"]) < eps
                ):
                    boundary_nodes.add(n)
            stress = frame.fieldOutputs["S"].getSubset(
                position=ELEMENT_NODAL, readOnly=ON
            )
            center_values = [v for v in stress.values if v.nodeLabel in center_nodes]
            section_numbers = [
                int(v.sectionPoint.number)
                for v in center_values
                if getattr(v, "sectionPoint", None) is not None
            ]
            if not section_numbers:
                raise ValueError("shell section-point stress output required")
            lower = [
                v
                for v in center_values
                if getattr(v, "sectionPoint", None) is not None
                and (
                    "sneg" in str(getattr(v.sectionPoint, "description", "")).lower()
                    or "fraction = -"
                    in str(getattr(v.sectionPoint, "description", "")).lower()
                )
            ]
            if not lower:
                lower_number = min(section_numbers)
                lower = [
                    v
                    for v in center_values
                    if getattr(v, "sectionPoint", None) is not None
                    and int(v.sectionPoint.number) == lower_number
                ]
            labels = list(stress.componentLabels)
            indices = [labels.index(name) for name in ("S11", "S22", "S12")]
            s11, s22, s12 = [
                mean([float(v.data[index]) for v in lower]) for index in indices
            ]
            values.update(
                center_lower_max_principal=in_plane_max_principal(s11, s22, s12),
                reaction_z=sum(component(frame, "RF", "RF3", boundary_nodes)),
            )
        elif c["family"] == "hemisphere_shell":
            frame = frames[-1]
            a_nodes = {
                n
                for n, p in nodes.items()
                if abs(p[0] - c["radius"]) < eps and abs(p[1]) < eps and abs(p[2]) < eps
            }
            if len(a_nodes) != 1:
                raise ValueError("one exact hemisphere A node required")
            values["a_u1"] = mean(component(frame, "U", "U1", a_nodes))
        elif c["family"] == "z_section_shell":
            frame = frames[-1]
            a_nodes = {
                n
                for n, p in nodes.items()
                if abs(p[0] - 2.5) < eps
                and abs(p[1] + 1.0) < eps
                and abs(p[2] + 1.0) < eps
            }
            if len(a_nodes) != 1:
                raise ValueError("one exact Z-section A node required")
            stress = frame.fieldOutputs["S"].getSubset(
                position=ELEMENT_NODAL, readOnly=ON
            )
            s11_index = list(stress.componentLabels).index("S11")
            a_values = [value for value in stress.values if value.nodeLabel in a_nodes]
            values["a_sigma_xx"] = shell_midsurface_component(a_values, s11_index)
        elif c["family"] == "cylindrical_shell_patch":
            frame = frames[-1]
            sample_angle = math.radians(c["sample_angle_degrees"])
            target = (
                c["radius"] * math.cos(sample_angle),
                c["radius"] * math.sin(sample_angle),
                c["sample_axial_position"],
            )
            sample_tol = max(c["radius"] * 2e-5, eps)
            e_nodes = {
                n for n, point in nodes.items() if math.dist(point, target) < sample_tol
            }
            if len(e_nodes) != 1:
                raise ValueError("one exact cylindrical-shell E node required")
            stress = frame.fieldOutputs["S"].getSubset(
                position=ELEMENT_NODAL, readOnly=ON
            )
            s11_index = list(stress.componentLabels).index("S11")
            e_values = [value for value in stress.values if value.nodeLabel in e_nodes]
            values.update(
                e_outer_s11=shell_outer_component(e_values, s11_index),
                reaction_m3=sum(component(frame, "RM", "RM3", left)),
            )
        elif c["family"] == "elliptic_membrane":
            frame = frames[-1]
            d_nodes = {
                n
                for n, p in nodes.items()
                if abs(p[0] - c["inner_semi_axis_x"]) < eps and abs(p[1]) < eps
            }
            if len(d_nodes) != 1:
                raise ValueError("one exact elliptic-membrane D node required")
            stress = frame.fieldOutputs["S"].getSubset(
                position=ELEMENT_NODAL, readOnly=ON
            )
            s22_index = list(stress.componentLabels).index("S22")
            d_values = [
                float(v.data[s22_index])
                for v in stress.values
                if v.nodeLabel in d_nodes
            ]
            x_sym = {n for n, p in nodes.items() if abs(p[0]) < eps}
            y_sym = {n for n, p in nodes.items() if abs(p[1]) < eps}
            values.update(
                d_sigma_yy=mean(d_values),
                reaction_x=sum(component(frame, "RF", "RF1", x_sym)),
                reaction_y=sum(component(frame, "RF", "RF2", y_sym)),
            )
        elif c["family"] == "thick_plate":
            frame = frames[-1]
            d_nodes = {
                n
                for n, p in nodes.items()
                if abs(p[0] - c["inner_semi_axis_x"]) < eps
                and abs(p[1]) < eps
                and abs(p[2] - c["height"]) < eps
            }
            if len(d_nodes) != 1:
                raise ValueError("one exact thick-plate top D node required")
            stress = frame.fieldOutputs["S"].getSubset(
                position=ELEMENT_NODAL, readOnly=ON
            )
            s22_index = list(stress.componentLabels).index("S22")
            d_values = [
                float(v.data[s22_index])
                for v in stress.values
                if v.nodeLabel in d_nodes
            ]
            if not d_values:
                raise ValueError("thick-plate D-node stress output required")
            outer_mid = {
                n
                for n, p in nodes.items()
                if abs((p[0] / c["length"]) ** 2 + (p[1] / c["width"]) ** 2 - 1.0)
                <= 5e-4
                and abs(p[2] - c["height"] / 2.0) < eps
            }
            if not outer_mid:
                raise ValueError("thick-plate outer mid-plane support nodes required")
            values.update(
                d_sigma_yy=mean(d_values),
                reaction_z=sum(component(frame, "RF", "RF3", outer_mid)),
            )
        elif c["family"] == "hole":
            frame = frames[-1]
            surface = {
                n
                for n, p in nodes.items()
                if abs(math.hypot(p[0] - 7.5, p[1] - 2.5) - 0.5) < 1e-5
            }
            field = frame.fieldOutputs["S"].getSubset(
                position=ELEMENT_NODAL, readOnly=ON
            )
            index = list(field.componentLabels).index("S11")
            values["hole_max_s11"] = max(
                float(v.data[index]) for v in field.values if v.nodeLabel in surface
            )
            values["reaction_x"] = sum(component(frame, "RF", "RF1", left))
        elif c["family"] == "thermal_solid":
            frame = frames[-1]
            a_nodes = {
                n
                for n, p in nodes.items()
                if math.dist(p, (1.0, 0.0, 0.0)) < max(3e-4, eps)
            }
            if len(a_nodes) != 1:
                raise ValueError("one exact thermal-solid A node required")
            stress = frame.fieldOutputs["S"].getSubset(
                position=ELEMENT_NODAL, readOnly=ON
            )
            s33_index = list(stress.componentLabels).index("S33")
            a_values = [
                float(value.data[s33_index])
                for value in stress.values
                if value.nodeLabel in a_nodes
            ]
            values["a_sigma_zz"] = mean(a_values)
        else:
            if len(frames) != 3:
                raise ValueError("three independent ordered load steps required")
            for i, axis in enumerate("xyz"):
                values[f"tip_u{i + 1}_{axis}"] = max(
                    component(frames[i], "U", f"U{i + 1}", right)
                )
                values[f"reaction_{axis}_{axis}"] = sum(
                    component(frames[i], "RF", f"RF{i + 1}", left)
                )
        elastic = []
        expansion = []
        for material in odb.materials.values():
            elastic.append([float(x) for x in material.elastic.table[0]])
            thermal = getattr(material, "expansion", None)
            if thermal is not None:
                expansion.append([float(x) for x in thermal.table[0]])
        profiles = (
            [{"a": float(p.a), "b": float(p.b)} for p in odb.profiles.values()]
            if c["family"] == "beam"
            else []
        )
        orientations = (
            [list(o.vector) for o in instance.beamOrientations]
            if c["family"] in ("beam", "shaft")
            else []
        )
        volume = None
        if c["family"] in ("hole", "solid_beam", "thick_plate"):
            volume = sum(float(v.data) for v in frames[-1].fieldOutputs["EVOL"].values)
        if not all(math.isfinite(v) for v in values.values()):
            raise ValueError("nonfinite oracle measurement")
        return {
            "metrics": values,
            "nodes": nodes,
            "elements": elements,
            "volume": volume,
            "step_names": list(odb.steps.keys()),
            "elastic": elastic,
            "expansion": expansion,
            "profiles": profiles,
            "beam_orientations": orientations,
            "assembly_node_count": len(getattr(odb.rootAssembly, "nodes", ())),
        }
    finally:
        odb.close()


if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as stream:
        request = json.load(stream)
    result = inspect(request["odb_path"], request["case"])
    with open(sys.argv[2], "w", encoding="utf-8") as stream:
        json.dump(result, stream, allow_nan=False)
