"""梁截面修改任务要求保持的轴向边界合同。"""

from pathlib import Path

_FORBIDDEN_LOAD_KEYWORDS = {
    "*body force",
    "*cflux",
    "*cload",
    "*connector load",
    "*dflux",
    "*dload",
    "*dsflux",
    "*dsload",
    "*film",
    "*foundation",
    "*radiate",
}


def beam_input_contract_passed(path: Path) -> bool:
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp936", errors="replace")

    keyword = ""
    step_headers: list[str] = []
    static_count = 0
    forbidden_load_found = False
    boundary_rows: list[tuple[str, ...]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            keyword = line.split(",", 1)[0].strip().casefold()
            if keyword == "*step":
                step_headers.append(line.upper().replace(" ", ""))
            elif keyword == "*static":
                static_count += 1
            elif keyword in _FORBIDDEN_LOAD_KEYWORDS:
                forbidden_load_found = True
            continue
        if keyword == "*boundary":
            boundary_rows.append(
                tuple(part.strip() for part in line.split(",") if part.strip())
            )

    if (
        len(step_headers) != 1
        or "NLGEOM=NO" not in step_headers[0]
        or static_count != 1
        or len(boundary_rows) != 2
        or forbidden_load_found
    ):
        return False
    encastre_rows = [
        row for row in boundary_rows if len(row) == 2 and row[1].upper() == "ENCASTRE"
    ]
    displacement_rows = [row for row in boundary_rows if _is_right_u1_row(row)]
    return (
        len(encastre_rows) == 1
        and len(displacement_rows) == 1
        and encastre_rows[0][0].casefold() != displacement_rows[0][0].casefold()
    )


def _is_right_u1_row(row: tuple[str, ...]) -> bool:
    if len(row) != 4:
        return False
    try:
        first_dof = int(row[1])
        last_dof = int(row[2])
        magnitude = float(row[3])
    except ValueError:
        return False
    return first_dof == last_dof == 1 and abs(magnitude - 0.1) <= 1.0e-12
