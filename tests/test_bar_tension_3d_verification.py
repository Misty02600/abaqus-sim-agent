from abaqus_sim_agent.verification.bar_tension_3d import (
    BarTension3DMeasurements,
    verify_bar_tension_3d,
)


def _measurements(**overrides: object) -> BarTension3DMeasurements:
    values: dict[str, object] = {
        "odb_path": "bar_tension.odb",
        "step_name": "Tension",
        "frame_index": 1,
        "instance_name": "BAR-1",
        "element_type_counts": {"C3D8R": 80},
        "node_count": 189,
        "axis_spans": (100.0, 10.0, 10.0),
        "coordinate_layer_counts": (21, 3, 3),
        "right_mean_u1": 0.1,
        "mean_s11": 210.0,
        "left_sum_rf1": -21000.0,
        "y_face_mean_u2_delta": -0.003,
        "z_face_mean_u3_delta": -0.003,
    }
    values.update(overrides)
    return BarTension3DMeasurements.model_validate(values)


def _freshness() -> dict[str, bool]:
    return {
        "build_model.py": True,
        "extract_results.py": True,
        "bar_tension.cae": True,
        "bar_tension.inp": True,
        "bar_tension.odb": True,
        "results.json": True,
    }


def _agent_results() -> dict[str, float]:
    return {
        "right_mean_u1": 0.1,
        "mean_s11": 210.0,
        "left_sum_rf1": -21000.0,
    }


def test_bar_tension_3d_verification_passes_reference_measurements() -> None:
    result = verify_bar_tension_3d(
        _measurements(),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results=_agent_results(),
    )

    assert result.passed
    assert result.findings == ()


def test_bar_tension_3d_verification_rejects_stale_artifact() -> None:
    freshness = _freshness()
    freshness["bar_tension.odb"] = False

    result = verify_bar_tension_3d(
        _measurements(),
        job_completed=True,
        artifact_freshness=freshness,
        agent_results=_agent_results(),
    )

    assert not result.passed
    assert not result.artifacts_fresh


def test_bar_tension_3d_verification_rejects_wrong_mesh_and_physics() -> None:
    result = verify_bar_tension_3d(
        _measurements(
            element_type_counts={"C3D8": 80},
            right_mean_u1=0.08,
            mean_s11=180.0,
            left_sum_rf1=-17000.0,
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


def test_bar_tension_3d_verification_rejects_wrong_geometry_and_constraints() -> None:
    result = verify_bar_tension_3d(
        _measurements(
            node_count=180,
            axis_spans=(90.0, 10.0, 8.0),
            coordinate_layer_counts=(20, 3, 3),
            y_face_mean_u2_delta=0.0,
            z_face_mean_u3_delta=-0.002,
        ),
        job_completed=True,
        artifact_freshness=_freshness(),
        agent_results=_agent_results(),
    )

    assert not result.passed
    assert not result.node_count_passed
    assert not result.coordinate_layers_passed
    assert not result.x_span.passed
    assert result.y_span.passed
    assert not result.z_span.passed
    assert not result.y_contraction.passed
    assert not result.z_contraction.passed


def test_bar_tension_3d_verification_rejects_fabricated_results_json() -> None:
    result = verify_bar_tension_3d(
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
