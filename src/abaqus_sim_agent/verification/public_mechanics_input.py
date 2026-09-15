"""六个公开力学 Case 的有界 INP 校验；不支持任意 Abaqus 输入语法。"""

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
        "*element",
        "*nset",
        "*elset",
        "*solid section",
        "*beam section",
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
        "*el print",
        "*section controls",
    }
    bad = {b.keyword for b in data} - allowed
    if bad:
        return [f"Unsupported physical/input keywords: {sorted(bad)}"]
    errors = []
    nodes = {int(k): v for k, v in m["nodes"].items()}
    elements = {int(k): v for k, v in m["elements"].items()}
    tolerance = max(c.length * 1e-7, 1e-8)
    left = {n for n, p in nodes.items() if abs(p[0]) < tolerance}
    right = {n for n, p in nodes.items() if abs(p[0] - c.length) < tolerance}
    sets = {}
    for b in data:
        if b.keyword in {"*nset", "*elset"}:
            key = (b.keyword, b.scope, b.options[b.keyword[1:]])
            sets.setdefault(key, []).append(b)

    def resolve(name, kind, scope="assembly", seen=()):
        name = name.lower()
        if name.isdigit():
            return {int(name)}
        if "." in name:
            name = name.split(".", 1)[1]
            scope = "part"
        key = (kind, scope, name)
        if key in seen:
            raise ValueError("cyclic set reference")
        if key not in sets:
            raise ValueError(f"unknown set {key}")
        ids = set()
        for b in sets[key]:
            for row in b.rows:
                if "generate" in b.flags:
                    a, z, step = map(int, row)
                    if step <= 0 or z < a:
                        raise ValueError("invalid generated set")
                    ids.update(range(a, z + 1, step))
                else:
                    for token in row:
                        ids.update(resolve(token, kind, scope, (*seen, key)))
        return ids

    types = {e["type"] for e in elements.values()}
    permitted = (
        {"T3D2", "T3D3"}
        if c.family == "truss"
        else {"B31", "B32"}
        if c.family == "beam"
        else {"C3D4", "C3D10", "C3D8", "C3D8R", "C3D20", "C3D20R"}
    )
    if not types or not types <= permitted:
        errors.append(f"element representation mismatch: {types}")
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
    if used != set(nodes) or len({find(n) for n in nodes}) != 1:
        errors.append("orphan nodes/disconnected material mesh")
    bounds = [
        (min(p[i] for p in nodes.values()), max(p[i] for p in nodes.values()))
        for i in range(3)
    ]
    expected = [
        (0, c.length),
        (0, 0 if c.family in {"truss", "beam"} else c.width),
        (0, 0 if c.family in {"truss", "beam"} else c.height),
    ]
    if any(
        abs(a - b) > tolerance
        for pair, target in zip(bounds, expected, strict=True)
        for a, b in zip(pair, target, strict=True)
    ):
        errors.append("geometry bounding box mismatch")
    if c.family == "hole" and any(
        math.hypot(p[0] - 7.5, p[1] - 2.5) < 0.5 - 1e-5 for p in nodes.values()
    ):
        errors.append("mesh fills the specified hole")
    if c.family in {"hole", "solid_beam"}:
        expected_volume = c.length * c.width * c.height - (
            math.pi * 0.5**2 * c.height if c.family == "hole" else 0
        )
        if not isinstance(m.get("volume"), (float, int)) or not math.isclose(
            m["volume"], expected_volume, rel_tol=0.005
        ):
            errors.append("solid volume mismatch or missing EVOL")
    material_blocks = [b for b in data if b.keyword == "*elastic"]
    if (
        len(material_blocks) != 1
        or material_blocks[0].options.get("type", "isotropic") != "isotropic"
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
    if any(
        b.flags or any(k not in {"name", "nlgeom", "inc"} for k in b.options)
        for b in steps
    ):
        errors.append("unsupported step alteration")
    if (
        len([b for b in data if b.keyword == "*part"]) != 1
        or len([b for b in data if b.keyword == "*instance"]) != 1
    ):
        errors.append("one part/instance required")
    if any(b.rows for b in data if b.keyword == "*instance"):
        errors.append("translated/rotated instance not in this coordinate contract")
    if any(b.keyword == "*node" and b.scope != "part" for b in data):
        errors.append("extra assembly/reference nodes in INP")
    # 一维网格必须连续覆盖每段，禁止叠杆或跨越截面断点。
    if c.family in {"truss", "beam"}:
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
    sections = [b for b in data if b.keyword in {"*solid section", "*beam section"}]
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
    if covered != set(elements):
        errors.append("section coverage mismatch")
    bc = set()
    for b in data:
        if b.keyword != "*boundary":
            continue
        if b.options or b.flags:
            errors.append("boundary options unsupported")
        for row in b.rows:
            ids = resolve(row[0], "*nset")
            if row[1].upper() == "ENCASTRE":
                dofs = range(1, 7 if c.family == "beam" else 4)
                value = 0
            else:
                dofs = range(int(row[1]), int(row[2] if len(row) > 2 else row[1]) + 1)
                value = _number(row[3]) if len(row) > 3 else 0
            if value != 0:
                errors.append("nonzero displacement replaces force load")
            bc.update((n, dof) for n in ids for dof in dofs)
    want = {(n, d) for n in left for d in range(1, 7 if c.family == "beam" else 4)}
    if c.family == "truss":
        want = {(n, 1) for n in left} | {(n, d) for n in nodes for d in (2, 3)}
    if bc != want:
        errors.append("physical support/extra boundary mismatch")
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
        if c.family == "hole":
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
            loaded = set()
            for row in effective["*dload"]:
                ids = resolve(row[0], "*elset")
                if row[1].upper() != "PY" or not math.isclose(
                    _number(row[2]), -c.load, rel_tol=1e-7
                ):
                    errors.append("beam line load mismatch")
                if loaded & ids:
                    errors.append("overlapping line loads")
                loaded.update(ids)
            if loaded != set(elements):
                errors.append("line load does not cover full beam")
        else:
            if effective["*dload"] or effective["*dsload"]:
                errors.append("unexpected distributed load")
            totals = [0.0, 0.0, 0.0]
            axis = step if c.family == "solid_beam" else 1 if c.family == "beam" else 0
            sign = -1 if c.family == "beam" else 1
            moments = [0.0, 0.0]
            for row in effective["*cload"]:
                ids = resolve(row[0], "*nset")
                dof = int(row[1])
                value = _number(row[2])
                if not ids <= right or dof not in {1, 2, 3}:
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
            expected_load = [0.0, 0.0, 0.0]
            expected_load[
                step if c.family == "solid_beam" else 1 if c.family == "beam" else 0
            ] = c.load * (-1 if c.family == "beam" else 1)
            if any(
                not math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-8)
                for a, b in zip(totals, expected_load, strict=True)
            ):
                errors.append(
                    f"load case {step + 1} is not the declared independent resultant"
                )
    return list(dict.fromkeys(errors))
