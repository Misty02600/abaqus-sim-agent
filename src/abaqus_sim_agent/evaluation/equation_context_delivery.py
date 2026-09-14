"""候选索引的结构关联校验与交付；不改变检索、重排或产品默认路径。"""

import copy
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from abaqus_sim_agent.retrieval.equation_prose import (
    PARTITION_VERSION,
    EquationContextRelation,
)
from abaqus_sim_agent.retrieval.evidence_units import EvidenceUnit


def context_references(
    relations: Sequence[EquationContextRelation], units: Mapping[str, EvidenceUnit]
) -> dict[str, list[dict[str, Any]]]:
    result = {}
    for relation in relations:
        key = relation.explanation_id
        if key in result or key not in units or not relation.context_ids:
            raise ValueError("duplicate or missing explanation/context")
        explanation = units[key]
        rule = PARTITION_VERSION + ":" + relation.source_group_id
        if (
            explanation.unit_type != "prose"
            or explanation.construction_rule != rule + ":explanation"
        ):
            raise ValueError("relation source is not a partitioned explanation")
        if len(set(relation.context_ids)) != len(relation.context_ids):
            raise ValueError("duplicate equation context")
        refs = []
        for target in relation.context_ids:
            if target not in units:
                raise ValueError("missing context target")
            context = units[target]
            if (
                context.unit_type != "formula"
                or context.construction_rule != rule + ":equation_context"
                or context.evidence.document_id != explanation.evidence.document_id
                or context.evidence.source_sha256 != explanation.evidence.source_sha256
                or context.evidence.release != explanation.evidence.release
                or context.evidence.heading_path != explanation.evidence.heading_path
                or set(context.member_ids) & set(explanation.member_ids)
            ):
                raise ValueError("context ownership mismatch")
            refs.append(
                {
                    "id": target,
                    "source": context.evidence.source,
                    "member_xpaths": list(context.member_xpaths),
                    "projection_status": context.evidence.projection_status,
                    "relation": "same_original_equation_group_not_semantic_coreference",
                    "body_included": False,
                }
            )
        result[key] = refs
    return result


def deliver_with_context_references(
    result: Mapping[str, Any],
    references: Mapping[str, list[dict[str, Any]]],
    *,
    count: Callable[[str], int],
    context_tokens: int,
) -> dict[str, Any]:
    """在既有最多Top4完整单元上补来源关联，并重新核算全部序列化证据预算。

    Note:
        只标记结构关联而不附入公式正文，也不声称说明段物理意义独立完备。
        关联元数据超出同一预算则丢弃整个单元并记录，不截断、不追加召回或重排。
        此保守后处理可能少交付单元，不从第五个候选补位。
    """
    if context_tokens < 1:
        raise ValueError("context token budget must be positive")
    output = copy.deepcopy(dict(result))
    if "context_tokens" not in result:
        return output
    selected = []
    for item in output["evidence"]:
        refs = references.get(item["id"])
        if refs:
            item["body_scope"] = (
                "complete_source_paragraph_with_equation_context_reference"
            )
            item["equation_context"] = copy.deepcopy(refs)
        payload = json.dumps(
            [*selected, item], ensure_ascii=False, separators=(",", ":")
        )
        if count(payload) > context_tokens:
            output["trace"]["rejected"].append(
                {
                    "id": item["id"],
                    "reason": "serialized_context_budget_including_equation_references",
                }
            )
        else:
            selected.append(item)
    output["evidence"] = selected
    output["status"] = "found" if selected else "not_found"
    output["context_tokens"] = count(
        json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
    )
    return output
