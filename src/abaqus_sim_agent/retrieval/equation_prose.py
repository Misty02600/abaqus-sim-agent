"""按DOM拆出公式组内的独立说明段；保留公式、变量定义与结构关联。"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import lxml.etree as etree

from .evidence_blocks import EvidenceBlock
from .evidence_units import EvidenceUnit, build_evidence_units

PARTITION_VERSION = "equation-prose-partition-v1"


def _tag(node: etree._Element | None) -> str:
    return (
        etree.QName(node).localname
        if node is not None and isinstance(node.tag, str)
        else ""
    )


def _classes(node: etree._Element) -> set[str]:
    return set(str(node.get("class", "")).split())


def _display_equation(node: etree._Element) -> bool:
    owners = [node, *node.iterancestors()]
    return any("equation" in _classes(n) for n in owners) or any(
        _tag(n) == "math" and n.get("display") == "block"
        for n in [node, *node.iterdescendants()]
    )


def _outside_math_text(node: etree._Element) -> str:
    if not _tag(node) or _tag(node) in {"math", "script", "style"}:
        return ""
    return (node.text or "") + "".join(
        _outside_math_text(c) + (c.tail or "") for c in node
    )


def _explanation(block: EvidenceBlock, node: etree._Element) -> bool:
    if _tag(node) != "p" or block.source_role != "content" or _display_equation(node):
        return False
    ancestors = []
    for ancestor in node.iterancestors():
        if _tag(ancestor) in {"article", "body"}:
            break
        ancestors.append(ancestor)
    # 文档article外的站点布局table不是正文数据表；只检查当前内容范围内的容器。
    if any(
        _tag(n) in {"dl", "dd", "dt", "table", "ul", "ol", "li", "pre"}
        for n in ancestors
    ):
        return False
    if any(_tag(n) == "figure" and "usage" in _classes(n) for n in ancestors):
        return False
    following = node.getnext()
    if following is not None and (
        _display_equation(following)
        or (_tag(following) == "dl" and "variablelist" in _classes(following))
    ):
        return False
    return any(character.isalpha() for character in _outside_math_text(node))


@dataclass(frozen=True)
class EquationContextRelation:
    explanation_id: str
    context_ids: tuple[str, ...]
    source_group_id: str


@dataclass(frozen=True)
class EquationProsePartition:
    units: tuple[EvidenceUnit, ...]
    relations: tuple[EquationContextRelation, ...]


def _unit(
    source_group: EvidenceUnit,
    members: Sequence[EvidenceBlock],
    explanation: bool,
    limit: int,
) -> EvidenceUnit:
    first = members[0]
    body = "\n\n".join(member.body for member in members)
    ids = tuple(member.parent_id for member in members)
    kind = "prose" if explanation else "formula"
    identifier = hashlib.sha256(
        json.dumps(
            [PARTITION_VERSION, source_group.evidence.parent_id, kind, ids],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    status = "deferred_math" if not explanation else first.projection_status
    evidence = first.model_copy(
        update={
            "parent_id": identifier,
            "kind": "evidence_unit",
            "body": body,
            "search_text": "\n\n".join(m.search_text for m in members)
            if status == "text"
            else "",
            "search_context": "",
            "atomic_search_ends": (),
            "projection_status": status,
            "source_role": "content",
        }
    )
    return EvidenceUnit(
        evidence=evidence,
        unit_type=kind,
        construction_rule=PARTITION_VERSION
        + ":"
        + source_group.evidence.parent_id
        + (":explanation" if explanation else ":equation_context"),
        member_ids=ids,
        member_xpaths=tuple(m.xpath for m in members),
        member_anchors=tuple(m.anchor for m in members),
        oversized=len(body) > limit,
    )


def build_equation_prose_units(
    blocks: Sequence[EvidenceBlock],
    nodes: Mapping[str, etree._Element],
    *,
    max_prose_blocks: int = 4,
    max_prose_characters: int = 3000,
    eligible_group_ids: frozenset[str] | None = None,
) -> EquationProsePartition:
    """显式替代组块模式；完整原子块恰好分配一次，未涉及的单元完全不变。

    Args:
        blocks: 完整、按源顺序排列的原文块注册表。
        nodes: 与注册表逐项对应、已核对源hash的DOM节点。
        max_prose_blocks: 传给原有普通正文组块器的上限。
        max_prose_characters: 原有正文软上限，不截断单个原子块。
        eligible_group_ids: 可选的旧组ID集合；候选构建器应只传入原投影失败的组，
            从而保留所有原来可检索单元的ID和正文。None不额外限制。

    Note:
        只从含显式display equation的旧formula组中拆出其后的普通p；变量定义、
        列表/表格、纯数学块、公式/变量表引导段和usage容器不拆。内联MathML
        原样保留，后续仍需通过原有投影器，未知数学不自动变为可检索文本。
        relations只表示同一旧结构组，不证明代词或物理意义已独立完备；调用方
        在投影改变ID后须映射这些关系并在交付时保留上下文提示。
    """
    original = build_evidence_units(
        blocks,
        nodes,
        max_prose_blocks=max_prose_blocks,
        max_prose_characters=max_prose_characters,
    )
    by_id = {b.parent_id: b for b in blocks}
    result, relations = [], []
    for source_group in original:
        if source_group.unit_type != "formula" or (
            eligible_group_ids is not None
            and source_group.evidence.parent_id not in eligible_group_ids
        ):
            result.append(source_group)
            continue
        members = [by_id[key] for key in source_group.member_ids]
        seen_equation = False
        labels = []
        for block in members:
            node = nodes[block.parent_id]
            labels.append(seen_equation and _explanation(block, node))
            seen_equation = seen_equation or _display_equation(node)
        if not any(labels):
            result.append(source_group)
            continue
        groups: list[tuple[bool, list[EvidenceBlock]]] = []
        for block, explanation in zip(members, labels, strict=True):
            if not explanation and groups and not groups[-1][0]:
                groups[-1][1].append(block)
            else:
                groups.append((explanation, [block]))
        replacements = [
            _unit(source_group, group, explanation, max_prose_characters)
            for explanation, group in groups
        ]
        if (
            "\n\n".join(u.evidence.body for u in replacements)
            != source_group.evidence.body
        ):
            raise ValueError("equation partition changed source body")
        context_ids = tuple(
            u.evidence.parent_id for u in replacements if u.unit_type == "formula"
        )
        if not context_ids:
            raise ValueError("explanation lost its equation context")
        for unit in replacements:
            if unit.unit_type == "prose":
                relations.append(
                    EquationContextRelation(
                        unit.evidence.parent_id,
                        context_ids,
                        source_group.evidence.parent_id,
                    )
                )
        result.extend(replacements)
    if [key for unit in result for key in unit.member_ids] != [
        b.parent_id for b in blocks
    ]:
        raise ValueError("partition must preserve every source block exactly once")
    return EquationProsePartition(tuple(result), tuple(relations))
