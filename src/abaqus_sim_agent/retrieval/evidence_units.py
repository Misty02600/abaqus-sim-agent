"""入库时构造可追溯的父证据单元；不把结构规则当作语义完整性的保证。"""

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

import lxml.etree as etree
from pydantic import BaseModel, ConfigDict, model_validator

from .evidence_blocks import EvidenceBlock

UNIT_VERSION = "structural-evidence-units-v1"


class EvidenceUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    evidence: EvidenceBlock
    unit_type: Literal[
        "prose", "definition", "list", "code", "table", "formula", "excluded", "other"
    ]
    construction_rule: str
    member_ids: tuple[str, ...]
    member_xpaths: tuple[str, ...]
    member_anchors: tuple[str, ...]
    oversized: bool
    projection_version: str = "text-only-v1"
    projection_reason: str = ""
    version: Literal["structural-evidence-units-v1"] = UNIT_VERSION

    @model_validator(mode="after")
    def check_members(self) -> "EvidenceUnit":
        if not self.member_ids or len(set(self.member_ids)) != len(self.member_ids):
            raise ValueError("unit members must be nonempty and unique")
        if len(self.member_ids) != len(self.member_xpaths) or len(
            self.member_ids
        ) != len(self.member_anchors):
            raise ValueError("unit source mappings must have equal lengths")
        return self


def _tag(node: etree._Element) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def _classes(node: etree._Element) -> set[str]:
    return set(str(node.get("class", "")).split())


def _scope(parent: EvidenceBlock) -> tuple[object, ...]:
    return parent.document_id, parent.release, parent.source_sha256, parent.heading_path


def _usage_owner(node: etree._Element) -> etree._Element | None:
    for owner in [node, *node.iterancestors()]:
        if (
            _tag(owner) == "figure"
            and "usage" in _classes(owner)
            and owner.xpath(".//pre")
        ):
            return owner
    return None


def _formula(parent: EvidenceBlock, node: etree._Element) -> bool:
    def variable_context(owner: etree._Element) -> bool:
        if _tag(owner) != "dl" or "variablelist" not in _classes(owner):
            return False
        previous = owner.getprevious()
        if previous is not None and _tag(previous) == "p":
            previous = previous.getprevious()
        return previous is not None and "equation" in _classes(previous)

    return (
        parent.projection_status == "deferred_math"
        or parent.source_role == "equation_context"
        or any(
            "equation" in _classes(n) or variable_context(n)
            for n in [node, *node.iterancestors()]
        )
    )


def _prose(parent: EvidenceBlock) -> bool:
    return (
        parent.source_role == "content"
        and parent.projection_status == "text"
        and parent.kind in {"p", "text"}
    )


def _lead_attachment(
    a: EvidenceBlock, b: EvidenceBlock, nodes: Mapping[str, etree._Element]
) -> bool:
    if (
        not _prose(a)
        or not a.body.rstrip().endswith(":")
        or b.source_role != "content"
        or b.kind not in {"ul", "ol", "pre"}
        or _scope(a) != _scope(b)
    ):
        return False
    left, right = nodes[a.parent_id], nodes[b.parent_id]
    return right.getprevious() is left or right.getparent() is left


def _table_note(
    table: EvidenceBlock, note: EvidenceBlock, nodes: Mapping[str, etree._Element]
) -> bool:
    if _scope(table) != _scope(note) or note.source_role != "content":
        return False
    node = nodes[note.parent_id]
    if not (
        _classes(node) & {"tablefootnote", "fn"} or node.get("role") == "doc-footnote"
    ):
        return False
    return node.getparent() is nodes[table.parent_id].getparent()


