"""限定范围的结构检索视图；原文不变，不进行公式求解或隐式丢弃。"""

import hashlib
import json
import re

import lxml.etree as etree

from .evidence_blocks import TokenBoundChildren, retrieval_projection
from .evidence_units import EvidenceUnit

PROJECTION_VERSION = "table-rows-mathml-subset-v1"
HEADERLESS_PAIR_VERSION = "headerless-two-column-rows-v1"


class UnsupportedProjection(ValueError):
    pass


def _tag(node: etree._Element) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def _parse(xml: str) -> etree._Element:
    if "<!" in xml:
        raise UnsupportedProjection("xml_declaration_or_entity")
    try:
        return etree.fromstring(
            xml.encode(), etree.XMLParser(resolve_entities=False, no_network=True)
        )
    except etree.XMLSyntaxError as exc:
        raise UnsupportedProjection("invalid_xml") from exc


def mathml_projection(node: etree._Element) -> str:
    """序列化常见Presentation MathML子集，保留分组、上下标、分式及字体变体。

    Raises:
        UnsupportedProjection: 不支持的元素、改变含义的属性或不合法的元数。

    Note:
        输出是带显式括号的检索记法，不是LaTeX、计算表达式或数学等价性证明。
    """
    tag = _tag(node)
    allowed = {
        "math",
        "mrow",
        "mi",
        "mn",
        "mo",
        "mtext",
        "msub",
        "msup",
        "msubsup",
        "mfrac",
        "msqrt",
        "mroot",
    }
    if tag not in allowed:
        raise UnsupportedProjection("math_element:" + tag)
    for name, value in node.attrib.items():
        if name not in {
            "class",
            "id",
            "display",
            "mathvariant",
            "xmlns",
            "stretchy",
            "fence",
            "separator",
            "form",
            "lspace",
            "rspace",
            "scriptlevel",
            "displaystyle",
        }:
            raise UnsupportedProjection("math_attribute:" + name)
        if name == "scriptlevel" and value != "0":
            raise UnsupportedProjection("math_scriptlevel")
    if tag in {"mi", "mn", "mo", "mtext"}:
        if len(node) or not (node.text or "").strip():
            raise UnsupportedProjection("math_token_content")
        value = " ".join(str(node.text).split())
        value = (
            value.replace("\u2062", "×")
            .replace("\u2061", "[apply]")
            .replace("\u2063", ",")
            .replace("\u2064", "+")
        )
    else:
        if (node.text or "").strip() or any((n.tail or "").strip() for n in node):
            raise UnsupportedProjection("math_mixed_text")
        parts = [mathml_projection(n) for n in node]
        arity = {"msub": 2, "msup": 2, "msubsup": 3, "mfrac": 2, "mroot": 2}
        if not parts or (tag in arity and len(parts) != arity[tag]):
            raise UnsupportedProjection("math_arity:" + tag)
        if tag in {"math", "mrow"}:
            value = " ".join(parts)
            if tag == "mrow":
                value = "(" + value + ")"
        elif tag == "msqrt":
            value = "sqrt(" + " ".join(parts) + ")"
        elif tag == "mfrac":
            value = f"({parts[0]})/({parts[1]})"
        elif tag == "mroot":
            value = f"root({parts[0]}, index={parts[1]})"
        elif tag == "msub":
            value = f"({parts[0]})_({parts[1]})"
        elif tag == "msup":
            value = f"({parts[0]})^({parts[1]})"
        else:
            value = f"({parts[0]})_({parts[1]})^({parts[2]})"
    variant = node.get("mathvariant")
    return f"{variant}({value})" if variant else value


def _inline(node: etree._Element) -> str:
    tag = _tag(node)
    if tag == "math":
        return " [Formula: " + mathml_projection(node) + "] "
    if tag not in {
        "td",
        "th",
        "caption",
        "p",
        "span",
        "b",
        "strong",
        "i",
        "em",
        "a",
        "sup",
        "sub",
        "br",
        "code",
        "tt",
    }:
        raise UnsupportedProjection("table_inline_element:" + tag)
    if tag == "br" or "newline" in str(node.get("class", "")).split():
        if len(node) or (node.text or "").strip():
            raise UnsupportedProjection("nonempty_line_break")
        return " / "
    value = (node.text or "") + "".join(_inline(c) + (c.tail or "") for c in node)
    if tag == "sub":
        return "_(" + value + ")"
    if tag == "sup":
        return "^(" + value + ")"
    return value + (" / " if tag == "p" else "")


