import json
from dataclasses import replace

import pytest

from abaqus_sim_agent.evaluation.equation_context_delivery import (
    context_references,
    deliver_with_context_references,
)
from abaqus_sim_agent.retrieval.equation_prose import build_equation_prose_units
from tests.test_equation_prose import DEFINITIONS, EQUATION, EXPLANATION
from tests.test_evidence_units import registry


def fixture():
    blocks, nodes = registry(EQUATION + DEFINITIONS + EXPLANATION)
    result = build_equation_prose_units(blocks, nodes)
    units = {u.evidence.parent_id: u for u in result.units}
    return result.relations, units


def test_context_targets_are_bound_to_original_group_and_source():
    relations, units = fixture()
    refs = context_references(relations, units)
    assert refs[relations[0].explanation_id][0]["body_included"] is False
    with pytest.raises(ValueError, match="duplicate"):
        context_references((*relations, *relations), units)
    with pytest.raises(ValueError, match="partitioned"):
        context_references([replace(relations[0], source_group_id="0" * 64)], units)
    target = relations[0].context_ids[0]
    original = units[target]
    units[target] = original.model_copy(
        update={
            "evidence": original.evidence.model_copy(update={"source_sha256": "b" * 64})
        }
    )
    with pytest.raises(ValueError, match="ownership"):
        context_references(relations, units)


def test_full_metadata_budget_boundary_drops_whole_unit_without_mutating_input():
    relations, units = fixture()
    refs = context_references(relations, units)
    key = relations[0].explanation_id
    item = {
        "id": key,
        "body": units[key].evidence.body,
        "body_scope": "complete_stored_evidence_unit",
    }
    result = {
        "status": "found",
        "evidence": [item],
        "context_tokens": 1,
        "trace": {"rejected": []},
    }
    accepted = deliver_with_context_references(
        result, refs, count=len, context_tokens=10000
    )
    used = len(
        json.dumps(accepted["evidence"], ensure_ascii=False, separators=(",", ":"))
    )
    assert used == accepted["context_tokens"]
    assert accepted["evidence"][0]["body"] == item["body"]
    assert "equation_context" not in item
    assert (
        deliver_with_context_references(result, refs, count=len, context_tokens=used)[
            "evidence"
        ]
        == accepted["evidence"]
    )
    rejected = deliver_with_context_references(
        result, refs, count=len, context_tokens=used - 1
    )
    assert rejected["evidence"] == []
    assert rejected["status"] == "not_found"
    assert len(rejected["trace"]["rejected"]) == 1
    assert result["trace"]["rejected"] == []


def test_early_guard_result_is_unchanged():
    result = {"status": "query_too_long", "evidence": [], "trace": {}}
    assert (
        deliver_with_context_references(result, {}, count=len, context_tokens=4000)
        == result
    )
