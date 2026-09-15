"""只读取官方对象用途与当前方法表格；原XML和有层次的文本分别保留。"""

from copy import deepcopy
from typing import Literal

import lxml.etree as etree

from .models import ApiReferenceText, ApiSourceRef


def _tag(node: etree._Element) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def _inline(node: etree._Element, warnings: set[str]) -> str:
    tag = _tag(node)
    if tag in {"msub", "msup", "msubsup", "mfrac"}:
        children = [_inline(c, warnings) for c in node]
        if len(children) == (3 if tag == "msubsup" else 2):
            if tag == "mfrac":
                return f"({children[0]})/({children[1]})"
            suffix = "_" if tag in {"msub", "msubsup"} else "^"
            value = children[0] + suffix + "{" + children[1] + "}"
            return value + ("^{" + children[2] + "}" if tag == "msubsup" else "")
    if tag == "math" and any(
        _tag(c)
        not in {
            "math",
            "mi",
            "mn",
            "mo",
            "mrow",
            "msub",
            "msup",
            "msubsup",
            "mfrac",
            "mtext",
        }
        for c in node.iter()
    ):
        warnings.add("Complex MathML retained as XML; do not infer missing notation.")
        return etree.tostring(node, encoding="unicode", with_tail=False)
    if tag == "img":
        warnings.add(
            "Image content requires the source; alt text is not a formula verification."
        )
        return node.get("alt", "[image: see source]")
    return (node.text or "") + "".join(
        _inline(c, warnings) + (c.tail or "") for c in node
    )


def _blocks(node: etree._Element, warnings: set[str], depth: int = 0) -> list[str]:
    tag = _tag(node)
    if tag in {"h1", "h2", "h3"}:
        return []
    if tag in {"ul", "ol"}:
        return [line for child in node for line in _blocks(child, warnings, depth + 1)]
    nested = any(_tag(c) in {"p", "ul", "ol", "div", "section"} for c in node)
    if not nested:
        text = " ".join(_inline(node, warnings).split())
        return (
            [("  " * max(depth - 1, 0) + "- " if tag == "li" else "") + text]
            if text
            else []
        )
    lines = []
    prefix = "  " * max(depth - 1, 0) + "- " if tag == "li" else ""
    if node.text and node.text.strip():
        lines.append(prefix + " ".join(node.text.split()))
        prefix = ""
    for child in node:
        block = _blocks(child, warnings, depth)
        if block and prefix:
            block[0] = prefix + block[0]
            prefix = ""
        lines.extend(block)
    return lines


def page_description(tree: etree._ElementTree) -> etree._Element | None:
    """复制页级用途段，排除当前英文文档嵌入其中的目录。

    Note:
        不修改输入树，不把方法article内的摘要当作对象用途；没有匹配段落时返回None。
    """
    for node in tree.iter():
        if "abstract" not in (node.get("class") or "").split():
            continue
        if any(_tag(a) == "article" for a in node.iterancestors()):
            continue
        selected = deepcopy(node)
        stop = False
        for child in list(selected):
            stop = stop or " ".join("".join(child.itertext()).split()).startswith(
                "This page discusses:"
            )
            stop = stop or any(c.get("id") == "toc_rg" for c in child.iter())
            if stop:
                selected.remove(child)
        return selected
    return None


def reference_text(
    node: etree._Element,
    *,
    kind: Literal["object_description", "table_data"],
    source: ApiSourceRef,
    source_sha256: str,
) -> ApiReferenceText | None:
    """保存选定片段及其有序文本，并显式提示无法简化的公式。

    Note:
        source_sha256绑定原始文件，raw_xhtml是选定片段的XML序列化，不是原文件字节。
        不支持的MathML保留XML且添加警告；空片段返回None，不补造说明。
    """
    warnings: set[str] = set()
    text = "\n".join(_blocks(node, warnings)).strip()
    if not text:
        return None
    if "\ufffd" in text:
        warnings.add(
            "Source contains Unicode replacement characters; consult source, do not repair by guessing."
        )
    return ApiReferenceText(
        kind=kind,
        text=text,
        raw_xhtml=etree.tostring(node, encoding="unicode", with_tail=False),
        source=source,
        source_sha256=source_sha256,
        text_warnings=tuple(sorted(warnings)),
    )
