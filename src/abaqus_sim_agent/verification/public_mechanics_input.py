"""公开力学 Case 的有界 INP 校验；不支持任意 Abaqus 输入语法。"""

import math
from dataclasses import dataclass
from pathlib import Path

from abaqus_sim_agent.evaluation.public_mechanics_cases import MechanicsCase


@dataclass
class Block:
    keyword: str
    options: dict[str, str]
    flags: set[str]
    rows: list[list[str]]
    scope: str
    step: int


def blocks(path: Path) -> list[Block]:
    result = []
    scope = "assembly"
    step = -1
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            parts = [p.strip().lower() for p in line.split(",") if p.strip()]
            options = dict(p.split("=", 1) for p in parts[1:] if "=" in p)
            flags = {p for p in parts[1:] if "=" not in p}
            key = parts[0]
            if key == "*part":
                scope = "part"
            if key == "*end part":
                scope = "assembly"
            if key == "*step":
                step += 1
            result.append(Block(key, options, flags, [], scope, step))
        else:
            if not result:
                raise ValueError("data before first keyword")
            result[-1].rows.append([v.strip() for v in line.split(",") if v.strip()])
    return result


def _number(value: str) -> float:
    result = float(value.replace("D", "E").replace("d", "e"))
    if not math.isfinite(result):
        raise ValueError("nonfinite INP value")
    return result


def _plane_stress_faces(element: dict) -> dict[int, tuple[int, ...]]:
    nodes = element["nodes"]
    if element["type"] in {"CPS8", "CPS8R"}:
        return {
            1: (nodes[0], nodes[1], nodes[4]),
            2: (nodes[1], nodes[2], nodes[5]),
            3: (nodes[2], nodes[3], nodes[6]),
            4: (nodes[3], nodes[0], nodes[7]),
        }
    if element["type"] in {"CPS6", "CPS6M"}:
        return {
            1: (nodes[0], nodes[1], nodes[3]),
            2: (nodes[1], nodes[2], nodes[4]),
            3: (nodes[2], nodes[0], nodes[5]),
        }
    raise ValueError(f"unsupported quadratic plane-stress element: {element['type']}")


def _solid_faces(element: dict) -> dict[int, tuple[int, ...]]:
    nodes = element["nodes"]
    if element["type"] in {"C3D10", "C3D10HS", "C3D10M"}:
        return {
            1: (nodes[0], nodes[1], nodes[2]),
            2: (nodes[0], nodes[3], nodes[1]),
            3: (nodes[1], nodes[3], nodes[2]),
            4: (nodes[2], nodes[3], nodes[0]),
        }
    if element["type"] in {"C3D20", "C3D20R"}:
        return {
            1: (nodes[0], nodes[1], nodes[2], nodes[3]),
            2: (nodes[4], nodes[7], nodes[6], nodes[5]),
            3: (nodes[0], nodes[4], nodes[5], nodes[1]),
            4: (nodes[1], nodes[5], nodes[6], nodes[2]),
            5: (nodes[2], nodes[6], nodes[7], nodes[3]),
            6: (nodes[3], nodes[7], nodes[4], nodes[0]),
        }
    raise ValueError(f"unsupported thick-plate solid element: {element['type']}")


def check_input(path: Path, case: MechanicsCase, measurement: dict) -> list[str]:
    """按明确 Case 校验物理输入；不认识的物理关键字拒绝而非忽略。

    Args:
        path: 本次 Solver 实际使用的 INP。
        case: 宿主拥有的固定 Case 合同。
        measurement: 独立 ODB 读取的网格及材料数据。

    Returns:
        违规说明；空列表表示通过当前有界语法与物理检查。
    """
    try:
        return _check(path, case, measurement)
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError) as exc:
        return [f"INP contract could not be established: {exc}"]


