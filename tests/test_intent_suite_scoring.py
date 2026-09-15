import importlib.util
import json
from pathlib import Path


def scoring_module():
    path = Path(__file__).parents[1] / "benchmarks/api_catalog/score_intent_suite.py"
    spec = importlib.util.spec_from_file_location("intent_suite_scoring", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_original_baseline_is_independent_of_model_failure_or_bad_rewrite():
    scoring = scoring_module()
    case = {"action": "search", "question": "原始问题"}
    assert scoring.query_forms(case, None) == [("original", "原始问题")]
    assert scoring.qualified_hit("original", 2, False, False)
    assert not scoring.qualified_hit("operation", 2, True, False)
    assert not scoring.qualified_hit("operation", 2, False, True)
    assert scoring.qualified_hit("operation", 2, True, True)
    assert not scoring.qualified_hit("original", None, True, True)


def test_clarification_is_not_ranked_as_an_original_search():
    scoring = scoring_module()
    case = {"action": "clarify", "question": "厚度未给"}
    assert scoring.query_forms(case, {"action": "clarify", "operation": None}) == []
    assert scoring.query_forms(
        case, {"action": "search", "operation": "guess thickness"}
    ) == [("operation", "guess thickness")]


def test_acceptance_cases_are_explicit_and_target_disjoint():
    root = Path(__file__).parents[1] / "benchmarks/api_catalog"
    new = json.loads((root / "intent_acceptance_cases_v1.json").read_bytes())["cases"]
    old = json.loads((root / "discovery_development_v1.json").read_bytes())["families"]
    assert len(new) == len({c["id"] for c in new}) == 20
    assert sum(c["action"] == "search" for c in new) == 16
    assert sum(c["action"] == "clarify" for c in new) == 4
    assert not (
        {n for c in new for n in c["expected"]}
        & {n for c in old for n in c["expected"]}
    )
    assert all(c["question"] and c["review"] for c in new)
    assert all(bool(c["expected"]) == (c["action"] == "search") for c in new)
    protocol = json.loads((root / "intent_regression_protocol_v1.json").read_bytes())
    assert len(protocol["guards"]) == 3
    assert len(protocol["goal_lines"]) == 9
