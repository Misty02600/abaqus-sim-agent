from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

from abaqus_sim_agent.verification.beam_reference import (
    BeamTensionMeasurements,
    BeamTensionOracleSpec,
    BeamTensionVerification,
    verify_beam_tension,
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BeamSectionChangeOracleSpec(BeamTensionOracleSpec):
    profile_a: float = 20.0
    profile_b: float = 10.0
    left_rf1: float = -42_000.0
    right_rf1: float = 42_000.0
    mean_sf1: float = 42_000.0


class BeamSectionChangeVerification(_FrozenModel):
    passed: bool
    source_run_verified: bool
    build_script_changed: bool
    extractor_preserved: bool
    execution_provenance_passed: bool
    new_requirement_passed: bool
    regression_passed: bool
    beam_verification: BeamTensionVerification
    findings: tuple[str, ...] = ()


def verify_beam_section_change(
    measurements: BeamTensionMeasurements,
    *,
    job_completed: bool,
    artifact_freshness: Mapping[str, bool],
    source_run_verified: bool,
    build_script_changed: bool,
    extractor_preserved: bool,
    execution_provenance_passed: bool,
    agent_results: Mapping[str, float] | None,
    input_contract_passed: bool,
    spec: BeamSectionChangeOracleSpec | None = None,
) -> BeamSectionChangeVerification:
    expected = spec or BeamSectionChangeOracleSpec()
    effective_freshness = dict(artifact_freshness)
    effective_freshness["build_model.py"] = build_script_changed
    effective_freshness["extract_results.py"] = extractor_preserved
    beam_verification = verify_beam_tension(
        measurements,
        job_completed=job_completed,
        artifact_freshness=effective_freshness,
        agent_results=agent_results,
        input_contract_passed=input_contract_passed,
        spec=expected,
    )
    new_requirement_passed = all(
        (
            beam_verification.profile_passed,
            beam_verification.u1.passed,
            beam_verification.left_rf1.passed,
            beam_verification.right_rf1.passed,
            beam_verification.rf1_balance.passed,
            beam_verification.sf1.passed,
            beam_verification.sf1_uniformity.passed,
        )
    )
    regression_passed = all(
        (
            source_run_verified,
            build_script_changed,
            extractor_preserved,
            execution_provenance_passed,
            beam_verification.input_contract_passed,
            beam_verification.analysis_step_count_passed,
            beam_verification.step_definition_passed,
            beam_verification.element_type_passed,
            beam_verification.element_count_passed,
            beam_verification.node_count_passed,
            beam_verification.connectivity_passed,
            beam_verification.coordinate_layers_passed,
            beam_verification.coordinates_passed,
            beam_verification.material_passed,
            beam_verification.section_linkage_passed,
            beam_verification.orientation_passed,
            beam_verification.sf1_value_count_passed,
            beam_verification.sf1_element_coverage_passed,
            beam_verification.sf1_sign_passed,
            beam_verification.results_json_valid,
            beam_verification.results_match_oracle,
            beam_verification.x_span.passed,
            beam_verification.transverse_coordinate.passed,
            beam_verification.elastic_e.passed,
            beam_verification.elastic_nu.passed,
            beam_verification.transverse_u.passed,
            beam_verification.transverse_sf.passed,
        )
    )
    findings = list(beam_verification.findings)
    if not source_run_verified:
        findings.append("source B31 CodingRun was not independently verified")
    if not build_script_changed:
        findings.append("build_model.py was not changed from the verified seed")
    if not extractor_preserved:
        findings.append(
            "extract_results.py changed despite an unchanged result contract"
        )
    if not execution_provenance_passed:
        findings.append("current artifacts do not satisfy the three-stage provenance")
    passed = beam_verification.passed and new_requirement_passed and regression_passed
    return BeamSectionChangeVerification(
        passed=passed,
        source_run_verified=source_run_verified,
        build_script_changed=build_script_changed,
        extractor_preserved=extractor_preserved,
        execution_provenance_passed=execution_provenance_passed,
        new_requirement_passed=new_requirement_passed,
        regression_passed=regression_passed,
        beam_verification=beam_verification,
        findings=tuple(dict.fromkeys(findings)),
    )
