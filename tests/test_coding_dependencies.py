import pytest

import abaqus_sim_agent.coding.agent as coding_agent
from abaqus_sim_agent.coding.dependencies import (
    CodingAgentDependencies,
    RuntimeBudgetExceeded,
)


def test_run_scoped_dependencies_have_a_single_owner() -> None:
    assert CodingAgentDependencies.__module__ == "abaqus_sim_agent.coding.dependencies"
    assert RuntimeBudgetExceeded.__module__ == "abaqus_sim_agent.coding.dependencies"


@pytest.mark.parametrize(
    "name",
    [
        "ApiCatalogMode",
        "CodingAgentDependencies",
        "CODING_DECISION_OUTPUT_POLICY_VERSION",
        "CONCEPT_ADOPTION_POLICY_VERSION",
        "ConceptRetrievalMode",
        "EVIDENCE_REPAIR_CONVERGENCE_POLICY_VERSION",
        "PROBE_PROTOCOL_VERSION",
        "ProbeProtocolMode",
        "RuntimeBudgetExceeded",
    ],
)
def test_agent_module_does_not_reexport_moved_owners(name: str) -> None:
    assert not hasattr(coding_agent, name)