def _normal(node: etree._Element) -> str:
    return " ".join(_inline(node).split())


def table_projection(
    body: str, *, allow_headerless: bool = False
) -> tuple[str, str, tuple[int, ...]]:
    """处理矩形单层表头表，可显式允许无表头两列表；不推断列的业务含义。

    Args:
        body: 保存的表格XML及显式关联的附注。
        allow_headerless: 允许全为非空td、无表头声明的两列表，仅保留行内左右顺序。

    Returns:
        数据行检索文本、共享表格上下文和不可拆分的数据行结束偏移。

    Raises:
        UnsupportedProjection: 合并单元格、嵌套结构、缺失表头、未知关联等。
    """
    if body.count("[TableXML]") != 1:
        raise UnsupportedProjection("table_wrapper_count")
    before, rest = body.split("[TableXML]\n", 1)
    xml, after = rest.split("\n[/TableXML]", 1)
    table = _parse(xml)
    if _tag(table) != "table" or before.strip():
        raise UnsupportedProjection("table_wrapper_shape")
    if table.get("role") in {"presentation", "none"}:
        raise UnsupportedProjection("table_presentational_role")
    rows = []
    captions = []
    for child in table:
        tag = _tag(child)
        if tag == "caption":
            captions.append(_normal(child))
        elif tag == "tr":
            rows.append(child)
        elif tag in {"thead", "tbody"}:
            if any(_tag(n) != "tr" for n in child) or (child.text or "").strip():
                raise UnsupportedProjection("table_row_group")
            rows.extend(child)
        elif tag == "colgroup":
            if any(_tag(n) != "col" or len(n) or (n.text or "").strip() for n in child):
                raise UnsupportedProjection("table_colgroup")
        else:
            raise UnsupportedProjection("table_element:" + tag)
    for n in table.iter():
        if (
            _tag(n) in {"table", "thead", "tbody", "tr", "colgroup", "col"}
            and (n.text or "").strip()
        ):
            raise UnsupportedProjection("table_structural_text")
        if (
            n is not table
            and (n.tail or "").strip()
            and _tag(n.getparent()) in {"table", "thead", "tbody", "tr", "colgroup"}
        ):
            raise UnsupportedProjection("table_structural_tail")
    headerless = (
        bool(rows)
        and allow_headerless
        and all(_tag(cell) == "td" for row in rows for cell in row)
    )
    if headerless:
        if any(_tag(n) == "thead" for n in table):
            raise UnsupportedProjection("table_undeclared_header_in_thead")
        if any(len(row) != 2 for row in rows):
            raise UnsupportedProjection("table_headerless_requires_two_columns")
    elif len(rows) < 2 or not len(rows[0]) or any(_tag(n) != "th" for n in rows[0]):
        raise UnsupportedProjection("table_missing_single_header")
    width = len(rows[0])
    header_ids = [] if headerless else [str(n.get("id", "")) for n in rows[0]]
    headers = [] if headerless else [_normal(n) for n in rows[0]]
    if not headerless and (any(not h for h in headers) or len(set(headers)) != width):
        raise UnsupportedProjection("table_ambiguous_header")
    for row_index, row in enumerate(rows):
        if len(row) != width:
            raise UnsupportedProjection("table_nonrectangular")
        for col, cell in enumerate(row):
            if _tag(cell) not in {"td", "th"}:
                raise UnsupportedProjection("table_cell_element")
            if cell.get("rowspan", "1") != "1" or cell.get("colspan", "1") != "1":
                raise UnsupportedProjection("table_merged_cells")
            expected_scope = (
                ""
                if headerless
                else ("col" if row_index == 0 else "row" if _tag(cell) == "th" else "")
            )
            if cell.get("scope", expected_scope) != expected_scope:
                raise UnsupportedProjection("table_header_scope")
            if cell.get("headers") and (
                headerless or str(cell.get("headers")).split() != [header_ids[col]]
            ):
                raise UnsupportedProjection("table_header_association")
    lines = []
    for i, row in enumerate(rows if headerless else rows[1:], 1):
        if headerless:
            cells = [_normal(c) for c in row]
            if any(not c for c in cells):
                raise UnsupportedProjection("table_headerless_empty_cell")
            lines.append(f"Row {i}: " + json.dumps(cells, ensure_ascii=False) + "\n")
            continue
        if all(_tag(c) == "th" for c in row):
            raise UnsupportedProjection("table_multiple_header_rows")
        lines.append(
            f"Row {i}: "
            + "; ".join(
                f"{h}: {json.dumps(_normal(c), ensure_ascii=False)}"
                for h, c in zip(headers, row, strict=True)
            )
            + "\n"
        )
    if any(
        marker in after
        for marker in ("[TableXML]", "[DefinitionXML]", "[ListXML]", "[MathML]")
    ):
        raise UnsupportedProjection("table_structured_note")
    context = "Table: " + " | ".join(captions) + "\n" if captions else ""
    if headerless:
        context += (
            "Table layout: two columns in source order; no declared column headers.\n"
        )
    if after.strip():
        context += "Table notes: " + after.strip() + "\n"
    # Footnote links outside this owned unit cannot be silently treated as explained.
    owned_ids = {str(n.get("id")) for n in table.iter() if n.get("id")}
    for link in table.iter("a"):
        href = str(link.get("href", ""))
        if (
            (
                any(
                    _tag(n) == "sup"
                    for n in [link, *link.iterancestors(), *link.iterdescendants()]
                )
                or "fn" in str(link.get("class", "")).split()
            )
            and href.startswith("#")
            and href[1:] not in owned_ids
        ):
            raise UnsupportedProjection("table_unresolved_footnote")
    ends = []
    length = 0
    for line in lines:
        length += len(line)
        ends.append(length)
    return "".join(lines), context, tuple(ends)


