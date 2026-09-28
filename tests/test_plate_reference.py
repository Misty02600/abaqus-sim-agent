from abaqus_sim_agent.verification.plate_reference import (
    PlateTensionMeasurements,
    PlateTensionOracleSpec,
    verify_plate_tension,
)


def _measurements(**overrides: object) -> PlateTensionMeasurements:
    values: dict[str, object] = {
        "odb_path": "plate_tension.odb",
        "step_name": "Tension",
        "frame_index": 1,
        "instance_name": "PLATE-1",
        "element_type_counts": {"CPS4R": 80},
        "right_mean_u1": 0.1,
        "mean_s11": 210.0,
        "left_sum_rf1": -4200.0,
    }
    values.update(overrides)
    return PlateTensionMeasurements.model_validate(values)


def _freshness() -> dict[str, bool]:
    return {
        "build_model.py": True,
        "extract_results.py": True,
        "plate_tension.cae": True,
        "plate_tension.inp": True,
        "plate_tension.odb": True,
        "results.json": True,
    }


def _agent_results() -> dict[str, float]:
    return {
        "right_mean_u1": 0.1,
        "mean_s11": 210.0,
        "left_sum_rf1": -4200.0,
    }


def test_plate_tension_verification_passes_reference_measurements() -> None:
    result = verify_plate_tension(
        _measurements(),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results=_agent_results(),
    )

    assert result.passed
    assert result.findings == ()


def test_plate_tension_verification_accepts_one_allowed_element_choice() -> None:
    spec = PlateTensionOracleSpec(allowed_element_types=("CPS4", "CPS4R"))

    cps4 = verify_plate_tension(
        _measurements(element_type_counts={"CPS4": 80}),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results=_agent_results(),
        spec=spec,
    )
    mixed = verify_plate_tension(
        _measurements(element_type_counts={"CPS4": 40, "CPS4R": 40}),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results=_agent_results(),
        spec=spec,
    )

    assert cps4.passed
    assert not mixed.passed
    assert not mixed.element_type_passed


def test_plate_tension_verification_rejects_stale_artifact() -> None:
    freshness = _freshness()
    freshness["plate_tension.odb"] = False

    result = verify_plate_tension(
        _measurements(),
        job_completed=True,
        artifact_freshness=freshness,
        agent_results=_agent_results(),
    )

    assert not result.passed
    assert not result.artifacts_fresh


def test_plate_tension_verification_rejects_wrong_mesh_and_physics() -> None:
    result = verify_plate_tension(
        _measurements(
            element_type_counts={"CPE4R": 80},
            right_mean_u1=0.08,
            mean_s11=180.0,
            left_sum_rf1=-3500.0,
        ),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results=_agent_results(),
    )

    assert not result.passed
    assert not result.element_type_passed
    assert not result.element_count_passed
    assert not result.u1.passed
    assert not result.s11.passed
    assert not result.rf1.passed


def test_plate_tension_verification_rejects_fabricated_results_json() -> None:
    result = verify_plate_tension(
        _measurements(),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results={
            "right_mean_u1": 0.1,
            "mean_s11": 210.0,
            "left_sum_rf1": 0.0,
        },
    )

    assert not result.passed
    assert result.results_json_valid
    assert not result.results_match_oracle
