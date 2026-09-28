import pytest

from abaqus_sim_agent.coding.instructions import build_instructions
from abaqus_sim_agent.coding.knowledge_scope import KNOWLEDGE_SCOPE_INSTRUCTIONS
from tests.test_coding_agent import _request


@pytest.mark.parametrize("enabled", [True, False])
def test_policy_is_in_actual_host_instructions_with_or_without_retrieval(
    tmp_path, enabled
):
    text = build_instructions(
        _request(tmp_path),
        api_catalog_enabled=enabled,
        concept_retrieval_enabled=enabled,
    )
    assert text.count(KNOWLEDGE_SCOPE_INSTRUCTIONS) == 1
    assert "missing catalog entry" in text
    assert "not proof that an API" in text
    assert "Do not add fallback logic" in text
    assert "leave the affected code unchanged" in text
    assert "not as a fabricated missing" in text


def test_policy_does_not_embed_case_solution_or_domain_values():
    assert not any(
        s in KNOWLEDGE_SCOPE_INSTRUCTIONS
        for s in ("LEFT", "nodeSets", "getSubset", "CPS4R", "210000")
    )