def _replace_math(body: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return "[Formula: " + mathml_projection(_parse(match.group(1))) + "]"

    value = re.sub(r"\[MathML\](.*?)\[/MathML\]", replace, body, flags=re.DOTALL)
    if "[MathML]" in value or re.search(r"<(?:\w+:)?math(?:\s|>)", value):
        raise UnsupportedProjection("math_outside_supported_wrapper")
    return value


def project_evidence_unit(
    unit: EvidenceUnit, splitter: TokenBoundChildren, *, allow_headerless: bool = False
) -> EvidenceUnit:
    """显式候选profile的投影；任何不支持或token溢出都保留原单元并记录原因。

    Note:
        不改变默认text-only构造路径。ID绑定投影版本和内容；不能重复投影已处理单元。
    """
    if unit.projection_version != "text-only-v1":
        raise ValueError("unit already has a structured projection")
    if unit.evidence.projection_status == "text" or unit.unit_type == "excluded":
        return unit
    reason = ""
    version = PROJECTION_VERSION
    evidence = unit.evidence
    try:
        if unit.unit_type == "table":
            try:
                text, context, ends = table_projection(evidence.body)
            except UnsupportedProjection as exc:
                if not allow_headerless or str(exc) != "table_missing_single_header":
                    raise
                version = HEADERLESS_PAIR_VERSION
                text, context, ends = table_projection(
                    evidence.body, allow_headerless=True
                )
        elif evidence.projection_status == "deferred_math":
            value = _replace_math(evidence.body)
            # Nested XML wrappers need their own structure-aware renderer, not a flattening shortcut.
            if any(m in value for m in ("[DefinitionXML]", "[ListXML]", "[TableXML]")):
                raise UnsupportedProjection("math_nested_structure")
            text, status = retrieval_projection(value, "evidence_unit")
            if status != "text":
                raise UnsupportedProjection("math_remaining_structure")
            context, ends = "", ()
        else:
            raise UnsupportedProjection("structure_not_in_profile")
        candidate = evidence.model_copy(
            update={
                "search_text": text,
                "search_context": context,
                "atomic_search_ends": ends,
                "projection_status": "text",
            }
        )
        splitter.split(candidate)
        evidence = candidate
    except UnsupportedProjection as exc:
        reason = str(exc)
    except ValueError as exc:
        if "token budget" not in str(exc):
            raise
        reason = "token_budget:" + str(exc)
    identity = json.dumps(
        [
            unit.evidence.parent_id,
            version,
            evidence.search_text,
            evidence.search_context,
            evidence.atomic_search_ends,
            reason,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    evidence = evidence.model_copy(
        update={"parent_id": hashlib.sha256(identity.encode()).hexdigest()}
    )
    return unit.model_copy(
        update={
            "evidence": evidence,
            "projection_version": version,
            "projection_reason": reason,
        }
    )
