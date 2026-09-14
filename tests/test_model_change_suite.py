from __future__ import annotations

import asyncio
import hashlib
import json
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


def test_checked_in_suite_evidence_preserves_per_case_limits() -> None:
    project_root = Path(__file__).parents[1]
    evidence = json.loads(
        (
            project_root
            / "benchmarks"
            / "coding"
            / "plate_model_change_suite_evaluation_20260825.json"
        ).read_text(encoding="utf-8")
    )

    assert evidence["cases"]["dual_material"]["formal_result"]["result"] == "2/3"
    assert evidence["cases"]["dual_material"]["formal_result"]["gate_passed"]
    assert evidence["cases"]["load_mode"]["corrected_formal_result"]["result"] == "0/3"
    assert not evidence["suite_result"]["suite_passed"]
    assert evidence["suite_contract"]["api_catalog_mode"] == "off"
    for case_id, filename in (
        ("dual_material", "plate_dual_material_change_request.json"),
        ("load_mode", "plate_load_mode_change_request.json"),
    ):
        path = project_root / "benchmarks" / "coding" / filename
        assert (
            evidence["cases"][case_id]["request_sha256"]
            == hashlib.sha256(path.read_bytes()).hexdigest()
        )


def test_checked_in_model_change_treatments_preserve_bounded_claims() -> None:
    project_root = Path(__file__).parents[1]
    evidence = json.loads(
        (
            project_root
            / "benchmarks"
            / "coding"
            / "plate_model_change_treatments_20260825.json"
        ).read_text(encoding="utf-8")
    )
    load = evidence["load_mode_full_catalog_treatment"]
    expanded = evidence["load_mode_expanded_budget_treatment"]
    typed_probe = evidence["load_mode_typed_probe_protocol_treatment"]
    center = evidence["center_hole_host_metric_treatment"]

    assert load["control"]["result"] == "0/3"
    assert load["treatment"]["result"] == "2/3"
    assert load["treatment"]["gate_passed"]
    assert load["treatment"]["catalog_cards_sha256"] == (
        "b557c9cb79c2a593afbb0c6ed332c664001f880cc5667d465729252e3635fd5e"
    )
    assert expanded["control"]["result"] == "2/3"
    assert expanded["treatment"]["result"] == "2/3"
    assert expanded["treatment"]["model_request_budget"] == 48
    assert expanded["treatment"]["abaqus_call_budget"] == 18
    assert (
        expanded["treatment"]["aggregate"]["model_requests"]
        > (expanded["control"]["aggregate"]["model_requests"])
    )
    assert any(
        attempt.get("status") == "passed" and attempt["model_requests"] > 36
        for attempt in expanded["treatment"]["attempts"]
    )
    assert typed_probe["v1_diagnostic"]["result"] == "0/3"
    assert typed_probe["v2_treatment"]["result"] == "2/3"
    assert typed_probe["v2_treatment"]["gate_passed"]
    assert typed_probe["v2_treatment"]["allowed_production_targets"] == [
        "build_model.py"
    ]
    assert not any(
        attempt.get("probe_statuses", {}).get("validated", 0)
        for attempt in typed_probe["v2_treatment"]["attempts"]
    )
    assert typed_probe["deterministic_contract_evidence"]["result"] == "passed"
    final9 = typed_probe["final9_verified_note_treatment"]
    assert final9["result"] == "2/3"
    assert final9["gate_passed"]
    assert final9["catalog"]["deterministic_benchmark"] == "13/13"
    assert final9["behavioral_adoption"] == {
        "note_delivered": "3/3",
        "production_scripts_using_assembly_surface": "3/3",
        "production_scripts_using_plain_set_region": "0/3",
    }
    parameter_lookup = typed_probe["final9_parameter_lookup_treatment"]
    assert parameter_lookup["result"] == "2/3"
    assert parameter_lookup["gate_passed"]
    assert parameter_lookup["catalog"]["deterministic_benchmark"] == "16/16"
    assert (
        parameter_lookup["catalog"]["cards_sha256"] == final9["catalog"]["cards_sha256"]
    )
    assert not parameter_lookup["implementation_boundary"]["catalog_artifact_changed"]
    assert (
        parameter_lookup["behavioral_adoption"]["direction_vector_parameter_found"]
        == "3/3"
    )
    assert (
        parameter_lookup["behavioral_adoption"][
            "direction_vector_payload_excluded_region_note"
        ]
        == "3/3"
    )
    failed_parameter_attempt = next(
        attempt
        for attempt in parameter_lookup["attempts"]
        if attempt.get("status") == "failed"
    )
    assert failed_parameter_attempt["failure_stage"] == "model_budget"
    assert "follower=true" in failed_parameter_attempt["first_verifier_failure"]
    assert "Python subprocess" in failed_parameter_attempt["convergence_failure"]
    assert (
        "Do not add another" in parameter_lookup["interpretation"]["semantic_stop_rule"]
    )
    no_shell = typed_probe["precise_feedback_no_shell_treatment"]
    assert no_shell["result"] == "3/3"
    assert no_shell["gate_passed"]
    assert no_shell["implementation"]["coder_general_shell_policy"] == (
        "coder-general-shell-disabled-v1"
    )
    assert not no_shell["execution_boundary_observation"]["run_command_registered"]
    assert no_shell["execution_boundary_observation"]["run_command_tool_calls"] == (
        "0/3"
    )
    replay = no_shell["actual_failure_artifact_replay"]
    assert replay["structured_result"]["follower_actual"] is True
    assert not replay["structured_result"]["follower_passed"]
    assert "follower=OFF" in replay["finding"]
    assert (
        "does not demonstrate Product-Agent adoption"
        in no_shell["interpretation"]["feedback_result"]
    )
    assert "No stable success-rate" in no_shell["interpretation"]["causal_limit"]
    assert center["formal_result"]["result"] == "1/3"
    assert not center["formal_result"]["gate_passed"]
    passed = next(
        attempt
        for attempt in center["formal_result"]["attempts"]
        if attempt.get("status") == "passed"
    )
    assert passed["extractor_preserved"]
    assert passed["extract_results_patch_sha256"] == hashlib.sha256(b"").hexdigest()
    for treatment, filename in (
        (load, "plate_load_mode_change_request.json"),
        (center, "plate_hole_change_request_v2.json"),
    ):
        request = project_root / "benchmarks" / "coding" / filename
        assert (
            treatment["frozen_request_sha256"]
            == hashlib.sha256(request.read_bytes()).hexdigest()
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
