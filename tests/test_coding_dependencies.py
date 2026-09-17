from abaqus_sim_agent.coding.dependencies import (
    CodingAgentDependencies,
    RuntimeBudgetExceeded,
)


def test_run_scoped_dependencies_have_a_single_owner() -> None:
    assert CodingAgentDependencies.__module__ == "abaqus_sim_agent.coding.dependencies"
    assert RuntimeBudgetExceeded.__module__ == "abaqus_sim_agent.coding.dependencies"
