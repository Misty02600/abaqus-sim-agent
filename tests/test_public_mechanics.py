import ast
import math
from pathlib import Path

import pytest

from abaqus_sim_agent.coding.contracts import ReadyForVerificationDecision
from abaqus_sim_agent.coding.workspace import FileSnapshot, WorkspaceSnapshot
from abaqus_sim_agent.evaluation.public_mechanics_cases import get_case, mechanics_cases
from abaqus_sim_agent.evaluation.public_mechanics_runner import execution_key
from abaqus_sim_agent.execution import AbaqusExecutionResult, AbaqusInvocation
from abaqus_sim_agent.execution.abaqus2024 import (
    ArtifactChange,
    ArtifactChangeKind,
    ArtifactFingerprint,
)
from abaqus_sim_agent.verification.public_mechanics_run import (
    provenance_errors as check_provenance,
)
from abaqus_sim_agent.verification.public_mechanics_run import (
    resolve_solver_results,
)


def test_twenty_distinct_cases_and_independent_reference_formulas():
    cases = mechanics_cases()
    assert len({c.case_id for c in cases}) == 20
    assert {case.run_budget().max_model_requests for case in cases} == {200}
    assert {case.run_budget().max_api_lookup_calls for case in cases} == {None}
    assert {case.run_budget().max_concept_lookup_calls for case in cases} == {None}
    assert {case.run_budget().max_diagnostic_retrieval_calls for case in cases} == {
        None
    }
    assert {case.run_budget().max_static_preflight_calls for case in cases} == {None}
    assert {case.run_budget().max_abaqus_calls for case in cases} == {80}
    assert {case.run_budget().max_solver_calls for case in cases} == {10}
    assert {case.run_budget().wall_time_seconds for case in cases} == {5400.0}
    assert cases[0].metrics["tip_u1"].target == 10000 * 1000 / (200000 * 100)
    assert cases[1].metrics["tip_u1"].target == pytest.approx(
        10000 / 200000 * (400 / 100 + 600 / 200)
    )
    inertia = 20 * 50**3 / 12
    assert cases[2].metrics["tip_u2"].target == -1000 * 1000**3 / (3 * 200000 * inertia)
    assert cases[3].metrics["tip_u2"].target == pytest.approx(
        -(1000**4) / (8 * 200000 * inertia)
    )
    shear_modulus = 200000 / (2 * (1 + 0.3))
    polar_moment = 3.141592653589793 * 20**4 / 32
    assert cases[4].metrics["tip_ur1"].target == pytest.approx(
        100000 * 1000 / (shear_modulus * polar_moment)
    )
    assert cases[4].metrics["surface_shear_nominal"].target == pytest.approx(
        100000 * 10 / polar_moment
    )
    assert cases[5].metrics["tip_u1"].target == pytest.approx(0.05)
    assert cases[5].metrics["mean_s11"].target == pytest.approx(200000 * 0.05 / 100)
    assert cases[5].metrics["reaction_x"].target == pytest.approx(-100 * 20 * 2)
    a, b, pressure = 20.0, 40.0, 100.0
    lame_a = pressure * a**2 / (b**2 - a**2)
    lame_b = pressure * a**2 * b**2 / (b**2 - a**2)
    assert cases[6].metrics["inner_u1"].target == pytest.approx(
        ((1 - 0.3) * lame_a * a + (1 + 0.3) * lame_b / a) / 200000
    )
    assert cases[6].metrics["outer_u1"].target == pytest.approx(
        ((1 - 0.3) * lame_a * b + (1 + 0.3) * lame_b / b) / 200000
    )
    assert cases[6].metrics["area_mean_s11"].target == pytest.approx(
        lame_a - lame_b / (a * b)
    )
    assert cases[6].metrics["area_mean_s33"].target == pytest.approx(
        lame_a + lame_b / (a * b)
    )
    assert all("analysis.json" not in c.goal() for c in cases)
    assert cases[7].case_id == "nafems-le6-skew-plate"
    assert cases[7].load == pytest.approx(700.0)
    assert cases[7].metrics["center_lower_max_principal"].target == pytest.approx(
        0.802e6
    )
    assert cases[7].metrics["reaction_z"].target == pytest.approx(0.5 * 700.0)
    assert cases[8].refinement
    assert cases[9].refinement
    assert cases[10].case_id == "nafems-le1-elliptic-membrane"
    assert cases[10].metrics["d_sigma_yy"].target == pytest.approx(92.7e6)
    assert cases[10].metrics["reaction_x"].target == pytest.approx(-2.75e6)
    assert cases[10].metrics["reaction_y"].target == pytest.approx(-3.25e6)
    assert cases[11].case_id == "nafems-le10-thick-plate-pressure"
    assert cases[11].metrics["d_sigma_yy"].target == pytest.approx(-5.38e6)
    expected_area = 3.141592653589793 / 4 * (3.25 * 2.75 - 2.0 * 1.0)
    assert cases[11].metrics["reaction_z"].target == pytest.approx(expected_area * 1e6)
    assert cases[12].case_id == "nafems-le3-hemisphere-point-loads"
    assert cases[12].radius == pytest.approx(10.0)
    assert cases[12].metrics["a_u1"].target == pytest.approx(0.185)
    assert cases[13].case_id == "nafems-le5-z-section-cantilever"
    assert cases[13].load == pytest.approx(1.2e6)
    assert cases[13].metrics["a_sigma_xx"].target == pytest.approx(-108.0e6)
    assert cases[14].case_id == "nafems-le2-cylindrical-shell-patch"
    assert cases[14].radius == pytest.approx(1.0)
    assert cases[14].arc_angle_degrees == pytest.approx(10.0)
    assert cases[14].metrics["e_outer_s11"].target == pytest.approx(60.0e6)
    assert cases[14].metrics["reaction_m3"].target == pytest.approx(-500.0)
    assert cases[15].case_id == "nafems-le4-axisymmetric-hyperbolic-shell"
    assert cases[15].radius == pytest.approx(1.0)
    assert cases[15].metrics["k_meridional_s11"].target == pytest.approx(-50.0e6)
    assert cases[15].metrics["k_hoop_s22"].target == pytest.approx(50.0e6)
    assert cases[15].metrics["reaction_z"].target == pytest.approx(math.pi * 1.0e6)
    assert cases[16].case_id == "nafems-le7-axisymmetric-cylinder-sphere"
    assert cases[16].axisymmetric_shell_profile == "sphere_cylinder"
    assert cases[16].sample_axial_position == pytest.approx(1.4034)
    assert cases[16].metrics["d_outer_s11"].target == pytest.approx(25.9e6)
    assert cases[16].metrics["reaction_z"].target == pytest.approx(-math.pi * 1.0e6)
    assert cases[17].case_id == "nafems-le8-axisymmetric-crown-knuckle-shell"
    assert cases[17].axisymmetric_shell_profile == "crown_knuckle_cylinder"
    assert cases[17].sample_radius == pytest.approx(0.150763)
    assert cases[17].sample_axial_position == pytest.approx(0.5505748)
    assert cases[17].metrics["d_outer_s22"].target == pytest.approx(94.5e6)
    assert cases[17].metrics["reaction_z"].target == pytest.approx(
        -math.pi * 1.0e6 * 0.25**2
    )
    assert cases[18].case_id == "nafems-le9-branched-axisymmetric-shell"
    assert cases[18].axisymmetric_shell_profile == "branched_sphere_cylinder"
    assert cases[18].sample_radius == pytest.approx(0.707107)
    assert cases[18].sample_axial_position == pytest.approx(1.0)
    assert cases[18].metrics["c_outer_s11"].target == pytest.approx(-319.9e6)
    assert cases[18].metrics["reaction_z"].target == pytest.approx(
        math.pi * 1.0e6 * 0.707107**2
    )
    assert cases[19].case_id == "nafems-le11-thermal-solid"
    assert cases[19].family == "thermal_solid"
    assert cases[19].thermal_expansion == pytest.approx(2.3e-4)
    assert cases[19].metrics["a_sigma_zz"].target == pytest.approx(-105.0e6)


