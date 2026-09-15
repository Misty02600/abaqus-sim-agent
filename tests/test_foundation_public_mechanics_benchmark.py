import math

from abaqus_sim_agent.evaluation.foundation_public_mechanics_benchmark import (
    PILOT_DIAGNOSTIC_CASE_IDS,
    PILOT_PUBLIC_CASE_IDS,
    load_benchmark,
    paired_outcome,
    severity_rank,
)


def test_public_mechanics_v1_is_frozen_and_fully_covered() -> None:
    cases, rubrics, sources = load_benchmark()

    assert len(cases) == 6
    assert {case.case_id for case in cases} == {rubric.case_id for rubric in rubrics}
    assert {source.source_id for source in sources.sources} == {
        "libretexts-roylance-axial-v1",
        "libretexts-cantilever-deflection-v1",
        "ansys-vm-wb-mech-002-2024r2",
        "ansys-vm184-2025r2",
    }


def test_numeric_references_catch_basic_mechanics_regressions() -> None:
    cases, _, _ = load_benchmark()
    by_id = {case.case_id: case for case in cases}

    axial = by_id["uniform-axial-bar"].reference.values
    assert axial["axial_stress_MPa"] == 100.0
    assert axial["tip_elongation_mm"] == 0.5

    stepped = by_id["stepped-axial-bar"].reference.values
    assert stepped["segment_1_elongation_mm"] == 0.2
    assert stepped["segment_2_elongation_mm"] == 0.15
    assert math.isclose(stepped["tip_elongation_mm"], 0.35)

    tip = by_id["cantilever-tip-load"].reference.values
    assert math.isclose(tip["tip_deflection_magnitude_mm"], 8.0)
    assert math.isclose(tip["max_bending_stress_MPa"], 120.0)

    udl = by_id["cantilever-uniform-load"].reference.values
    assert math.isclose(udl["tip_deflection_magnitude_mm"], 3.0)
    assert math.isclose(udl["max_bending_stress_MPa"], 60.0)

    plate = by_id["plate-with-hole-tension"].reference.values
    assert plate["target_max_normal_x_stress_Pa"] == 312.5
    assert plate["target_stress_concentration_ratio"] == 3.125

    solid = by_id["solid-cantilever-directional-bending"].reference.values
    assert solid["target_delta_x_in"] == 3.0e-5
    assert solid["target_delta_y_in"] == 0.108
    assert solid["target_delta_z_in"] == 0.432


def test_pair_outcome_keeps_unknown_out_of_treatment_wins() -> None:
    assert paired_outcome("fail", "pass") == "win"
    assert paired_outcome("pass", "fail") == "loss"
    assert paired_outcome("pass", "pass") == "tie"
    assert paired_outcome("fail", "fail") == "tie"
    assert paired_outcome("unknown", "pass") == "unpaired_unknown"
    assert paired_outcome("pass", "unknown") == "unpaired_unknown"


def test_error_severity_order_and_pilot_are_predeclared() -> None:
    assert [severity_rank(level) for level in ("S0", "S1", "S2", "S3")] == [0, 1, 2, 3]
    assert PILOT_PUBLIC_CASE_IDS == (
        "uniform-axial-bar",
        "cantilever-tip-load",
        "plate-with-hole-tension",
    )
    assert PILOT_DIAGNOSTIC_CASE_IDS == (
        "existing-property-load-edit",
        "stress-result-semantics",
        "missing-user-physical-input",
    )