def build_evidence_units(
    blocks: Sequence[EvidenceBlock],
    nodes: Mapping[str, etree._Element],
    *,
    max_prose_blocks: int = 4,
    max_prose_characters: int = 3000,
) -> list[EvidenceUnit]:
    """从完整、源顺序的原文块构造父单元，再由调用方生成检索子片段。

    Args:
        blocks: 完整原文块注册表，不能传排序结果或删去中间块的子集。
        nodes: 经源文件hash核对并按块XPath解析的DOM节点。
        max_prose_blocks: 普通正文单元的组块上限，非语义完整性标准。
        max_prose_characters: 普通正文组合的软限制；不截断完整原子块。

    Returns:
        原块恰好分配一次的持久父单元。定义不相互合并；列表/代码可带直接引导句；
        公式及变量定义按结构关联保存，但未验证的数学检索仍明确延后。

    Raises:
        ValueError: 限制无效、注册表缺失/乱序、重复块或缺少DOM节点。

    Note:
        仅显式tablefootnote/fn/doc-footnote关联可附到表格；不能保证识别所有脚注。
        同章节正文按结构组合不意味着已理解所有跨段因果关系。复杂单元不会通过
        删除未支持内容来制造可检索版本。源DOM和原文块保持不变。
    """
    if max_prose_blocks < 1 or max_prose_characters < 1:
        raise ValueError("unit limits must be positive")
    counters: dict[tuple[str, str, str], int] = defaultdict(int)
    seen = set()
    for block in blocks:
        key = block.document_id, block.release, block.source_sha256
        identity = json.dumps(
            [
                block.document_id,
                block.release,
                block.source_sha256,
                block.xpath,
                counters[key],
                block.kind,
                block.body,
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if block.parent_id != hashlib.sha256(identity.encode()).hexdigest():
            raise ValueError("source registry is incomplete, reordered or inconsistent")
        if block.parent_id in seen or block.parent_id not in nodes:
            raise ValueError("duplicate block or missing source node")
        node = nodes[block.parent_id]
        if node.getroottree().getpath(node) != block.xpath:
            raise ValueError("source node XPath does not match block")
        seen.add(block.parent_id)
        counters[key] += 1
    if seen != set(nodes):
        raise ValueError("source registry and node inventory differ")
    result: list[EvidenceUnit] = []
    position = 0
    while position < len(blocks):
        first = blocks[position]
        group = [first]
        kind: Literal[
            "prose",
            "definition",
            "list",
            "code",
            "table",
            "formula",
            "excluded",
            "other",
        ]
        if first.source_role in {"metadata", "navigation"}:
            kind, rule = "excluded", "source_role"
        elif (usage := _usage_owner(nodes[first.parent_id])) is not None:
            kind, rule = "code", "explicit_usage_container"
            while position + len(group) < len(blocks):
                following = blocks[position + len(group)]
                if (
                    _scope(first) != _scope(following)
                    or _usage_owner(nodes[following.parent_id]) is not usage
                ):
                    break
                group.append(following)
        elif _formula(first, nodes[first.parent_id]):
            kind, rule = "formula", "equation_and_variable_context"
            while position + len(group) < len(blocks):
                following = blocks[position + len(group)]
                if _scope(first) != _scope(following) or not _formula(
                    following, nodes[following.parent_id]
                ):
                    break
                group.append(following)
        elif position + 1 < len(blocks) and _lead_attachment(
            first, blocks[position + 1], nodes
        ):
            group.append(blocks[position + 1])
            kind = "code" if group[1].kind == "pre" else "list"
            rule = "direct_lead_and_structure"
        elif first.kind == "definition":
            kind, rule = "definition", "atomic_named_definition"
        elif first.kind in {"ul", "ol"}:
            kind, rule = "list", "atomic_list_no_detected_lead"
        elif first.kind == "pre":
            kind, rule = "code", "atomic_code_no_detected_lead"
        elif first.kind == "table":
            kind, rule = "table", "table_and_explicit_notes"
            while position + len(group) < len(blocks) and _table_note(
                first, blocks[position + len(group)], nodes
            ):
                group.append(blocks[position + len(group)])
        elif _prose(first):
            kind, rule = "prose", "contiguous_same_scope_prose"
            while position + len(group) < len(blocks):
                offset = position + len(group)
                following = blocks[offset]
                if (
                    not _prose(following)
                    or _scope(first) != _scope(following)
                    or first.anchor != following.anchor
                ):
                    break
                if offset + 1 < len(blocks) and _lead_attachment(
                    following, blocks[offset + 1], nodes
                ):
                    break
                length = (
                    sum(len(p.body) for p in group)
                    + 2 * len(group)
                    + len(following.body)
                )
                if len(group) >= max_prose_blocks or length > max_prose_characters:
                    break
                group.append(following)
        else:
            kind, rule = "other", "atomic_unmodeled_structure"
        body = "\n\n".join(p.body for p in group)
        status: Literal["text", "deferred_math", "deferred_table", "deferred_list"] = (
            "text"
        )
        if kind == "formula":
            status = "deferred_math"
        else:
            for member in group:
                if member.projection_status != "text":
                    status = member.projection_status
                    break
        search_text = (
            "\n\n".join(p.search_text for p in group) if status == "text" else ""
        )
        members = tuple(p.parent_id for p in group)
        unit_id = hashlib.sha256(
            json.dumps([UNIT_VERSION, kind, members], separators=(",", ":")).encode()
        ).hexdigest()
        evidence = EvidenceBlock(
            parent_id=unit_id,
            document_id=first.document_id,
            title=first.title,
            release=first.release,
            heading_path=first.heading_path,
            source=first.source,
            source_sha256=first.source_sha256,
            xpath=first.xpath,
            anchor=first.anchor,
            kind="definition" if kind == "definition" else "evidence_unit",
            body=body,
            search_text=search_text,
            projection_status=status,
            source_role=first.source_role if kind == "excluded" else "content",
        )
        result.append(
            EvidenceUnit(
                evidence=evidence,
                unit_type=kind,
                construction_rule=rule,
                member_ids=members,
                member_xpaths=tuple(p.xpath for p in group),
                member_anchors=tuple(p.anchor for p in group),
                oversized=len(body) > max_prose_characters,
            )
        )
        position += len(group)
    if [key for unit in result for key in unit.member_ids] != [
        p.parent_id for p in blocks
    ]:
        raise ValueError(
            "unit membership does not preserve the complete source sequence"
        )
    return result


def write_unit_store(path: Path, units: Sequence[EvidenceUnit]) -> str:
    if len({unit.evidence.parent_id for unit in units}) != len(units):
        raise ValueError("duplicate unit IDs")
    payload = "".join(unit.model_dump_json() + "\n" for unit in units).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(payload)
    return hashlib.sha256(payload).hexdigest()


def read_unit_store(path: Path, *, expected_sha256: str) -> dict[str, EvidenceUnit]:
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("unit store hash mismatch")
    units = [
        EvidenceUnit.model_validate_json(line) for line in payload.splitlines() if line
    ]
    if len({unit.evidence.parent_id for unit in units}) != len(units):
        raise ValueError("duplicate unit IDs")
    return {unit.evidence.parent_id: unit for unit in units}