def test_provenance_requires_current_inputs_and_order(tmp_path):
    files = [
        "build_model.py",
        "extract_results.py",
        "model.cae",
        "model.inp",
        "model.odb",
        "model.sta",
        "results.json",
    ]
    current = WorkspaceSnapshot(
        files=tuple(
            FileSnapshot(relative_path=p, size_bytes=1, sha256="a" * 64) for p in files
        )
    )

    def execution(invocation, inputs, outputs, command):
        return AbaqusExecutionResult(
            invocation=invocation,
            command=command,
            workspace=tmp_path,
            exit_code=0,
            timed_out=False,
            duration_seconds=1,
            stdout="",
            stderr="",
            stdout_encoding="utf8",
            stderr_encoding="utf8",
            stdout_raw_path=tmp_path / "out",
            stderr_raw_path=tmp_path / "err",
            input_fingerprints=tuple(
                ArtifactFingerprint(path=p, size_bytes=1, sha256="a" * 64)
                for p in inputs
            ),
            artifact_changes=tuple(
                ArtifactChange(
                    kind=ArtifactChangeKind.CREATED,
                    path=p,
                    after=ArtifactFingerprint(path=p, size_bytes=1, sha256="a" * 64),
                )
                for p in outputs
            ),
        )

    cae = execution(
        AbaqusInvocation.CAE_NOGUI,
        ["build_model.py"],
        ["model.cae", "model.inp"],
        ("abaqus", "cae"),
    )
    solver = execution(
        AbaqusInvocation.SOLVER_JOB,
        ["model.inp"],
        ["model.odb", "model.sta"],
        ("abaqus", "job=model", "input=model.inp"),
    )
    extract = execution(
        AbaqusInvocation.ABAQUS_PYTHON,
        ["extract_results.py"],
        ["results.json"],
        ("abaqus", "python"),
    )
    c = get_case("uniform-axial-bar")

    def provenance_errors(case, snapshot, executions, observations=None):
        decision = ReadyForVerificationDecision(
            summary="finished",
            deliverables=({"relative_path": "model.odb", "role": "primary"},),
        )
        sources, errors = resolve_solver_results(
            case, decision, snapshot, executions, observations or {}
        )
        return errors or check_provenance(snapshot, executions, sources, observations)

    assert provenance_errors(c, current, [cae, solver, extract]) == []
    edited = current.model_copy(
        update={
            "files": tuple(
                f.model_copy(update={"sha256": "b" * 64})
                if f.relative_path == "build_model.py"
                else f
                for f in current.files
            )
        }
    )
    # Script-only edits do not invalidate matching artifact/execution evidence.
    assert provenance_errors(c, edited, [cae, solver, extract]) == []
    assert provenance_errors(c, current, [solver, cae, extract])
    altered = current.model_copy(
        update={
            "files": tuple(
                f.model_copy(update={"sha256": "b" * 64})
                if f.relative_path == "model.odb"
                else f
                for f in current.files
            )
        }
    )
    assert provenance_errors(c, altered, [cae, solver, extract])
    authoring_with_old_solver_output = execution(
        AbaqusInvocation.CAE_NOGUI,
        ["build_model.py"],
        ["model.cae", "model.inp", "model.odb"],
        ("abaqus", "cae"),
    )
    assert (
        provenance_errors(
            c, current, [authoring_with_old_solver_output, solver, extract]
        )
        == []
    )
    assert provenance_errors(c, current, [authoring_with_old_solver_output, extract])
    identical = extract.model_copy(update={"artifact_changes": ()})
    assert provenance_errors(c, current, [cae, solver, identical])
    before = {"sha256": "a" * 64, "size": 1, "mtime_ns": 1}
    after = {**before, "mtime_ns": 2}
    witness = {
        execution_key(identical): {
            "before": {"results.json": before},
            "after": {"results.json": after},
        }
    }
    assert provenance_errors(c, current, [cae, solver, identical], witness) == []
    witness[execution_key(identical)]["before"] = {"results.json": after}
    assert provenance_errors(c, current, [cae, solver, identical], witness)


def test_embedded_scripts_are_python_310_syntax():
    root = Path(__file__).parents[1] / "src/abaqus_sim_agent/verification/assets"
    for p in sorted(root.glob("*.py")):
        ast.parse(p.read_text(encoding="utf-8"), feature_version=(3, 10))
