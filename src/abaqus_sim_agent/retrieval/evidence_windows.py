"""DOM来源筛选和有界阅读窗口；不改变查询或重排分数。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Literal

import lxml.etree as etree
from langchain_core.documents import Document

from .evidence_blocks import EvidenceBlock, expand_ranked_children


def _tag(node: etree._Element | None) -> str:
    return (
        etree.QName(node).localname
        if node is not None and isinstance(node.tag, str)
        else ""
    )


def _classes(node: etree._Element) -> set[str]:
    return set(str(node.get("class", "")).split())


def _toc_list(node: etree._Element | None) -> bool:
    if _tag(node) not in {"ul", "ol"} or node is None:
        return False
    if not any("abstract" in _classes(p) for p in node.iterancestors()):
        return False
    items = [p for p in node if isinstance(p.tag, str)]
    if len(items) < 2 or any(_tag(p) != "li" for p in items):
        return False
    targets = {
        str(p.get("id"))
        for p in node.getroottree().xpath("//*[@id]")
        if p.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//h6")
    }
    for item in items:
        links = item.xpath(".//a[@href]")
        if len(links) != 1:
            return False
        link = links[0]
        href = str(link.get("href"))
        if not href.startswith("#") or href[1:] not in targets:
            return False
        if " ".join("".join(item.itertext()).split()) != " ".join(
            "".join(link.itertext()).split()
        ):
            return False
    return True


def source_role(
    node: etree._Element,
) -> Literal["content", "metadata", "navigation", "equation_context"]:
    """只根据明确DOM结构分类，不把短文本或特定单词作为排除依据。

    Note:
        正文区keygroup作为元信息；abstract中的同页章节链接列表及其冒号标签
        作为导航；夹在公式/变量定义之间的纯文本段落只作公式上下文。
        该分类不是领域相关性或物理适用性裁决，原文仍须保存。
    """
    owners = [node, *node.iterancestors()]
    if any("keygroup" in _classes(p) for p in owners):
        return "metadata"
    if any(_tag(p) == "nav" or p.get("role") == "navigation" for p in owners):
        return "navigation"
    if any(_toc_list(p) for p in owners):
        return "navigation"
    if _tag(node) == "p" and len(node) == 0:
        if (node.text or "").rstrip().endswith(":") and _toc_list(node.getnext()):
            return "navigation"
        previous, following = node.getprevious(), node.getnext()
        previous_equation = previous is not None and "equation" in _classes(previous)
        next_equation = following is not None and "equation" in _classes(following)
        next_variables = (
            _tag(following) == "dl"
            and following is not None
            and "variablelist" in _classes(following)
        )
        if previous_equation and (next_equation or next_variables):
            return "equation_context"
    return "content"


def reading_windows(
    parents: Mapping[str, EvidenceBlock],
    *,
    max_blocks: int = 4,
    max_characters: int = 3000,
) -> list[tuple[str, ...]]:
    """仅合并相邻、同范围的普通正文；参数/列表/代码等保留原子边界。

    Note:
        字符数限制只约束新增相邻上下文，不截短本身超长的原子块。正文窗口
        不跨页、版本、源文件hash、anchor或heading_path，不跳过中间结构。
    """
    if max_blocks < 1 or max_characters < 1:
        raise ValueError("window limits must be positive")
    groups: list[list[EvidenceBlock]] = []
    for parent in parents.values():
        previous = groups[-1] if groups else []
        eligible = (
            parent.source_role == "content"
            and parent.projection_status == "text"
            and parent.kind in {"p", "text"}
        )
        same_scope = bool(previous) and all(
            (p.document_id, p.release, p.source_sha256, p.anchor, p.heading_path)
            == (
                parent.document_id,
                parent.release,
                parent.source_sha256,
                parent.anchor,
                parent.heading_path,
            )
            and p.source_role == "content"
            and p.projection_status == "text"
            and p.kind in {"p", "text"}
            for p in previous
        )
        size = sum(len(p.body) for p in previous) + 2 * len(previous) + len(parent.body)
        if (
            eligible
            and same_scope
            and len(previous) < max_blocks
            and size <= max_characters
        ):
            previous.append(parent)
        else:
            groups.append([parent])
    return [tuple(p.parent_id for p in group) for group in groups]


def expand_reading_windows(
    children: Sequence[Document],
    parents: Mapping[str, EvidenceBlock],
    *,
    top_k: int = 4,
    max_blocks: int = 4,
    max_characters: int = 3000,
) -> list[Document]:
    """固定原Top-K父块选择后补正文窗口；合并后不从低排名补回额外窗口。"""
    selected = expand_ranked_children(children, parents, top_k=top_k)
    groups = reading_windows(
        parents, max_blocks=max_blocks, max_characters=max_characters
    )
    membership = {key: group for group in groups for key in group}
    result = []
    seen: set[tuple[str, ...]] = set()
    for hit in selected:
        group = membership[str(hit.id)]
        if group in seen:
            continue
        seen.add(group)
        metadata = dict(hit.metadata)
        metadata.update(
            {
                "entity_kind": "source_window",
                "reading_window_version": "bounded-source-window-v1",
                "member_parent_ids": list(group),
                "source_block_xpaths": [parents[key].xpath for key in group],
                "window_max_blocks": max_blocks,
                "window_max_characters": max_characters,
                "oversized_atomic_block": len(group) == 1
                and len(parents[group[0]].body) > max_characters,
            }
        )
        result.append(
            Document(
                id=hashlib.sha256(json.dumps(group).encode()).hexdigest(),
                page_content="\n\n".join(parents[key].body for key in group),
                metadata=metadata,
            )
        )
    return result
