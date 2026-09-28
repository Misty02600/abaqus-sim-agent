from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.contracts import CodingRunOutcome, CodingRunStatus
from abaqus_sim_agent.evaluation.model_change_runtime import (
    _api_catalog_binding,
    _validate_api_catalog_configuration,
)
from abaqus_sim_agent.evaluation.model_change_suite import (
    CaseId,
    ModelChangeSuiteInput,
    ModelChangeSuiteObservation,
    _load_api_catalog,
    _validate_evaluation_budgets,
    build_dataset,
    gate_report,
)
from abaqus_sim_agent.verification.load_change_run import (
    LoadModeChangeVerificationAttempt,
)
from abaqus_sim_agent.verification.material_change_run import (
    TwoMaterialPlateChangeVerificationAttempt,
)
from abaqus_sim_agent.verification.model_change_evidence import (
    WorkspaceChangeReport,
)
from abaqus_sim_agent.verification.plate_change_run import (
    PlateHoleChangeVerificationAttempt,
)


def test_suite_gate_requires_each_case_to_pass_its_own_gate() -> None:
    counts = {"center_hole": 0, "dual_material": 0, "load_mode": 0}

    async def task(inputs: ModelChangeSuiteInput) -> ModelChangeSuiteObservation:
        counts[inputs.case_id] += 1
        passed = inputs.case_id != "load_mode"
        return _observation(inputs.case_id, passed=passed)

    report = asyncio.run(
        build_dataset("source").evaluate(
            task, repeat=3, max_concurrency=1, progress=False
        )
    )
    gate = gate_report(
        report,
        case_ids=("center_hole", "dual_material", "load_mode"),
        repeat=3,
        required_passes=2,
    )

    assert sum(item.passes for item in gate.cases) == 6
    assert [item.passed for item in gate.cases] == [True, True, False]
    assert not gate.passed


def test_suite_case_filter_keeps_independent_gate_shape() -> None:
    async def task(inputs: ModelChangeSuiteInput) -> ModelChangeSuiteObservation:
        return _observation(inputs.case_id, passed=True)

    report = asyncio.run(
        build_dataset("source", case_ids=("dual_material",)).evaluate(
            task, repeat=1, max_concurrency=1, progress=False
        )
    )
    gate = gate_report(
        report,
        case_ids=("dual_material",),
        repeat=1,
        required_passes=1,
    )

    assert gate.passed
    assert len(gate.cases) == 1
    assert gate.cases[0].case_id == "dual_material"


def test_model_change_catalog_modes_require_consistent_artifacts(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "runs"
    catalog_dir = tmp_path / "catalog"
    ApiCatalog.build(runtime_release="2024").write(catalog_dir)

    assert _load_api_catalog(run_root, "off", None) is None
    with pytest.raises(ValueError, match="must not receive"):
        _load_api_catalog(run_root, "off", catalog_dir)
    with pytest.raises(ValueError, match="requires"):
        _load_api_catalog(run_root, "full", None)

    catalog = _load_api_catalog(run_root, "full", catalog_dir)

    assert catalog is not None
    binding = _api_catalog_binding(catalog)
    assert binding is not None
    assert binding.runtime_release == "2024"
    assert binding.card_count == 0
    _validate_api_catalog_configuration(None, "off")
    _validate_api_catalog_configuration(catalog, "full")
    with pytest.raises(ValueError, match="must not receive"):
        _validate_api_catalog_configuration(catalog, "off")
    with pytest.raises(ValueError, match="requires"):
        _validate_api_catalog_configuration(None, "full")


def test_model_change_catalog_cannot_live_under_run_root(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    catalog_dir = run_root / "catalog"
    ApiCatalog.build(runtime_release="2024").write(catalog_dir)

    with pytest.raises(ValueError, match="outside"):
        _load_api_catalog(run_root, "full", catalog_dir)


def test_model_change_evaluation_budgets_are_explicit_and_consistent() -> None:
    _validate_evaluation_budgets(
        model_request_budget=48,
        abaqus_call_budget=18,
        solver_call_budget=3,
    )
    for values, message in (
        ((0, 18, 3), "model request"),
        ((48, 0, 3), "Abaqus call"),
        ((48, 18, 0), "solver call"),
        ((48, 2, 3), "must not exceed"),
    ):
        with pytest.raises(ValueError, match=message):
            _validate_evaluation_budgets(
                model_request_budget=values[0],
                abaqus_call_budget=values[1],
                solver_call_budget=values[2],
            )


def _observation(
    case_id: CaseId,
    *,
    passed: bool,
) -> ModelChangeSuiteObservation:
    report = WorkspaceChangeReport(
        source_run_id="source",
        changes=(),
        build_model_patch="diff build",
        extract_results_patch="" if case_id != "dual_material" else "diff extract",
    )
    attempt_kwargs = {
        "passed": passed,
        "feedback": "synthetic",
        "change_report": report,
        "failure_stage": None if passed else "verification",
    }
    if case_id == "center_hole":
        attempt = PlateHoleChangeVerificationAttempt(**attempt_kwargs)
    elif case_id == "dual_material":
        attempt = TwoMaterialPlateChangeVerificationAttempt(**attempt_kwargs)
    else:
        attempt = LoadModeChangeVerificationAttempt(**attempt_kwargs)
    return ModelChangeSuiteObservation(
        case_id=case_id,
        run_id=f"{case_id}-run",
        source_run_id="source",
        outcome=CodingRunOutcome(
            run_id=f"{case_id}-run",
            status=(CodingRunStatus.COMPLETED if passed else CodingRunStatus.FAILED),
            summary="synthetic",
            failure_stage=None if passed else "verification",
        ),
        verification=attempt,
        abaqus_runtime_seconds=1.0,
    )
