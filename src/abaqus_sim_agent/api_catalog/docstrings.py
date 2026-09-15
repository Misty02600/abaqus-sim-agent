"""保留原始说明，只解析当前stub中明确的摘要和NumPy风格参数段。"""

import ast
import hashlib
import inspect
import re

from .models import ApiDocstring

_HEADING = re.compile(r"(?m)^([A-Za-z][A-Za-z ]*)\n-{3,}[ \t]*$")
_PARAMETER = re.compile(r"^(\*{0,2}\w+(?:\s*,\s*\*{0,2}\w+)*)(?:\s*:\s*.*)?$")


def extract_docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef, source_sha256: str
) -> ApiDocstring:
    raw = ast.get_docstring(node, clean=False)
    common = {
        "source_line": node.lineno,
        "source_end_line": node.end_lineno or node.lineno,
        "source_sha256": source_sha256,
    }
    if raw is None:
        return ApiDocstring(raw=None, parse_status="absent", **common)
    clean = inspect.cleandoc(raw)
    headings = list(_HEADING.finditer(clean))
    intro = clean[: headings[0].start()] if headings else clean
    summary = " ".join(intro.split("\n\n", 1)[0].split())
    sections = [
        clean[
            m.end() : headings[i + 1].start() if i + 1 < len(headings) else len(clean)
        ]
        for i, m in enumerate(headings)
        if m.group(1) == "Parameters"
    ]
    parameters = {}
    partial = False
    for section in sections:
        names = []
        lines = []
        for line in [*section.splitlines(), "__END__"]:
            if line and not line[0].isspace():
                if names:
                    description = "\n".join(lines).strip()
                    for name in names:
                        if name in parameters or not description:
                            partial = True
                        else:
                            parameters[name] = description
                names, lines = [], []
                if line == "__END__":
                    break
                match = _PARAMETER.fullmatch(line.strip())
                if match:
                    names = [n.strip().lstrip("*") for n in match.group(1).split(",")]
                else:
                    partial = True
            elif names:
                lines.append(line[4:] if line.startswith("    ") else line.lstrip())
            elif line.strip():
                partial = True
    return ApiDocstring(
        raw=raw,
        summary=summary,
        parameters=parameters,
        parse_status="partial"
        if partial
        else "parameters_parsed"
        if sections
        else "summary_only",
        docstring_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        **common,
    )
