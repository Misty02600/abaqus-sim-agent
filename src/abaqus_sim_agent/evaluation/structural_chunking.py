"""结构保真切分原型；不替换产品解析器或写入现有索引。"""

from collections.abc import Sequence
from dataclasses import dataclass

import lxml.etree as etree

from abaqus_sim_agent.retrieval.documents import _content_roots
from abaqus_sim_agent.retrieval.types import Document

STRUCTURAL_PARSER_VERSION = "dom-block-preservation-v2"


@dataclass(frozen=True)
class SourceBlock:
    kind: str
    text: str
    xpath: str
    anchor: str
    heading_path: tuple[str, ...]


def _tag(node: etree._Element) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def _normal(text: str) -> str:
    return " ".join(text.split())


def _anchor(node: etree._Element, fallback: str) -> str:
    for owner in [node, *node.iterancestors()]:
        if owner.get("id"):
            return str(owner.get("id"))
    return fallback


def _inline(node: etree._Element) -> str:
    if not _tag(node):
        return ""
    if _tag(node) == "math":
        return (
            "[MathML]"
            + etree.tostring(node, encoding="unicode", with_tail=False)
            + "[/MathML]"
        )
    if _tag(node) in {"script", "style"}:
        return ""
    if _tag(node) == "br":
        return "\n"
    parts = [node.text or ""]
    for child in node:
        parts.extend((_inline(child), child.tail or ""))
    return "".join(parts)


def _render(node: etree._Element) -> str:
    tag = _tag(node)
    if tag == "pre":
        return "[Code]\n" + "".join(node.itertext()).strip("\r\n") + "\n[/Code]"
    if tag == "table":
        # Preserve spans, headers and formulas instead of pretending a flattened row is equivalent.
        return (
            "[TableXML]\n"
            + etree.tostring(node, encoding="unicode", with_tail=False)
            + "\n[/TableXML]"
        )
    if tag in {"ul", "ol"}:
        if (
            node.attrib
            or node.xpath(".//ul|.//ol|.//pre|.//table|.//math")
            or any(child.attrib for child in node)
        ):
            return (
                "[ListXML]\n"
                + etree.tostring(node, encoding="unicode", with_tail=False)
                + "\n[/ListXML]"
            )
        return "\n".join(
            f"{index}. {_normal(_inline(item))}"
            if tag == "ol"
            else f"- {_normal(_inline(item))}"
            for index, item in enumerate(node, 1)
            if _tag(item) == "li"
        )
    if tag == "dd" and any(
        _tag(child) in {"p", "ul", "ol", "pre", "table", "dl", "div"} for child in node
    ):
        return (
            "[DefinitionXML]\n"
            + etree.tostring(node, encoding="unicode", with_tail=False)
            + "\n[/DefinitionXML]"
        )
    return _normal(_inline(node))


def prototype_content_roots(tree: etree._ElementTree) -> list[etree._Element]:
    roots = _content_roots(tree)
    abstracts = [
        node
        for node in tree.xpath("//p")
        if "abstract" in str(node.get("class", "")).split()
    ]
    combined = set(roots + abstracts)
    return [
        node
        for node in tree.iter()
        if node in combined
        and not any(parent in combined for parent in node.iterancestors())
    ]