def _check(path, c, m):
    data = blocks(path)
    allowed = {
        "*heading",
        "*preprint",
        "*part",
        "*node",
        "*ngen",
        "*ncopy",
        "*element",
        "*elgen",
        "*nset",
        "*elset",
        "*nmap",
        "*orientation",
        "*transform",
        "*mpc",
        "*solid section",
        "*beam section",
        "*shell section",
        "*end part",
        "*assembly",
        "*instance",
        "*end instance",
        "*surface",
        "*end assembly",
        "*material",
        "*elastic",
        "*boundary",
        "*step",
        "*static",
        "*cload",
        "*dload",
        "*dsload",
        "*output",
        "*node output",
        "*element output",
        "*energy output",
        "*restart",
        "*end step",
        "*node print",
        "*node file",
        "*el print",
        "*el file",
        "*section controls",
    }
    if c.family == "thermal_solid":
        allowed.update({"*expansion", "*temperature"})
    bad = {b.keyword for b in data} - allowed
    if bad:
        return [f"Unsupported physical/input keywords: {sorted(bad)}"]
    errors = []
    elliptic_outer_faces: set[tuple[int, int]] = set()
    nodes = {int(k): v for k, v in m["nodes"].items()}
    elements = {int(k): v for k, v in m["elements"].items()}
    tolerance = max(c.length * 1e-7, 1e-8)
    x_min = c.inner_radius if c.family == "axisymmetric_pressure" else 0.0
    left = {n for n, p in nodes.items() if abs(p[0] - x_min) < tolerance}
    right = {n for n, p in nodes.items() if abs(p[0] - c.length) < tolerance}
    sets = {}
    for b in data:
        if b.keyword in {"*nset", "*elset"}:
            key = (b.keyword, b.scope, b.options[b.keyword[1:]])
            sets.setdefault(key, []).append(b)
        elif b.keyword == "*element" and "elset" in b.options:
            key = ("*elset", b.scope, b.options["elset"])
            sets.setdefault(key, []).append(b)
        elif b.keyword == "*elgen" and "elset" in b.options:
            key = ("*elset", b.scope, b.options["elset"])
            sets.setdefault(key, []).append(b)
        elif b.keyword == "*ngen" and "nset" in b.options:
            key = ("*nset", b.scope, b.options["nset"])
            sets.setdefault(key, []).append(b)

    def resolve(name, kind, scope="assembly", seen=()):
        name = name.lower()
        available = set(nodes) if kind == "*nset" else set(elements)
        if name.isdigit():
            label = int(name)
            return {label} if label in available else set()
        if "." in name:
            name = name.split(".", 1)[1]
            scope = "part"
            if name.isdigit():
                label = int(name)
                return {label} if label in available else set()
        key = (kind, scope, name)
        if key in seen:
            raise ValueError("cyclic set reference")
        if key not in sets:
            raise ValueError(f"unknown set {key}")
        ids = set()
        for b in sets[key]:
            if b.keyword == "*element":
                ids.update(int(row[0]) for row in b.rows if row)
                continue
            if b.keyword == "*elgen":
                for row in b.rows:
                    if len(row) < 4 or (len(row) - 1) % 3:
                        raise ValueError("unsupported ELGEN row")
                    start = int(row[0])
                    offsets = {0}
                    for index in range(1, len(row), 3):
                        count = int(row[index])
                        increment = int(row[index + 2])
                        if count <= 0 or increment <= 0:
                            raise ValueError("invalid ELGEN range")
                        offsets = {
                            base + i * increment
                            for base in offsets
                            for i in range(count)
                        }
                    ids.update(start + offset for offset in offsets)
                continue
            if b.keyword == "*ngen":
                available = set(nodes)
                for row in b.rows:
                    if len(row) not in {2, 3}:
                        raise ValueError("unsupported NGEN row")
                    start, end = map(int, row[:2])
                    increment = int(row[2]) if len(row) == 3 else 1
                    if increment <= 0 or end < start:
                        raise ValueError("invalid NGEN range")
                    ids.update(
                        label
                        for label in range(start, end + 1, increment)
                        if label in available
                    )
                continue
            for row in b.rows:
                if b.flags & {"gen", "generate"}:
                    a, z, step = map(int, row)
                    if step <= 0 or z < a:
                        raise ValueError("invalid generated set")
                    ids.update(
                        label for label in range(a, z + 1, step) if label in available
                    )
                else:
                    for token in row:
                        ids.update(resolve(token, kind, scope, (*seen, key)))
        return ids

    mpc_blocks = [b for b in data if b.keyword == "*mpc"]
    junction_mpc_nodes = None
    if (
        c.family == "axisymmetric_shell"
        and c.axisymmetric_shell_profile == "branched_sphere_cylinder"
    ):
        if len(mpc_blocks) != 1:
            errors.append("branched axisymmetric shell requires one MPC block")
        else:
            block = mpc_blocks[0]
            if block.options or block.flags or len(block.rows) != 1:
                errors.append("branched axisymmetric shell MPC definition mismatch")
            else:
                row = block.rows[0]
                if len(row) != 3 or row[0].upper() != "TIE":
                    errors.append(
                        "branched axisymmetric shell requires one MPC TIE row"
                    )
                else:
                    tied = []
                    for token in row[1:]:
                        ids = (
                            {int(token)}
                            if token.isdigit()
                            else resolve(token, "*nset", block.scope)
                        )
                        if len(ids) != 1:
                            errors.append(
                                "MPC TIE endpoints must each resolve to one node"
                            )
                            break
                        tied.append(next(iter(ids)))
                    if len(tied) == 2:
                        if tied[0] == tied[1] or any(n not in nodes for n in tied):
                            errors.append(
                                "MPC TIE must connect two distinct material nodes"
                            )
                        else:
                            junction_mpc_nodes = set(tied)
                            if math.dist(nodes[tied[0]], nodes[tied[1]]) > tolerance:
                                errors.append("MPC TIE endpoints must be coincident")
    elif mpc_blocks:
        errors.append("MPC is unsupported for this public mechanics case")

    types = {str(e["type"]).upper() for e in elements.values()}
    element_blocks = [b for b in data if b.keyword == "*element"]
    inp_types = {
        b.options["type"].upper() for b in element_blocks if "type" in b.options
    }
    permitted = (
        {"T3D2", "T3D3"}
        if c.family == "truss"
        else {"B31", "B32"}
        if c.family in {"beam", "shaft"}
        else {"CPS4", "CPS4R", "CPS8", "CPS8R"}
        if c.family == "plane_stress"
        else {"CPS6", "CPS6M", "CPS8", "CPS8R"}
        if c.family == "elliptic_membrane"
        else {"C3D20", "C3D20R", "C3D10", "C3D10HS", "C3D10M"}
        if c.family == "thick_plate"
        else {"CAX4", "CAX4R", "CAX8", "CAX8R"}
        if c.family == "axisymmetric_pressure"
        else {"SAX1", "SAX2"}
        if c.family == "axisymmetric_shell"
        else {"S4", "S4R", "S8R", "S8R5", "S9R5"}
        if c.family == "skew_plate"
        else {"S8R", "S8R5", "S9R5"}
        if c.family == "hemisphere_shell"
        else {"S8R", "S8R5", "S9R5"}
        if c.family == "z_section_shell"
        else {"S4", "S4R", "S8R", "S8R5", "S9R5"}
        if c.family == "cylindrical_shell_patch"
        else {"C3D20", "C3D20R"}
        if c.family == "thermal_solid"
        else {"C3D4", "C3D10", "C3D8", "C3D8R", "C3D20", "C3D20R"}
    )
    if any("type" not in b.options for b in element_blocks):
        errors.append("every INP element block must declare TYPE")
    if not inp_types or not inp_types <= permitted:
        errors.append(f"INP element representation mismatch: {inp_types}")
    if not types or not types <= permitted:
        errors.append(f"element representation mismatch: {types}")
    if inp_types and types and inp_types != types:
        errors.append(f"INP/ODB element type mismatch: {inp_types} != {types}")
    if m["assembly_node_count"]:
        errors.append("extra assembly/reference nodes")
    parent = {n: n for n in nodes}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    used = set()
    for e in elements.values():
        used.update(e["nodes"])
        a = find(e["nodes"][0])
        for n in e["nodes"][1:]:
            parent[find(n)] = a
    if junction_mpc_nodes and junction_mpc_nodes <= set(nodes):
        a, b = tuple(junction_mpc_nodes)
        parent[find(b)] = find(a)
    if used != set(nodes) or len({find(n) for n in nodes}) != 1:
        errors.append("orphan nodes/disconnected material mesh")
    bounds = [
        (min(p[i] for p in nodes.values()), max(p[i] for p in nodes.values()))
        for i in range(3)
    ]
    if c.family == "cylindrical_shell_patch":
        angle = math.radians(c.arc_angle_degrees)
        expected = [
            (c.radius * math.cos(angle), c.radius),
            (0, c.radius * math.sin(angle)),
            (0, c.length),
        ]
    elif c.family == "axisymmetric_shell":
        if c.axisymmetric_shell_profile == "hyperbolic":
            expected = [
                (c.radius, c.width),
                (0, c.length),
                (0, 0),
            ]
        elif c.axisymmetric_shell_profile == "sphere_cylinder":
            expected = [
                (0, c.radius),
                (0, c.length),
                (0, 0),
            ]
        elif c.axisymmetric_shell_profile == "crown_knuckle_cylinder":
            expected = [
                (0, c.radius),
                (0, c.length),
                (0, 0),
            ]
        elif c.axisymmetric_shell_profile == "branched_sphere_cylinder":
            expected = [
                (0, c.radius),
                (0, c.length),
                (0, 0),
            ]
        else:
            raise ValueError("unsupported axisymmetric-shell profile")
    elif c.family == "skew_plate":
        angle = math.radians(c.skew_angle_degrees)
        expected = [
            (0, c.length + c.width * math.cos(angle)),
            (0, c.width * math.sin(angle)),
            (0, 0),
        ]
    elif c.family == "hemisphere_shell":
        expected = [(0, c.radius), (0, c.radius), (0, c.radius)]
    elif c.family == "z_section_shell":
        expected = [(0, c.length), (-1, 1), (-1, 1)]
    elif c.family == "thermal_solid":
        expected = [(0, c.length), (0, c.width), (0, c.height)]
    else:
        expected = [
            (x_min, c.length),
            (0, 0 if c.family in {"truss", "beam", "shaft"} else c.width),
            (
                0,
                0
                if c.family
                in {
                    "truss",
                    "beam",
                    "shaft",
                    "plane_stress",
                    "elliptic_membrane",
                    "axisymmetric_pressure",
                }
                else c.height,
            ),
        ]
    bounds_tolerance = (
        2e-4
        if c.family == "axisymmetric_shell"
        and c.axisymmetric_shell_profile == "crown_knuckle_cylinder"
        else tolerance
    )
    if any(
        abs(a - b) > bounds_tolerance
        for pair, target in zip(bounds, expected, strict=True)
        for a, b in zip(pair, target, strict=True)
    ):
        errors.append("geometry bounding box mismatch")
    if c.family == "hole" and any(
        math.hypot(p[0] - 7.5, p[1] - 2.5) < 0.5 - 1e-5 for p in nodes.values()
    ):
        errors.append("mesh fills the specified hole")
    if c.family == "thermal_solid":
        surface_tol = 3e-4
        landmarks = (
            (1.0, 0.0, 0.0),
            (0.7071, 0.0, 0.7071),
            (0.7071, 0.0, 1.79),
            (1.4, 0.0, 0.0),
            (1.2124, 0.0, 0.7),
            (1.1062, 0.0, 1.045),
            (1.0, 0.0, 1.39),
            (1.0, 0.0, 1.79),
        )
        for target in landmarks:
            matches = [p for p in nodes.values() if math.dist(p, target) <= surface_tol]
            if len(matches) != 1:
                errors.append("thermal-solid profile landmark mismatch")
                break
        a_nodes = {
            label
            for label, point in nodes.items()
            if math.dist(point, (1.0, 0.0, 0.0)) <= surface_tol
        }
        if len(a_nodes) != 1:
            errors.append("thermal-solid mesh must contain one exact A node")
        for point in nodes.values():
            x, y, z = point
            r = math.hypot(x, y)
            if (
                x < -surface_tol
                or y < -surface_tol
                or z < -surface_tol
                or z > c.height + surface_tol
            ):
                errors.append("thermal-solid node lies outside the quarter sector")
                break
            inner = math.sqrt(max(0.0, 1.0 - z * z)) if z <= 0.7071 else 0.7071
            if z <= 0.7:
                outer = math.sqrt(max(0.0, 1.4**2 - z * z))
            elif z <= 1.39:
                outer = 1.2124 + (z - 0.7) * (1.0 - 1.2124) / (1.39 - 0.7)
            else:
                outer = 1.0
            if r < inner - surface_tol or r > outer + surface_tol:
                errors.append(
                    "thermal-solid node lies outside the declared radial profile"
                )
                break
    if c.family == "skew_plate":
        angle = math.radians(c.skew_angle_degrees)
        sine = math.sin(angle)
        cosine = math.cos(angle)
        center = (
            0.5 * (c.length + c.width * cosine),
            0.5 * c.width * sine,
        )
        center_nodes = set()
        for label, point in nodes.items():
            eta = point[1] / sine
            xi = point[0] - eta * cosine
            if (
                xi < -tolerance
                or xi > c.length + tolerance
                or eta < -tolerance
                or eta > c.width + tolerance
                or abs(point[2]) > tolerance
            ):
                errors.append("skew-plate node lies outside the declared parallelogram")
                break
            if math.hypot(point[0] - center[0], point[1] - center[1]) < tolerance:
                center_nodes.add(label)
        if len(center_nodes) != 1:
            errors.append("skew-plate mesh must contain one exact center node")
        for element in elements.values():
            if len(element["nodes"]) < 4:
                errors.append(
                    "skew-plate shell needs quadrilateral corner connectivity"
                )
                break
            points = [nodes[n] for n in element["nodes"][:4]]
            twice_area = sum(
                points[i][0] * points[(i + 1) % 4][1]
                - points[(i + 1) % 4][0] * points[i][1]
                for i in range(4)
            )
            if twice_area <= tolerance**2:
                errors.append("skew-plate shell normal/connectivity must point +Z")
                break
    if c.family == "hemisphere_shell":
        if c.radius <= 0:
            errors.append("hemisphere radius missing")
        else:
            sphere_tol = max(c.radius * 2e-5, tolerance)
            points = {
                "A": (c.radius, 0.0, 0.0),
                "C": (0.0, c.radius, 0.0),
                "E": (0.0, 0.0, c.radius),
            }
            exact = {name: set() for name in points}
            for label, point in nodes.items():
                if min(point) < -sphere_tol:
                    errors.append(
                        "hemisphere node lies outside the declared quarter sphere"
                    )
                    break
                if not math.isclose(
                    math.sqrt(sum(value * value for value in point)),
                    c.radius,
                    rel_tol=0.0,
                    abs_tol=sphere_tol,
                ):
                    errors.append(
                        "hemisphere node is not on the declared spherical midsurface"
                    )
                    break
                for name, target in points.items():
                    if math.dist(point, target) < tolerance:
                        exact[name].add(label)
            for name, labels in exact.items():
                if len(labels) != 1:
                    errors.append(f"hemisphere mesh must contain one exact {name} node")
    if c.family == "z_section_shell":
        a_nodes = set()
        for label, point in nodes.items():
            x, y, z = point
            on_lower_flange = (
                abs(y + 1.0) < tolerance and -1.0 - tolerance <= z <= tolerance
            )
            on_web = abs(z) < tolerance and -1.0 - tolerance <= y <= 1.0 + tolerance
            on_upper_flange = (
                abs(y - 1.0) < tolerance and -tolerance <= z <= 1.0 + tolerance
            )
            if (
                x < -tolerance
                or x > c.length + tolerance
                or not (on_lower_flange or on_web or on_upper_flange)
            ):
                errors.append(
                    "Z-section node lies outside the declared shell midsurface"
                )
                break
            if math.dist(point, (2.5, -1.0, -1.0)) < tolerance:
                a_nodes.add(label)
        if len(a_nodes) != 1:
            errors.append("Z-section mesh must contain one exact A node")
    if c.family == "cylindrical_shell_patch":
        if c.radius <= 0 or c.arc_angle_degrees <= 0:
            errors.append("cylindrical-shell radius/arc is missing")
        else:
            surface_tol = max(c.radius * 2e-5, tolerance)
            max_angle = math.radians(c.arc_angle_degrees)
            sample_angle = math.radians(c.sample_angle_degrees)
            sample_target = (
                c.radius * math.cos(sample_angle),
                c.radius * math.sin(sample_angle),
                c.sample_axial_position,
            )
            sample_nodes = set()
            for label, point in nodes.items():
                radius = math.hypot(point[0], point[1])
                theta = math.atan2(point[1], point[0])
                if (
                    not math.isclose(radius, c.radius, rel_tol=0.0, abs_tol=surface_tol)
                    or theta < -surface_tol
                    or theta > max_angle + surface_tol
                    or point[2] < -tolerance
                    or point[2] > c.length + tolerance
                ):
                    errors.append(
                        "cylindrical-shell node lies outside the declared cylindrical midsurface"
                    )
                    break
                if math.dist(point, sample_target) < surface_tol:
                    sample_nodes.add(label)
            if len(sample_nodes) != 1:
                errors.append("cylindrical-shell mesh must contain one exact E node")
    if c.family == "axisymmetric_shell":
        surface_tol = 2e-4
        if c.axisymmetric_shell_profile == "hyperbolic":
            k_nodes = set()
            for label, point in nodes.items():
                radius, axial, out_of_plane = point
                if (
                    radius < c.radius - tolerance
                    or radius > c.width + tolerance
                    or axial < -tolerance
                    or axial > c.length + tolerance
                    or abs(out_of_plane) > tolerance
                    or abs(radius * radius - axial * axial - c.radius * c.radius)
                    > surface_tol
                ):
                    errors.append(
                        "axisymmetric hyperbolic-shell node lies outside the declared midsurface"
                    )
                    break
                if abs(radius - c.radius) < tolerance and abs(axial) < tolerance:
                    k_nodes.add(label)
            if len(k_nodes) != 1:
                errors.append(
                    "axisymmetric hyperbolic-shell mesh must contain one exact K node"
                )
        elif c.axisymmetric_shell_profile == "sphere_cylinder":
            d_nodes = set()
            for label, point in nodes.items():
                radius, axial, out_of_plane = point
                valid_cylinder = (
                    axial <= c.width + tolerance
                    and abs(radius - c.radius) <= surface_tol
                )
                valid_sphere = (
                    axial >= c.width - tolerance
                    and abs(
                        radius * radius
                        + (axial - c.width) * (axial - c.width)
                        - c.radius * c.radius
                    )
                    <= surface_tol
                )
                if (
                    radius < -tolerance
                    or radius > c.radius + tolerance
                    or axial < -tolerance
                    or axial > c.length + tolerance
                    or abs(out_of_plane) > tolerance
                    or not (valid_cylinder or valid_sphere)
                ):
                    errors.append(
                        "axisymmetric sphere-cylinder node lies outside the declared midsurface"
                    )
                    break
                if (
                    abs(radius - c.radius) < tolerance
                    and abs(axial - c.sample_axial_position) < tolerance
                ):
                    d_nodes.add(label)
            if len(d_nodes) != 1:
                errors.append(
                    "axisymmetric sphere-cylinder mesh must contain one exact D node"
                )
        elif c.axisymmetric_shell_profile == "crown_knuckle_cylinder":
            d_nodes = set()
            crown_center_z = c.width + c.crown_radius
            knuckle_center_r = c.radius - c.knuckle_radius
            for label, point in nodes.items():
                radius, axial, out_of_plane = point
                valid_cylinder = (
                    axial <= c.width + surface_tol
                    and abs(radius - c.radius) <= surface_tol
                )
                valid_crown = (
                    radius <= c.sample_radius + surface_tol
                    and abs(
                        radius * radius
                        + (axial - crown_center_z) * (axial - crown_center_z)
                        - c.crown_radius * c.crown_radius
                    )
                    <= surface_tol
                )
                valid_knuckle = (
                    radius >= c.sample_radius - surface_tol
                    and axial >= c.width - surface_tol
                    and abs(
                        (radius - knuckle_center_r) * (radius - knuckle_center_r)
                        + (axial - c.width) * (axial - c.width)
                        - c.knuckle_radius * c.knuckle_radius
                    )
                    <= surface_tol
                )
                if (
                    radius < -tolerance
                    or radius > c.radius + tolerance
                    or axial < -tolerance
                    or axial > c.length + surface_tol
                    or abs(out_of_plane) > tolerance
                    or not (valid_cylinder or valid_crown or valid_knuckle)
                ):
                    errors.append(
                        "axisymmetric crown-knuckle-cylinder node lies outside the declared midsurface"
                    )
                    break
                if (
                    abs(radius - c.sample_radius) < tolerance
                    and abs(axial - c.sample_axial_position) < tolerance
                ):
                    d_nodes.add(label)
            if len(d_nodes) != 1:
                errors.append(
                    "axisymmetric crown-knuckle-cylinder mesh must contain one exact D node"
                )
        elif c.axisymmetric_shell_profile == "branched_sphere_cylinder":
            junction_nodes = set()
            pole_nodes = set()
            sphere_pole_z = c.branch_center_axial_position - c.branch_sphere_radius
            for label, point in nodes.items():
                radius, axial, out_of_plane = point
                valid_cylinder = (
                    abs(radius - c.radius) <= surface_tol
                    and -tolerance <= axial <= c.length + tolerance
                )
                valid_sphere = (
                    radius >= -tolerance
                    and radius <= c.radius + surface_tol
                    and axial >= sphere_pole_z - surface_tol
                    and axial <= c.sample_axial_position + surface_tol
                    and abs(
                        radius * radius
                        + (axial - c.branch_center_axial_position)
                        * (axial - c.branch_center_axial_position)
                        - c.branch_sphere_radius * c.branch_sphere_radius
                    )
                    <= surface_tol
                )
                if abs(out_of_plane) > tolerance or not (
                    valid_cylinder or valid_sphere
                ):
                    errors.append(
                        "branched axisymmetric-shell node lies outside the declared midsurface"
                    )
                    break
                if (
                    abs(radius - c.sample_radius) < tolerance
                    and abs(axial - c.sample_axial_position) < tolerance
                ):
                    junction_nodes.add(label)
                if abs(radius) < tolerance and abs(axial - sphere_pole_z) < tolerance:
                    pole_nodes.add(label)
            if len(junction_nodes) != 2:
                errors.append(
                    "branched axisymmetric shell must contain two coincident C nodes"
                )
            if len(pole_nodes) != 1:
                errors.append(
                    "branched axisymmetric shell must contain one sphere pole node"
                )
            if junction_mpc_nodes is None or junction_mpc_nodes != junction_nodes:
                errors.append("MPC TIE must connect exactly the two coincident C nodes")

            cylinder_elements = set()
            sphere_elements = set()
            for label, element in elements.items():
                element_nodes = element["nodes"]
                element_points = [nodes[n] for n in element_nodes]
                is_cylinder = all(
                    abs(point[0] - c.radius) <= surface_tol for point in element_points
                )
                is_sphere = all(
                    abs(
                        point[0] * point[0]
                        + (point[1] - c.branch_center_axial_position)
                        * (point[1] - c.branch_center_axial_position)
                        - c.branch_sphere_radius * c.branch_sphere_radius
                    )
                    <= surface_tol
                    for point in element_points
                )
                if is_cylinder:
                    cylinder_elements.add(label)
                    if (
                        nodes[element_nodes[0]][1]
                        >= nodes[element_nodes[1]][1] - tolerance
                    ):
                        errors.append(
                            "branched cylinder shell connectivity must run bottom-to-top"
                        )
                elif is_sphere:
                    sphere_elements.add(label)
                    if (
                        nodes[element_nodes[0]][0]
                        >= nodes[element_nodes[1]][0] - tolerance
                    ):
                        errors.append(
                            "branched sphere shell connectivity must run pole-to-junction"
                        )
                else:
                    errors.append(
                        "branched axisymmetric shell element crosses branch geometry"
                    )
            if not cylinder_elements or not sphere_elements:
                errors.append(
                    "branched axisymmetric shell requires cylinder and sphere elements"
                )
            if len(junction_nodes) == 2:
                roles = []
                for node in junction_nodes:
                    incident = {
                        label
                        for label, element in elements.items()
                        if node in element["nodes"]
                    }
                    roles.append(
                        (
                            bool(incident & cylinder_elements),
                            bool(incident & sphere_elements),
                        )
                    )
                if sorted(roles) != [(False, True), (True, False)]:
                    errors.append(
                        "coincident C nodes must belong separately to cylinder and sphere branches"
                    )
        else:
            errors.append("unsupported axisymmetric-shell profile")
    if c.family == "elliptic_membrane":
        inner_x = c.inner_semi_axis_x
        inner_y = c.inner_semi_axis_y
        if inner_x <= 0 or inner_y <= 0:
            errors.append("elliptic membrane inner semi-axes missing")
        else:
            ellipse_tol = 5e-4
            d_nodes = set()
            for label, point in nodes.items():
                if (
                    point[0] < -tolerance
                    or point[1] < -tolerance
                    or abs(point[2]) > tolerance
                ):
                    errors.append(
                        "elliptic-membrane node lies outside the quarter plane"
                    )
                    break
                inner_norm = (point[0] / inner_x) ** 2 + (point[1] / inner_y) ** 2
                outer_norm = (point[0] / c.length) ** 2 + (point[1] / c.width) ** 2
                if inner_norm < 1.0 - ellipse_tol or outer_norm > 1.0 + ellipse_tol:
                    errors.append(
                        "elliptic-membrane node lies outside the declared annulus"
                    )
                    break
                if abs(point[0] - inner_x) < tolerance and abs(point[1]) < tolerance:
                    d_nodes.add(label)
            if len(d_nodes) != 1:
                errors.append("elliptic-membrane mesh must contain one exact D node")

            owners: dict[tuple[int, int], list[tuple[int, int, tuple[int, ...]]]] = {}
            for label, element in elements.items():
                for face, face_nodes in _plane_stress_faces(element).items():
                    first, second = sorted((face_nodes[0], face_nodes[1]))
                    edge = (first, second)
                    owners.setdefault(edge, []).append((label, face, face_nodes))
            for face_owners in owners.values():
                if len(face_owners) != 1:
                    continue
                label, face, face_nodes = face_owners[0]
                points = [nodes[n] for n in face_nodes]
                on_outer = all(
                    abs((p[0] / c.length) ** 2 + (p[1] / c.width) ** 2 - 1.0)
                    <= ellipse_tol
                    for p in points
                )
                on_inner = all(
                    abs((p[0] / inner_x) ** 2 + (p[1] / inner_y) ** 2 - 1.0)
                    <= ellipse_tol
                    for p in points
                )
                on_x_sym = all(abs(p[0]) < tolerance for p in points)
                on_y_sym = all(abs(p[1]) < tolerance for p in points)
                if sum((on_outer, on_inner, on_x_sym, on_y_sym)) != 1:
                    errors.append(
                        "elliptic-membrane boundary does not match the four declared edges"
                    )
                    break
                if on_outer:
                    elliptic_outer_faces.add((label, face))
            if not elliptic_outer_faces:
                errors.append("elliptic-membrane outer curved edge is missing")
    if c.family == "thick_plate":
        inner_x = c.inner_semi_axis_x
        inner_y = c.inner_semi_axis_y
        if inner_x <= 0 or inner_y <= 0:
            errors.append("thick-plate inner semi-axes missing")
        else:
            ellipse_tol = 5e-4
            d_nodes = set()
            for label, point in nodes.items():
                if point[0] < -tolerance or point[1] < -tolerance:
                    errors.append("thick-plate node lies outside the quarter plane")
                    break
                inner_norm = (point[0] / inner_x) ** 2 + (point[1] / inner_y) ** 2
                outer_norm = (point[0] / c.length) ** 2 + (point[1] / c.width) ** 2
                if inner_norm < 1.0 - ellipse_tol or outer_norm > 1.0 + ellipse_tol:
                    errors.append(
                        "thick-plate node lies outside the declared annular planform"
                    )
                    break
                if (
                    abs(point[0] - inner_x) < tolerance
                    and abs(point[1]) < tolerance
                    and abs(point[2] - c.height) < tolerance
                ):
                    d_nodes.add(label)
            if len(d_nodes) != 1:
                errors.append("thick-plate mesh must contain one exact top D node")
    if c.family in {"hole", "solid_beam", "thick_plate"}:
        if c.family == "hole":
            expected_volume = (
                c.length * c.width * c.height - math.pi * 0.5**2 * c.height
            )
        elif c.family == "thick_plate":
            expected_volume = (
                math.pi
                / 4.0
                * (c.length * c.width - c.inner_semi_axis_x * c.inner_semi_axis_y)
                * c.height
            )
        else:
            expected_volume = c.length * c.width * c.height
        if not isinstance(m.get("volume"), (float, int)) or not math.isclose(
            m["volume"], expected_volume, rel_tol=0.005
        ):
            errors.append("solid volume mismatch or missing EVOL")
    material_blocks = [b for b in data if b.keyword == "*elastic"]
    if (
        len(material_blocks) != 1
        or material_blocks[0].options.get("type", "isotropic")
        not in {"iso", "isotropic"}
        or set(material_blocks[0].options) - {"type"}
        or len(material_blocks[0].rows) != 1
        or len(material_blocks[0].rows[0]) != 2
    ):
        errors.append("one constant isotropic Elastic definition required")
    else:
        e, nu = map(_number, material_blocks[0].rows[0])
        if not math.isclose(e, c.young, rel_tol=1e-7) or not math.isclose(
            nu, c.poisson, abs_tol=1e-7
        ):
            errors.append("INP elastic material mismatch")
    # 已在 Abaqus 2024 参考 ODB 复现：nu=0 时 table 省略末尾零。
    row = m["elastic"][0] if len(m["elastic"]) == 1 else []
    if (
        not row
        or not math.isclose(row[0], c.young, rel_tol=1e-7)
        or not (
            (len(row) == 2 and math.isclose(row[1], c.poisson, abs_tol=1e-7))
            or (len(row) == 1 and c.poisson == 0)
        )
    ):
        errors.append("ODB elastic material mismatch")
    expansion_blocks = [b for b in data if b.keyword == "*expansion"]
    if c.family == "thermal_solid":
        if (
            len(expansion_blocks) != 1
            or expansion_blocks[0].options
            or expansion_blocks[0].flags
            or len(expansion_blocks[0].rows) != 1
            or len(expansion_blocks[0].rows[0]) != 1
            or not math.isclose(
                _number(expansion_blocks[0].rows[0][0]),
                c.thermal_expansion,
                rel_tol=1e-7,
            )
        ):
            errors.append("thermal expansion coefficient mismatch")
        odb_expansion = m.get("expansion", [])
        if (
            len(odb_expansion) != 1
            or len(odb_expansion[0]) < 1
            or not math.isclose(odb_expansion[0][0], c.thermal_expansion, rel_tol=1e-7)
        ):
            errors.append("ODB thermal expansion coefficient mismatch")
    elif expansion_blocks:
        errors.append("thermal expansion is unsupported for this case")
    expected_steps = 3 if c.family == "solid_beam" else 1
    steps = [b for b in data if b.keyword == "*step"]
    if (
        len(steps) != expected_steps
        or len(m["step_names"]) != expected_steps
        or len([b for b in data if b.keyword == "*static"]) != expected_steps
    ):
        errors.append("analysis step count/type mismatch")
    if any(b.options.get("nlgeom", "no") not in {"no", "off"} for b in steps):
        errors.append("geometric nonlinearity enabled")
    allowed_step_flags = (
        {"pert", "perturbation"}
        if c.family in {"thick_plate", "cylindrical_shell_patch"}
        else set()
    )
    if any(
        (b.flags - allowed_step_flags)
        or any(k not in {"name", "nlgeom", "inc"} for k in b.options)
        for b in steps
    ):
        errors.append("unsupported step alteration")
    coordinate_keywords = [
        b for b in data if b.keyword in {"*nmap", "*orientation", "*transform"}
    ]
    if c.family != "cylindrical_shell_patch" and coordinate_keywords:
        errors.append("coordinate mapping/orientation is unsupported for this case")
    if c.family == "cylindrical_shell_patch":
        nmap_blocks = [b for b in data if b.keyword == "*nmap"]
        if len(nmap_blocks) > 1:
            errors.append("at most one cylindrical NMAP is supported")
        for b in nmap_blocks:
            if (
                b.options.get("type") != "cylindrical"
                or set(b.options) != {"nset", "type"}
                or b.flags
                or len(b.rows) != 2
                or len(b.rows[0]) != 6
                or len(b.rows[1]) != 3
                or resolve(b.options["nset"], "*nset", b.scope) != set(nodes)
            ):
                errors.append("cylindrical NMAP definition mismatch")
            else:
                axis = list(map(_number, b.rows[0]))
                radial = list(map(_number, b.rows[1]))
                if any(
                    not math.isclose(a, z, abs_tol=1e-12)
                    for a, z in zip(axis, (0, 0, 0, 0, 0, 1), strict=True)
                ) or any(
                    not math.isclose(a, z, abs_tol=1e-12)
                    for a, z in zip(radial, (1, 0, 0), strict=True)
                ):
                    errors.append("cylindrical NMAP axis mismatch")
        orientation_blocks = [b for b in data if b.keyword == "*orientation"]
        if len(orientation_blocks) != 1:
            errors.append("one cylindrical shell orientation is required")
        else:
            b = orientation_blocks[0]
            if (
                b.options.get("system") != "cylindrical"
                or "name" not in b.options
                or set(b.options) != {"name", "system"}
                or b.flags
                or len(b.rows) != 2
                or len(b.rows[0]) != 6
            ):
                errors.append("cylindrical shell orientation definition mismatch")
            else:
                axis = list(map(_number, b.rows[0]))
                if any(
                    not math.isclose(a, z, abs_tol=1e-12)
                    for a, z in zip(axis, (0, 0, 0, 0, 0, 1), strict=True)
                ):
                    errors.append("cylindrical shell orientation axis mismatch")
        transform_blocks = [b for b in data if b.keyword == "*transform"]
        if len(transform_blocks) > 1:
            errors.append("at most one cylindrical nodal transform is supported")
        for b in transform_blocks:
            if (
                b.options.get("type") != "c"
                or set(b.options) != {"type", "nset"}
                or b.flags
                or len(b.rows) != 1
                or len(b.rows[0]) != 6
                or resolve(b.options["nset"], "*nset", b.scope) != set(nodes)
            ):
                errors.append("cylindrical nodal transform mismatch")
            else:
                axis = list(map(_number, b.rows[0]))
                if any(
                    not math.isclose(a, z, abs_tol=1e-12)
                    for a, z in zip(axis, (0, 0, 0, 0, 0, 1), strict=True)
                ):
                    errors.append("cylindrical nodal transform axis mismatch")
    part_count = len([b for b in data if b.keyword == "*part"])
    instance_count = len([b for b in data if b.keyword == "*instance"])
    flat_input = part_count == 0 and instance_count == 0
    if not (flat_input or (part_count == 1 and instance_count == 1)):
        errors.append("one part/instance required")
    if any(b.rows for b in data if b.keyword == "*instance"):
        errors.append("translated/rotated instance not in this coordinate contract")
    if not flat_input and any(b.keyword == "*node" and b.scope != "part" for b in data):
        errors.append("extra assembly/reference nodes in INP")
    # 一维网格必须连续覆盖每段，禁止叠杆或跨越截面断点。
    if c.family in {"truss", "beam", "shaft"}:
        intervals = sorted(
            (min(nodes[n][0] for n in e["nodes"]), max(nodes[n][0] for n in e["nodes"]))
            for e in elements.values()
        )
        cursor = 0.0
        for a, z in intervals:
            if abs(a - cursor) > tolerance or z - a <= tolerance:
                errors.append("one-dimensional mesh gap/overlap")
                break
            cursor = z
        if abs(cursor - c.length) > tolerance:
            errors.append("one-dimensional mesh does not span length")
    sections = [
        b
        for b in data
        if b.keyword in {"*solid section", "*beam section", "*shell section"}
    ]
    covered = set()
    for b in sections:
        ids = resolve(b.options["elset"], "*elset", b.scope)
        if covered & ids:
            errors.append("overlapping section assignments")
        covered.update(ids)
        if c.family == "truss":
            area = _number(b.rows[0][0])
            for label in ids:
                xs = [nodes[n][0] for n in elements[label]["nodes"]]
                expected_area = (
                    c.segments[0][1]
                    if len(c.segments) == 1 or max(xs) <= 400 + tolerance
                    else c.segments[1][1]
                )
                if (
                    len(c.segments) > 1
                    and min(xs) < 400 - tolerance
                    and max(xs) > 400 + tolerance
                ):
                    errors.append("element crosses section interface")
                if not math.isclose(area, expected_area, rel_tol=1e-7):
                    errors.append("truss section area mismatch")
                    break
        elif c.family == "beam":
            if b.keyword != "*beam section" or b.options.get("section") != "rect":
                errors.append("rectangular beam section required")
            a, bb = map(_number, b.rows[0][:2])
            v = list(map(_number, b.rows[1][:3]))
            norm = math.hypot(v[1], v[2])
            iz = (
                bb * a**3 / 12 * (v[1] / norm) ** 2
                + a * bb**3 / 12 * (v[2] / norm) ** 2
            )
            if not math.isclose(
                a * bb, c.width * c.height, rel_tol=1e-6
            ) or not math.isclose(iz, c.width * c.height**3 / 12, rel_tol=1e-6):
                errors.append("beam dimensions/orientation mismatch")
        elif c.family == "shaft":
            if b.keyword != "*beam section" or b.options.get("section") != "circ":
                errors.append("solid circular beam section required")
                continue
            if not b.rows or not b.rows[0]:
                errors.append("circular shaft radius missing")
                continue
            radius = _number(b.rows[0][0])
            if not math.isclose(radius, c.width / 2.0, rel_tol=1e-7):
                errors.append("circular shaft radius mismatch")
        elif c.family in {"plane_stress", "elliptic_membrane"}:
            if b.keyword != "*solid section":
                errors.append("plane-stress solid section required")
                continue
            if not b.rows or not b.rows[0]:
                errors.append("plane-stress thickness missing")
                continue
            thickness = _number(b.rows[0][0])
            if not math.isclose(thickness, c.height, rel_tol=1e-7):
                errors.append("plane-stress thickness mismatch")
        elif c.family == "thick_plate" and b.keyword != "*solid section":
            errors.append("thick-plate solid section required")
        elif c.family == "axisymmetric_pressure" and b.keyword != "*solid section":
            errors.append("axisymmetric solid section required")
        elif c.family == "axisymmetric_shell":
            if b.keyword != "*shell section":
                errors.append("axisymmetric shell section required")
                continue
            if not b.rows or not b.rows[0]:
                errors.append("axisymmetric shell thickness missing")
                continue
            if c.axisymmetric_shell_profile == "branched_sphere_cylinder":
                if (
                    set(b.options) != {"elset", "material", "section integration"}
                    or b.options.get("section integration") != "gauss"
                    or b.flags
                ):
                    errors.append(
                        "branched axisymmetric shell requires GAUSS section integration"
                    )
                if len(b.rows[0]) != 2 or not math.isclose(
                    _number(b.rows[0][1]), 3.0, abs_tol=1e-12
                ):
                    errors.append(
                        "branched axisymmetric shell requires three through-thickness points"
                    )
            elif set(b.options) - {"elset", "material"} or b.flags:
                errors.append("axisymmetric shell section options are unsupported")
            thickness = _number(b.rows[0][0])
            if not math.isclose(thickness, c.height, rel_tol=1e-7):
                errors.append("axisymmetric shell thickness mismatch")
        elif c.family == "skew_plate":
            if b.keyword != "*shell section":
                errors.append("skew-plate shell section required")
                continue
            if not b.rows or not b.rows[0]:
                errors.append("skew-plate shell thickness missing")
                continue
            if set(b.options) - {"elset", "material"} or b.flags:
                errors.append(
                    "skew-plate shell offsets/orientation options are unsupported"
                )
            thickness = _number(b.rows[0][0])
            if not math.isclose(thickness, c.height, rel_tol=1e-7):
                errors.append("skew-plate shell thickness mismatch")
        elif c.family == "hemisphere_shell":
            if b.keyword != "*shell section":
                errors.append("hemisphere shell section required")
                continue
            if not b.rows or not b.rows[0]:
                errors.append("hemisphere shell thickness missing")
                continue
            if set(b.options) - {"elset", "material"} or b.flags:
                errors.append(
                    "hemisphere shell offsets/orientation options are unsupported"
                )
            thickness = _number(b.rows[0][0])
            if not math.isclose(thickness, c.height, rel_tol=1e-7):
                errors.append("hemisphere shell thickness mismatch")
        elif c.family == "z_section_shell":
            if b.keyword != "*shell section":
                errors.append("Z-section shell section required")
                continue
            if not b.rows or not b.rows[0]:
                errors.append("Z-section shell thickness missing")
                continue
            if set(b.options) - {"elset", "material"} or b.flags:
                errors.append(
                    "Z-section shell offsets/orientation options are unsupported"
                )
            thickness = _number(b.rows[0][0])
            if not math.isclose(thickness, c.height, rel_tol=1e-7):
                errors.append("Z-section shell thickness mismatch")
        elif c.family == "cylindrical_shell_patch":
            if b.keyword != "*shell section":
                errors.append("cylindrical shell section required")
                continue
            if not b.rows or not b.rows[0]:
                errors.append("cylindrical shell thickness missing")
                continue
            if set(b.options) - {"elset", "material", "orientation"} or b.flags:
                errors.append("cylindrical shell section options are unsupported")
            orientation_names = {
                block.options.get("name")
                for block in data
                if block.keyword == "*orientation"
            }
            if (
                not b.options.get("orientation")
                or b.options.get("orientation") not in orientation_names
            ):
                errors.append(
                    "cylindrical shell section must use the declared orientation"
                )
            thickness = _number(b.rows[0][0])
            if not math.isclose(thickness, c.height, rel_tol=1e-7):
                errors.append("cylindrical shell thickness mismatch")
        elif c.family == "thermal_solid" and b.keyword != "*solid section":
            errors.append("thermal-solid solid section required")
    if covered != set(elements):
        errors.append("section coverage mismatch")
    bc = set()
    nonzero_bc = {}
    for b in data:
        if b.keyword != "*boundary":
            continue
        if b.options or b.flags:
            errors.append("boundary options unsupported")
        for row in b.rows:
            ids = resolve(row[0], "*nset")
            if row[1].upper() == "ENCASTRE":
                dofs = range(
                    1,
                    7 if c.family in {"beam", "shaft", "z_section_shell"} else 4,
                )
                value = 0
            elif row[1].upper() == "XSYMM":
                dofs = (1, 5, 6) if c.family == "hemisphere_shell" else (1,)
                value = 0
            elif row[1].upper() == "YSYMM":
                dofs = (2, 4, 6) if c.family == "hemisphere_shell" else (2,)
                value = 0
            elif row[1].upper() == "ZSYMM":
                dofs = (3, 4, 5) if c.family == "cylindrical_shell_patch" else (3,)
                value = 0
            else:
                dofs = range(int(row[1]), int(row[2] if len(row) > 2 else row[1]) + 1)
                value = _number(row[3]) if len(row) > 3 else 0
            if value != 0:
                if (
                    c.family != "plane_stress"
                    or any(dof != 1 for dof in dofs)
                    or not ids <= right
                    or not math.isclose(value, c.load, rel_tol=1e-7)
                ):
                    errors.append("nonzero displacement replaces force load")
                for n in ids:
                    for dof in dofs:
                        nonzero_bc[n, dof] = value
            bc.update((n, dof) for n in ids for dof in dofs)
    want = {
        (n, d)
        for n in left
        for d in range(
            1,
            7 if c.family in {"beam", "shaft", "z_section_shell"} else 4,
        )
    }
    if c.family == "truss":
        want = {(n, 1) for n in left} | {(n, d) for n in nodes for d in (2, 3)}
    if c.family == "plane_stress":
        bottom_left = {
            n
            for n, p in nodes.items()
            if abs(p[0]) < tolerance and abs(p[1]) < tolerance
        }
        prescribed = set(nonzero_bc)
        want_zero = {(n, 1) for n in left} | {(n, 2) for n in bottom_left}
        want_nonzero = {(n, 1) for n in right}
        if bc - prescribed != want_zero or prescribed != want_nonzero:
            errors.append("physical support/prescribed-displacement mismatch")
    elif c.family == "elliptic_membrane":
        x_sym = {n for n, p in nodes.items() if abs(p[0]) < tolerance}
        y_sym = {n for n, p in nodes.items() if abs(p[1]) < tolerance}
        want = {(n, 1) for n in x_sym} | {(n, 2) for n in y_sym}
        if bc != want:
            errors.append("elliptic-membrane symmetry boundary mismatch")
    elif c.family == "thick_plate":
        ellipse_tol = 5e-4
        x_sym = {n for n, p in nodes.items() if abs(p[0]) < tolerance}
        y_sym = {n for n, p in nodes.items() if abs(p[1]) < tolerance}
        outer = {
            n
            for n, p in nodes.items()
            if abs((p[0] / c.length) ** 2 + (p[1] / c.width) ** 2 - 1.0) <= ellipse_tol
        }
        mid_outer = {n for n in outer if abs(nodes[n][2] - c.height / 2.0) < tolerance}
        want = {(n, 1) for n in x_sym} | {(n, 2) for n in y_sym}
        want.update((n, d) for n in outer for d in (1, 2))
        want.update((n, 3) for n in mid_outer)
        if not mid_outer or bc != want:
            errors.append("thick-plate support boundary mismatch")
    elif c.family == "axisymmetric_pressure":
        pin = {
            n
            for n, p in nodes.items()
            if abs(p[0] - c.length) < tolerance and abs(p[1]) < tolerance
        }
        if bc != {(n, 2) for n in pin}:
            errors.append("axisymmetric axial rigid-body support mismatch")
    elif c.family == "axisymmetric_shell":
        if c.axisymmetric_shell_profile == "hyperbolic":
            k_nodes = {
                n
                for n, p in nodes.items()
                if abs(p[0] - c.radius) < tolerance and abs(p[1]) < tolerance
            }
            if len(k_nodes) != 1 or bc != {(n, 2) for n in k_nodes}:
                errors.append("axisymmetric hyperbolic-shell K support mismatch")
        elif c.axisymmetric_shell_profile == "sphere_cylinder":
            pole_nodes = {
                n
                for n, p in nodes.items()
                if abs(p[0]) < tolerance and abs(p[1] - c.length) < tolerance
            }
            bottom_nodes = {
                n
                for n, p in nodes.items()
                if abs(p[0] - c.radius) < tolerance and abs(p[1]) < tolerance
            }
            wanted = {(n, dof) for n in pole_nodes for dof in (1, 6)}
            wanted.update((n, 2) for n in bottom_nodes)
            if len(pole_nodes) != 1 or len(bottom_nodes) != 1 or bc != wanted:
                errors.append("axisymmetric sphere-cylinder support mismatch")
        elif c.axisymmetric_shell_profile == "crown_knuckle_cylinder":
            pole_nodes = {
                n
                for n, p in nodes.items()
                if abs(p[0]) < tolerance and abs(p[1] - c.width) < tolerance
            }
            bottom_nodes = {
                n
                for n, p in nodes.items()
                if abs(p[0] - c.radius) < tolerance and abs(p[1]) < tolerance
            }
            wanted = {(n, dof) for n in pole_nodes for dof in (1, 6)}
            wanted.update((n, 2) for n in bottom_nodes)
            if len(pole_nodes) != 1 or len(bottom_nodes) != 1 or bc != wanted:
                errors.append("axisymmetric crown-knuckle-cylinder support mismatch")
        elif c.axisymmetric_shell_profile == "branched_sphere_cylinder":
            bottom_nodes = {
                n
                for n, p in nodes.items()
                if abs(p[0] - c.radius) < tolerance and abs(p[1]) < tolerance
            }
            wanted = {(n, dof) for n in bottom_nodes for dof in (1, 2, 6)}
            if len(bottom_nodes) != 1 or bc != wanted:
                errors.append("branched axisymmetric-shell bottom clamp mismatch")
        else:
            errors.append("unsupported axisymmetric-shell profile")
    elif c.family == "skew_plate":
        angle = math.radians(c.skew_angle_degrees)
        sine = math.sin(angle)
        cosine = math.cos(angle)
        edge_nodes = set()
        point_a = set()
        point_b = set()
        for n, p in nodes.items():
            eta = p[1] / sine
            xi = p[0] - eta * cosine
            if (
                abs(xi) < tolerance
                or abs(xi - c.length) < tolerance
                or abs(eta) < tolerance
                or abs(eta - c.width) < tolerance
            ):
                edge_nodes.add(n)
            if abs(xi) < tolerance and abs(eta) < tolerance:
                point_a.add(n)
            if abs(xi - c.length) < tolerance and abs(eta) < tolerance:
                point_b.add(n)
        want = {(n, 3) for n in edge_nodes}
        want.update((n, d) for n in point_a for d in (1, 2))
        want.update((n, 2) for n in point_b)
        if len(point_a) != 1 or len(point_b) != 1 or bc != want:
            errors.append("skew-plate simply-supported boundary mismatch")
    elif c.family == "hemisphere_shell":
        x_sym = {n for n, p in nodes.items() if abs(p[0]) < tolerance}
        y_sym = {n for n, p in nodes.items() if abs(p[1]) < tolerance}
        top = {
            n
            for n, p in nodes.items()
            if abs(p[0]) < tolerance
            and abs(p[1]) < tolerance
            and abs(p[2] - c.radius) < tolerance
        }
        want = {(n, d) for n in x_sym for d in (1, 5, 6)}
        want.update((n, d) for n in y_sym for d in (2, 4, 6))
        want.update((n, 3) for n in top)
        if len(top) != 1 or bc != want:
            errors.append("hemisphere shell symmetry/support boundary mismatch")
    elif c.family == "z_section_shell":
        if bc != want:
            errors.append("Z-section cantilever clamp mismatch")
    elif c.family == "cylindrical_shell_patch":
        angle_tol = max(c.radius * 2e-5, tolerance)
        max_angle = math.radians(c.arc_angle_degrees)
        clamp = {n for n, p in nodes.items() if abs(math.atan2(p[1], p[0])) < angle_tol}
        axial_edges = {
            n
            for n, p in nodes.items()
            if abs(p[2]) < tolerance or abs(p[2] - c.length) < tolerance
        }
        want = {(n, d) for n in clamp for d in range(1, 7)}
        want.update((n, d) for n in axial_edges for d in (3, 4, 5))
        if not clamp or not axial_edges or bc != want:
            errors.append("cylindrical-shell clamp/axial symmetry boundary mismatch")
    elif c.family == "thermal_solid":
        x_zero = {n for n, p in nodes.items() if abs(p[0]) < tolerance}
        y_zero = {n for n, p in nodes.items() if abs(p[1]) < tolerance}
        bottom = {n for n, p in nodes.items() if abs(p[2]) < tolerance}
        top = {n for n, p in nodes.items() if abs(p[2] - c.height) < tolerance}
        want = {(n, 1) for n in x_zero} | {(n, 2) for n in y_zero}
        want.update((n, 3) for n in bottom | top)
        if not x_zero or not y_zero or not bottom or not top or bc != want:
            errors.append("thermal-solid symmetry/end boundary mismatch")
    elif bc != want:
        errors.append("physical support/extra boundary mismatch")
    temperature_blocks = [b for b in data if b.keyword == "*temperature"]
    if c.family == "thermal_solid":
        assigned: dict[int, float] = {}
        if len(temperature_blocks) != 1:
            errors.append("one explicit nodal temperature block required")
        else:
            block = temperature_blocks[0]
            if block.options or block.flags or block.step != 0:
                errors.append(
                    "thermal-solid temperature must be explicit in the static step"
                )
            for row in block.rows:
                if len(row) != 2:
                    errors.append(
                        "thermal-solid temperature row must contain region and value"
                    )
                    continue
                ids = resolve(row[0], "*nset", block.scope)
                value = _number(row[1])
                for label in ids:
                    if label in assigned:
                        errors.append(
                            "thermal-solid node has duplicate temperature assignments"
                        )
                    assigned[label] = value
            if set(assigned) != set(nodes):
                errors.append(
                    "thermal-solid temperature must cover every material node"
                )
            for label, value in assigned.items():
                x, y, z = nodes[label]
                expected_temperature = math.hypot(x, y) + z
                if not math.isclose(
                    value, expected_temperature, rel_tol=1e-7, abs_tol=1e-8
                ):
                    errors.append("thermal-solid nodal temperature field mismatch")
                    break
    elif temperature_blocks:
        errors.append("temperature loading is unsupported for this case")
    effective = {"*cload": [], "*dload": [], "*dsload": []}
    for step in range(expected_steps):
        # OP=NEW 清除前一步传播的载荷；同一步的多个块共同定义新载荷。
        reset = set()
        for b in data:
            if b.keyword in effective and b.step == step:
                if b.options.get("op", "") == "new":
                    if b.keyword not in reset:
                        effective[b.keyword] = []
                        reset.add(b.keyword)
                elif step > 0:
                    errors.append("later load steps must reset previous loads")
                if any(k != "op" for k in b.options):
                    errors.append("load options alter fixed task")
                effective[b.keyword].extend(b.rows)
        # 有效载荷按数值解释，显式零分量不是额外物理载荷。
        for kind, rows in effective.items():
            effective[kind] = [
                row for row in rows if len(row) < 3 or _number(row[2]) != 0.0
            ]
        if c.family == "plane_stress":
            if any(effective.values()):
                errors.append("plane-stress displacement case must not add force loads")
        elif c.family == "thermal_solid":
            if any(effective.values()):
                errors.append("thermal-solid case must not add mechanical loads")
        elif c.family == "elliptic_membrane":
            if effective["*cload"]:
                errors.append(
                    "elliptic membrane must use outer-edge pressure, not nodal loads"
                )
                continue
            elliptic_loaded_faces: set[tuple[int, int]] = set()
            if effective["*dload"] and effective["*dsload"]:
                errors.append("elliptic membrane cannot mix DLOAD and DSLOAD pressure")
                continue
            for row in effective["*dload"]:
                if len(row) != 3 or not row[1].upper().startswith("P"):
                    errors.append(
                        "elliptic-membrane DLOAD must be an element-edge pressure"
                    )
                    continue
                try:
                    face = int(row[1][1:])
                except ValueError:
                    errors.append("elliptic-membrane pressure face is invalid")
                    continue
                if not math.isclose(_number(row[2]), -c.load, rel_tol=1e-7):
                    errors.append(
                        "elliptic-membrane pressure magnitude/direction mismatch"
                    )
                for label in resolve(row[0], "*elset"):
                    if face not in _plane_stress_faces(elements[label]):
                        errors.append("elliptic-membrane pressure face is invalid")
                        continue
                    elliptic_loaded_faces.add((label, face))
            for row in effective["*dsload"]:
                if (
                    len(row) != 3
                    or row[1].upper() != "P"
                    or not math.isclose(_number(row[2]), -c.load, rel_tol=1e-7)
                ):
                    errors.append(
                        "elliptic-membrane pressure magnitude/direction mismatch"
                    )
                    continue
                surfaces = [
                    b
                    for b in data
                    if b.keyword == "*surface"
                    and b.options.get("name") == row[0].lower()
                ]
                if len(surfaces) != 1:
                    errors.append("missing unique elliptic-membrane pressure surface")
                    continue
                for region, side in surfaces[0].rows:
                    if not side.upper().startswith("S"):
                        errors.append("elliptic-membrane surface face is invalid")
                        continue
                    try:
                        face = int(side[1:])
                    except ValueError:
                        errors.append("elliptic-membrane surface face is invalid")
                        continue
                    for label in resolve(region, "*elset", surfaces[0].scope):
                        if face not in _plane_stress_faces(elements[label]):
                            errors.append("elliptic-membrane surface face is invalid")
                            continue
                        elliptic_loaded_faces.add((label, face))
            if (
                not elliptic_loaded_faces
                or elliptic_loaded_faces != elliptic_outer_faces
            ):
                errors.append(
                    "elliptic-membrane pressure must cover only the complete outer edge"
                )
        elif c.family == "thick_plate":
            if effective["*cload"]:
                errors.append(
                    "thick plate must use top-surface pressure, not nodal loads"
                )
                continue
            if effective["*dload"] and effective["*dsload"]:
                errors.append("thick plate cannot mix DLOAD and DSLOAD pressure")
                continue
            expected_top_faces = set()
            for label, element in elements.items():
                for face, face_nodes in _solid_faces(element).items():
                    if all(abs(nodes[n][2] - c.height) < tolerance for n in face_nodes):
                        expected_top_faces.add((label, face))
            thick_loaded_faces: set[tuple[int, int]] = set()
            for row in effective["*dload"]:
                if len(row) != 3 or not row[1].upper().startswith("P"):
                    errors.append("thick-plate DLOAD must be element-face pressure")
                    continue
                try:
                    face = int(row[1][1:])
                except ValueError:
                    errors.append("thick-plate pressure face is invalid")
                    continue
                if not math.isclose(_number(row[2]), c.load, rel_tol=1e-7):
                    errors.append("thick-plate pressure magnitude/direction mismatch")
                for label in resolve(row[0], "*elset"):
                    if face not in _solid_faces(elements[label]):
                        errors.append("thick-plate pressure face is invalid")
                        continue
                    thick_loaded_faces.add((label, face))
            for row in effective["*dsload"]:
                if (
                    len(row) != 3
                    or row[1].upper() != "P"
                    or not math.isclose(_number(row[2]), c.load, rel_tol=1e-7)
                ):
                    errors.append("thick-plate pressure magnitude/direction mismatch")
                    continue
                surfaces = [
                    b
                    for b in data
                    if b.keyword == "*surface"
                    and b.options.get("name") == row[0].lower()
                ]
                if len(surfaces) != 1:
                    errors.append("missing unique thick-plate pressure surface")
                    continue
                for region, side in surfaces[0].rows:
                    if not side.upper().startswith("S"):
                        errors.append("thick-plate surface face is invalid")
                        continue
                    try:
                        face = int(side[1:])
                    except ValueError:
                        errors.append("thick-plate surface face is invalid")
                        continue
                    for label in resolve(region, "*elset", surfaces[0].scope):
                        if face not in _solid_faces(elements[label]):
                            errors.append("thick-plate surface face is invalid")
                            continue
                        thick_loaded_faces.add((label, face))
            if not expected_top_faces or thick_loaded_faces != expected_top_faces:
                errors.append(
                    "thick-plate pressure must cover only the complete top surface"
                )
        elif c.family == "axisymmetric_shell":
            if c.axisymmetric_shell_profile == "branched_sphere_cylinder":
                if (
                    effective["*cload"]
                    or effective["*dsload"]
                    or not effective["*dload"]
                ):
                    errors.append(
                        "branched axisymmetric shell requires DLOAD pressure only"
                    )
                    continue
                loaded = set()
                for row in effective["*dload"]:
                    if (
                        len(row) != 3
                        or row[1].upper() != "P"
                        or not math.isclose(_number(row[2]), -c.load, rel_tol=1e-7)
                    ):
                        errors.append(
                            "branched axisymmetric shell pressure magnitude/type mismatch"
                        )
                        continue
                    ids = resolve(row[0], "*elset")
                    if loaded & ids:
                        errors.append(
                            "branched axisymmetric shell pressure sets overlap"
                        )
                    loaded.update(ids)
                expected_loaded = set()
                for label, element in elements.items():
                    points = [nodes[n] for n in element["nodes"]]
                    is_cylinder = all(
                        abs(point[0] - c.radius) <= 2e-4 for point in points
                    )
                    is_sphere = all(
                        abs(
                            point[0] * point[0]
                            + (point[1] - c.branch_center_axial_position)
                            * (point[1] - c.branch_center_axial_position)
                            - c.branch_sphere_radius * c.branch_sphere_radius
                        )
                        <= 2e-4
                        for point in points
                    )
                    if is_sphere or (
                        is_cylinder
                        and sum(point[1] for point in points) / len(points)
                        >= c.sample_axial_position - tolerance
                    ):
                        expected_loaded.add(label)
                if loaded != expected_loaded:
                    errors.append(
                        "branched axisymmetric shell pressure must cover only upper cylinder and sphere"
                    )
                continue
            if (
                effective["*cload"]
                or effective["*dsload"]
                or len(effective["*dload"]) != 1
            ):
                errors.append(
                    "axisymmetric shell requires one full-shell DLOAD pressure"
                )
                continue
            row = effective["*dload"][0]
            if (
                len(row) != 3
                or row[1].upper() != "P"
                or not math.isclose(_number(row[2]), c.load, rel_tol=1e-7)
            ):
                errors.append("axisymmetric shell pressure magnitude/type mismatch")
                continue
            loaded = resolve(row[0], "*elset")
            if loaded != set(elements):
                errors.append("axisymmetric shell pressure does not cover all elements")
        elif c.family == "axisymmetric_pressure":
            if (
                effective["*cload"]
                or effective["*dload"]
                or len(effective["*dsload"]) != 1
            ):
                errors.append("axisymmetric cylinder requires one inner-wall pressure")
                continue
            row = effective["*dsload"][0]
            if (
                len(row) != 3
                or row[1].upper() != "P"
                or not math.isclose(_number(row[2]), c.load, rel_tol=1e-7)
            ):
                errors.append("inner-wall pressure magnitude/type mismatch")
                continue
            surfaces = [
                b
                for b in data
                if b.keyword == "*surface" and b.options.get("name") == row[0].lower()
            ]
            if len(surfaces) != 1:
                errors.append("missing unique inner-wall surface")
                continue
            face_positions = {
                "S1": (0, 1),
                "S2": (1, 2),
                "S3": (2, 3),
                "S4": (3, 0),
            }
            faces = set()
            for region, face in surfaces[0].rows:
                face = face.upper()
                if face not in face_positions:
                    errors.append("unsupported axisymmetric element face")
                    continue
                for label in resolve(region, "*elset", surfaces[0].scope):
                    element = elements[label]
                    labels = tuple(element["nodes"][j] for j in face_positions[face])
                    if not all(
                        abs(nodes[n][0] - c.inner_radius) < tolerance for n in labels
                    ):
                        errors.append(
                            "pressure surface is not confined to the inner wall"
                        )
                    if (label, face) in faces:
                        errors.append("duplicate loaded face")
                    faces.add((label, face))
            expected_inner_faces = set()
            for label, element in elements.items():
                for face, positions in face_positions.items():
                    labels = tuple(element["nodes"][j] for j in positions)
                    if all(
                        abs(nodes[n][0] - c.inner_radius) < tolerance for n in labels
                    ):
                        expected_inner_faces.add((label, face))
            if not faces or faces != expected_inner_faces:
                errors.append("pressure does not cover the complete inner wall")
        elif c.family == "skew_plate":
            if (
                effective["*cload"]
                or effective["*dload"]
                or len(effective["*dsload"]) != 1
            ):
                errors.append("skew plate requires one qualified SPOS surface pressure")
                continue
            row = effective["*dsload"][0]
            if (
                len(row) != 3
                or row[1].upper() != "P"
                or not math.isclose(_number(row[2]), c.load, rel_tol=1e-7)
            ):
                errors.append("skew-plate pressure magnitude/type mismatch")
                continue
            surfaces = [
                b
                for b in data
                if b.keyword == "*surface" and b.options.get("name") == row[0].lower()
            ]
            if len(surfaces) != 1:
                errors.append("missing unique skew-plate pressure surface")
                continue
            skew_loaded_elements: set[int] = set()
            for region, side in surfaces[0].rows:
                if side.upper() != "SPOS":
                    errors.append(
                        "skew-plate pressure must use the qualified SPOS side"
                    )
                    continue
                ids = resolve(region, "*elset", surfaces[0].scope)
                if skew_loaded_elements & ids:
                    errors.append("overlapping skew-plate pressure surface")
                skew_loaded_elements.update(ids)
            if skew_loaded_elements != set(elements):
                errors.append("skew-plate pressure does not cover all elements")
        elif c.family == "hemisphere_shell":
            if effective["*dload"] or effective["*dsload"]:
                errors.append(
                    "hemisphere point-load case must not use distributed loads"
                )
                continue
            point_a = {
                n
                for n, p in nodes.items()
                if abs(p[0] - c.radius) < tolerance
                and abs(p[1]) < tolerance
                and abs(p[2]) < tolerance
            }
            point_c = {
                n
                for n, p in nodes.items()
                if abs(p[0]) < tolerance
                and abs(p[1] - c.radius) < tolerance
                and abs(p[2]) < tolerance
            }
            totals = {"A": 0.0, "C": 0.0}
            for row in effective["*cload"]:
                if len(row) != 3:
                    errors.append("hemisphere point load row is invalid")
                    continue
                ids = resolve(row[0], "*nset")
                dof = int(row[1])
                value = _number(row[2])
                if ids <= point_a and dof == 1:
                    totals["A"] += len(ids) * value
                elif ids <= point_c and dof == 2:
                    totals["C"] += len(ids) * value
                else:
                    errors.append(
                        "hemisphere load is outside A/C or uses the wrong component"
                    )
            if not math.isclose(totals["A"], c.load, rel_tol=1e-7) or not math.isclose(
                totals["C"], -c.load, rel_tol=1e-7
            ):
                errors.append("hemisphere A/C point-load magnitude/direction mismatch")
        elif c.family == "z_section_shell":
            if effective["*dload"] or effective["*dsload"]:
                errors.append("Z-section torque case must use end CLOAD shears only")
                continue
            lower_total = 0.0
            upper_total = 0.0
            net_fz = 0.0
            torque_x = 0.0
            for row in effective["*cload"]:
                if len(row) != 3:
                    errors.append("Z-section end-load row is invalid")
                    continue
                ids = resolve(row[0], "*nset")
                dof = int(row[1])
                value = _number(row[2])
                if dof != 3:
                    errors.append("Z-section end loads must act in global Z")
                    continue
                for label in ids:
                    x, y, _ = nodes[label]
                    if abs(x - c.length) >= tolerance or not math.isclose(
                        abs(y), 1.0, rel_tol=0.0, abs_tol=tolerance
                    ):
                        errors.append(
                            "Z-section load must lie on an x=10 free-flange end edge"
                        )
                        continue
                    net_fz += value
                    torque_x += y * value
                    if y < 0:
                        lower_total += value
                    else:
                        upper_total += value
            if not math.isclose(
                net_fz, 0.0, rel_tol=0.0, abs_tol=max(c.load * 1e-7, 1e-8)
            ):
                errors.append("Z-section end loads must have zero resultant force")
            if not math.isclose(torque_x, c.load, rel_tol=1e-7):
                errors.append("Z-section end torque magnitude/direction mismatch")
            if not math.isclose(
                lower_total, -c.load / 2.0, rel_tol=1e-7
            ) or not math.isclose(upper_total, c.load / 2.0, rel_tol=1e-7):
                errors.append("Z-section flange shear resultants mismatch")
        elif c.family == "cylindrical_shell_patch":
            if effective["*dload"] or effective["*dsload"]:
                errors.append(
                    "cylindrical shell edge moment must use nodal moments only"
                )
                continue
            total_m3 = 0.0
            max_angle = math.radians(c.arc_angle_degrees)
            angle_tol = max(c.radius * 2e-5, tolerance)
            for row in effective["*cload"]:
                if len(row) != 3:
                    errors.append("cylindrical shell edge-moment row is invalid")
                    continue
                ids = resolve(row[0], "*nset")
                dof = int(row[1])
                value = _number(row[2])
                if dof != 6 or value <= 0:
                    errors.append("cylindrical shell edge moments must act about +axis")
                    continue
                for label in ids:
                    theta = math.atan2(nodes[label][1], nodes[label][0])
                    if abs(theta - max_angle) >= angle_tol:
                        errors.append("cylindrical shell moment is outside edge DC")
                        continue
                    total_m3 += value
            if not math.isclose(total_m3, c.load * c.length, rel_tol=5e-6):
                errors.append("cylindrical shell edge-moment resultant mismatch")
        elif c.family == "hole":
            if (
                effective["*cload"]
                or effective["*dload"]
                or len(effective["*dsload"]) != 1
            ):
                errors.append("hole requires one face load only")
                continue
            row = effective["*dsload"][0]
            if row[1].upper() == "P":
                valid = math.isclose(_number(row[2]), -c.load, rel_tol=1e-7)
            elif row[1].upper() == "TRVEC":
                valid = len(row) == 6 and all(
                    math.isclose(_number(a), b, abs_tol=1e-7)
                    for a, b in zip(row[2:], [c.load, 1, 0, 0], strict=True)
                )
            else:
                valid = False
            if not valid:
                errors.append("surface load magnitude/direction mismatch")
            surfaces = [
                b
                for b in data
                if b.keyword == "*surface" and b.options.get("name") == row[0].lower()
            ]
            if len(surfaces) != 1:
                errors.append("missing unique loaded surface")
                continue
            faces = set()
            for region, face in surfaces[0].rows:
                for label in resolve(region, "*elset", surfaces[0].scope):
                    element = elements[label]
                    positions = (
                        {
                            "S1": (0, 1, 2),
                            "S2": (0, 3, 1),
                            "S3": (1, 3, 2),
                            "S4": (2, 3, 0),
                        }
                        if element["type"] in {"C3D4", "C3D10"}
                        else {
                            "S1": (0, 1, 2, 3),
                            "S2": (4, 7, 6, 5),
                            "S3": (0, 4, 5, 1),
                            "S4": (1, 5, 6, 2),
                            "S5": (2, 6, 7, 3),
                            "S6": (3, 7, 4, 0),
                        }
                    )[face.upper()]
                    labels = tuple(element["nodes"][j] for j in positions)
                    if not set(labels) <= right:
                        errors.append("surface load is not confined to right end")
                    if (label, face) in faces:
                        errors.append("duplicate loaded face")
                    faces.add((label, face))
            if not faces:
                errors.append("empty loaded surface")
        elif c.distributed:
            if effective["*cload"] or effective["*dsload"]:
                errors.append("distributed load replaced by other load")
            beam_loaded_elements: set[int] = set()
            for row in effective["*dload"]:
                ids = resolve(row[0], "*elset")
                if row[1].upper() != "PY" or not math.isclose(
                    _number(row[2]), -c.load, rel_tol=1e-7
                ):
                    errors.append("beam line load mismatch")
                if beam_loaded_elements & ids:
                    errors.append("overlapping line loads")
                beam_loaded_elements.update(ids)
            if beam_loaded_elements != set(elements):
                errors.append("line load does not cover full beam")
        else:
            if effective["*dload"] or effective["*dsload"]:
                errors.append("unexpected distributed load")
            totals = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            axis = (
                3
                if c.family == "shaft"
                else step
                if c.family == "solid_beam"
                else 1
                if c.family == "beam"
                else 0
            )
            sign = -1 if c.family == "beam" else 1
            moments = [0.0, 0.0]
            for row in effective["*cload"]:
                ids = resolve(row[0], "*nset")
                dof = int(row[1])
                value = _number(row[2])
                permitted_dofs = {4} if c.family == "shaft" else {1, 2, 3}
                if not ids <= right or dof not in permitted_dofs:
                    errors.append("load outside free end or invalid component")
                    continue
                if dof != axis + 1 or value * sign <= 0:
                    errors.append("opposing/extra load components are not allowed")
                totals[dof - 1] += len(ids) * value
                if c.family == "solid_beam":
                    moments[0] += sum((nodes[n][1] - c.width / 2) * value for n in ids)
                    moments[1] += sum((nodes[n][2] - c.height / 2) * value for n in ids)
            if any(abs(x) > max(abs(c.load) * c.width * 1e-6, 1e-9) for x in moments):
                errors.append("end load is not centered/symmetric")
            expected_load = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            expected_load[
                3
                if c.family == "shaft"
                else step
                if c.family == "solid_beam"
                else 1
                if c.family == "beam"
                else 0
            ] = c.load * (-1 if c.family == "beam" else 1)
            if any(
                not math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-8)
                for a, b in zip(totals, expected_load, strict=True)
            ):
                errors.append(
                    f"load case {step + 1} is not the declared independent resultant"
                )
    return list(dict.fromkeys(errors))
