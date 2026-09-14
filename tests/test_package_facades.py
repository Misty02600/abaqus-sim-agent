import importlib.util

import pytest

import abaqus_sim_agent.api_catalog as api_catalog
import abaqus_sim_agent.coding as coding
import abaqus_sim_agent.evaluation as evaluation
import abaqus_sim_agent.execution as execution
import abaqus_sim_agent.retrieval as retrieval
import abaqus_sim_agent.verification as verification


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (execution, "SimulationExecutor"),
        (execution, "MockSimulationExecutor"),
        (retrieval, "RetrievalEvaluation"),
        (retrieval, "evaluate_retrieval"),
        (api_catalog, "ApiCatalogEvaluation"),
        (api_catalog, "evaluate_api_catalog"),
        (verification, "PlateTensionRunVerifier"),
        (verification, "verify_plate_tension"),
        (evaluation, "ClosedLoopEvaluator"),
        (evaluation, "gate_report"),
    ],
)
def test_component_facades_do_not_export_internal_or_evaluation_apis(
    module: object,
    name: str,
) -> None:
    assert not hasattr(module, name)


def test_legacy_verification_base_module_is_removed() -> None:
    assert importlib.util.find_spec("abaqus_sim_agent.verification.base") is None


def test_coding_facade_exposes_resume_failures() -> None:
    assert issubclass(coding.CodingRunResumeError, RuntimeError)
    assert issubclass(
        coding.CodingRunReconciliationRequired,
        coding.CodingRunResumeError,
    )
    assert issubclass(coding.CodingRunWorkspaceChanged, coding.CodingRunResumeError)
    assert issubclass(coding.CodingRunAlreadyLocked, RuntimeError)