def extract_source_blocks(
    tree: etree._ElementTree, *, title: str, document_id: str
) -> list[SourceBlock]:
    """按DOM顺序读取完整内容块，标题只作上下文，不单独产出正文。

    Note:
        使用产品内容根并补入未覆盖的abstract段落，仍不能声称整页正文无遗漏。公式保留MathML，
        表格保留XML结构，不声称这种序列化适合当前向量模型。源文件不修改。
    """
    result: list[SourceBlock] = []
    headings: dict[int, str] = {}
    atomic = {"p", "pre", "table", "ul", "ol", "math"}

    def structural_container(node: etree._Element) -> bool:
        return any(
            _tag(child)
            in (atomic - {"math"})
            | {"dl", "div", "article", "section", "h1", "h2", "h3", "h4", "h5", "h6"}
            for child in node.iterdescendants()
        )

    def path() -> tuple[str, ...]:
        values = [title]
        for level in sorted(headings):
            if headings[level] != values[-1]:
                values.append(headings[level])
        return tuple(values)

    def emit(node: etree._Element, kind: str, text: str) -> None:
        if text.strip():
            result.append(
                SourceBlock(
                    kind, text, tree.getpath(node), _anchor(node, document_id), path()
                )
            )

    def walk(node: etree._Element) -> None:
        tag = _tag(node)
        if tag in {"script", "style", "nav"} or not tag:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            for old in list(headings):
                if old >= level:
                    del headings[old]
            headings[level] = _normal("".join(node.itertext()))
            return
        if tag == "dl":
            pending: list[etree._Element] = []
            for child in node:
                if _tag(child) == "dt":
                    pending.append(child)
                elif _tag(child) == "dd":
                    label = "\n".join(_render(term) for term in pending)
                    emit(child, "definition", label + "\n" + _render(child))
                    pending.clear()
                else:
                    walk(child)
            for term in pending:
                emit(term, "definition_without_description", _render(term))
            return
        # Some vendor <p> containers wrap further block elements. Do not flatten those wrappers.
        has_blocks = any(
            _tag(child) in (atomic - {"math"}) | {"dl", "div", "article", "section"}
            or structural_container(child)
            for child in node
        )
        if tag in atomic and not (tag == "p" and has_blocks):
            emit(node, tag, _render(node))
            return
        saved_headings = dict(headings)
        buffer = [node.text or ""]
        for child in node:
            child_tag = _tag(child)
            if (
                child_tag
                in atomic
                | {
                    "dl",
                    "div",
                    "article",
                    "section",
                }
                or child_tag in {"h1", "h2", "h3", "h4", "h5", "h6"}
                or structural_container(child)
            ):
                emit(node, "text", _normal("".join(buffer)))
                buffer = []
                walk(child)
            else:
                buffer.append(_inline(child))
            buffer.append(child.tail or "")
        emit(node, "text", _normal("".join(buffer)))
        if tag in {"article", "section"}:
            headings.clear()
            headings.update(saved_headings)

    for root in prototype_content_roots(tree):
        headings.clear()
        walk(root)
    return result


def chunk_source_blocks(
    blocks: Sequence[SourceBlock], *, document_id: str, soft_limit: int = 1000
) -> list[Document]:
    """按同一来源上下文组合完整块；超过软上限的单块不硬切，显式标记。

    Args:
        blocks: DOM顺序的完整块。
        document_id: 源页面稳定ID。
        soft_limit: 组块字符目标，不是硬上限或embedding token预算。

    Returns:
        无重复overlap的正文片段，带原块路径和同页前后片段关联。

    Raises:
        ValueError: 软上限非正。

    Note:
        不保证跨段的规则与例外一定在同一块。块间链接仅保存关联，尚无检索时
        自动展开；过长块需要后续专门策略，不能直接送入短上下文embedding。
    """
    if soft_limit < 1:
        raise ValueError("soft_limit must be positive")
    groups: list[list[SourceBlock]] = []
    for block in blocks:
        previous = groups[-1] if groups else []
        same_context = (
            previous
            and previous[-1].heading_path == block.heading_path
            and previous[-1].anchor == block.anchor
        )
        fits = (
            sum(len(item.text) for item in previous)
            + 2 * len(previous)
            + len(block.text)
            <= soft_limit
        )
        if same_context and fits:
            previous.append(block)
        else:
            groups.append([block])
    result = []
    for position, group in enumerate(groups):
        text = "\n\n".join(block.text for block in group)
        result.append(
            Document(
                id=f"{document_id}#structural-{position + 1:04d}",
                page_content=text,
                metadata={
                    "document_id": document_id,
                    "section_id": group[0].anchor,
                    "heading_path": list(group[0].heading_path),
                    "source_block_xpaths": [block.xpath for block in group],
                    "block_kinds": [block.kind for block in group],
                    "oversized_block": len(text) > soft_limit,
                    "previous_chunk_id": f"{document_id}#structural-{position:04d}"
                    if position
                    else "",
                    "next_chunk_id": f"{document_id}#structural-{position + 2:04d}"
                    if position + 1 < len(groups)
                    else "",
                    "prototype_version": STRUCTURAL_PARSER_VERSION,
                },
            )
        )
    return result
