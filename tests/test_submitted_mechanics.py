import json

import pytest

from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    ReadyForVerificationDecision,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.evaluation.mechanics_submissions import model_feedback
from abaqus_sim_agent.evaluation.public_mechanics_cases import (
    BASIC_LINEAR_CASE_IDS,
    get_case,
)
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
    AbaqusInvocation,
)
from abaqus_sim_agent.execution.abaqus2024 import (
    ArtifactChange,
    ArtifactChangeKind,
    ArtifactFingerprint,
)
from abaqus_sim_agent.verification.public_mechanics_run import (
    PublicMechanicsRunVerifier,
    compare_metrics,
    refinement_errors,
)


class NoIndependentReader(Abaqus2024Runner):
    async def run_trusted_abaqus_python(self, *args, **kwargs):
        pytest.fail("submitted-results grading must not run an independent ODB reader")


def delivered_case(tmp_path, case_id, *, values=None, inp_text="*Heading\nfixture"):
    case = get_case(case_id)
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    initial = WorkspaceResolver().snapshot(workspace)
    values = (
        values if values is not None else {k: m.target for k, m in case.metrics.items()}
    )
    if case.refinement:
        values.setdefault(
            "coarse_metrics", {k: values[k] for k in case.refinement_metrics}
        )
    files = {
        "build_model.py": "pass\n",
        "extract_results.py": "pass\n",
        "model.cae": "fixture",
        "report.md": "Interpolation at the prescribed physical location.",
        "results.json": json.dumps(values),
    }
    jobs = ("model_coarse", "model") if case.refinement else ("model",)
    for job in jobs:
        files.update(
            {
                job + ".inp": inp_text,
                job + ".odb": "fixture; independent readings deliberately unavailable",
                job + ".sta": "THE ANALYSIS HAS COMPLETED SUCCESSFULLY",
            }
        )
    for name, value in files.items():
        (workspace / name).write_text(value, encoding="utf-8")
    snapshot = WorkspaceResolver().snapshot(workspace).by_path()

    def fingerprint(name):
        return ArtifactFingerprint(
            path=name,
            size_bytes=snapshot[name].size_bytes,
            sha256=snapshot[name].sha256,
        )

    def execution(invocation, inputs, outputs, command):
        return AbaqusExecutionResult(
            invocation=invocation,
            command=command,
            workspace=workspace,
            exit_code=0,
            timed_out=False,
            duration_seconds=1,
            stdout="",
            stderr="",
            stdout_encoding="utf-8",
            stderr_encoding="utf-8",
            stdout_raw_path=state / "stdout",
            stderr_raw_path=state / "stderr",
            input_fingerprints=tuple(map(fingerprint, inputs)),
            artifact_changes=tuple(
                ArtifactChange(
                    kind=ArtifactChangeKind.CREATED, path=name, after=fingerprint(name)
                )
                for name in outputs
            ),
        )

    executions = [
        execution(
            AbaqusInvocation.CAE_NOGUI,
            ["build_model.py"],
            ["model.cae", *(j + ".inp" for j in jobs)],
            ("abaqus", "cae"),
        )
    ]
    executions += [
        execution(
            AbaqusInvocation.SOLVER_JOB,
            [j + ".inp"],
            [j + ".odb"],
            ("abaqus", "job=" + j),
        )
        for j in jobs
    ]
    executions.append(
        execution(
            AbaqusInvocation.ABAQUS_PYTHON,
            ["extract_results.py"],
            ["results.json"],
            ("abaqus", "python"),
        )
    )
    request = CodingRunRequest(
        run_id="submitted-results",
        goal=case.goal(),
        workspace=workspace,
        state_dir=state,
        runtime=RuntimeProfile(launcher_path=tmp_path / "abq2024.bat"),
    )
    decision = ReadyForVerificationDecision(
        summary="finished",
        deliverables=tuple({"relative_path": name} for name in files),
    )
    return (
        case,
        request,
        NoIndependentReader(workspace, state),
        initial,
        executions,
        decision,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", BASIC_LINEAR_CASE_IDS)
async def test_all_scoring_cases_grade_submitted_values_without_reopening_odb(
    tmp_path, case_id
):
    case, request, runner, initial, executions, decision = delivered_case(
        tmp_path, case_id
    )
    attempt = await PublicMechanicsRunVerifier(case).verify(
        request, runner, initial, executions, decision
    )
    assert attempt.passed
    assert attempt.reported == {k: m.target for k, m in case.metrics.items()}
    assert "modeling details are not independently verified" in attempt.feedback
    assert not list(request.state_dir.glob("public-oracle-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("factor", [1.01, 1.20])
async def test_le1_interpolated_submission_uses_reference_tolerance(tmp_path, factor):
    case = get_case("nafems-le1-elliptic-membrane")
    values = {k: m.target for k, m in case.metrics.items()}
    values["d_sigma_yy"] *= factor
    case, request, runner, initial, executions, decision = delivered_case(
        tmp_path, case.case_id, values=values
    )
    attempt = await PublicMechanicsRunVerifier(case).verify(
        request, runner, initial, executions, decision
    )
    assert attempt.passed is (factor == 1.01)
    if not attempt.passed:
        feedback = model_feedback(attempt, tuple(case.metrics))
        assert feedback == "Failed checks: metric d_sigma_yy."
        assert "reference" in attempt.feedback


@pytest.mark.parametrize(
    "invalid", [None, True, "92.7e6", [], {}, float("nan"), float("inf"), 10**1000]
)
def test_invalid_submission_is_a_numeric_failure(invalid):
    case = get_case("nafems-le1-elliptic-membrane")
    values = {k: m.target for k, m in case.metrics.items()}
    values["d_sigma_yy"] = invalid
    assert compare_metrics(case, values) == [
        "d_sigma_yy: missing or nonfinite submitted value"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "missing",
        "stale_json",
        "stale_odb",
        "wrong_order",
        "failed_stage",
        "syntax",
        "incomplete_solver",
        "no_execution",
    ],
)
async def test_reference_numbers_do_not_replace_execution_evidence(tmp_path, defect):
    case, request, runner, initial, executions, decision = delivered_case(
        tmp_path, "nafems-le1-elliptic-membrane"
    )
    if defect == "missing":
        (request.workspace / "model.cae").unlink()
    elif defect == "stale_json":
        (request.workspace / "results.json").write_text("{}", encoding="utf-8")
    elif defect == "stale_odb":
        (request.workspace / "model.odb").write_text("changed", encoding="utf-8")
    elif defect == "wrong_order":
        executions.reverse()
    elif defect == "failed_stage":
        executions.append(executions[-1].model_copy(update={"exit_code": 1}))
    elif defect == "syntax":
        (request.workspace / "extract_results.py").write_text(
            "def broken(", encoding="utf-8"
        )
    elif defect == "incomplete_solver":
        (request.workspace / "model.sta").write_text("in progress", encoding="utf-8")
    else:
        executions.clear()
    attempt = await PublicMechanicsRunVerifier(case).verify(
        request, runner, initial, executions, decision
    )
    assert not attempt.passed
    assert attempt.failure_stage == "provenance"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    ["missing_coarse_results", "nonfinite", "diverged", "missing_coarse_execution"],
)
async def test_refinement_still_requires_two_actual_runs_and_submitted_convergence(
    tmp_path, defect
):
    case = get_case("plate-with-hole-tension")
    values = {k: m.target for k, m in case.metrics.items()}
    values["coarse_metrics"] = (
        {}
        if defect == "missing_coarse_results"
        else {
            "hole_max_s11": float("nan")
            if defect == "nonfinite"
            else 400.0
            if defect == "diverged"
            else values["hole_max_s11"]
        }
    )
    case, request, runner, initial, executions, decision = delivered_case(
        tmp_path, case.case_id, values=values
    )
    if defect == "missing_coarse_execution":
        executions.pop(1)
    attempt = await PublicMechanicsRunVerifier(case).verify(
        request, runner, initial, executions, decision
    )
    assert not attempt.passed
    assert attempt.failure_stage == (
        "provenance" if defect == "missing_coarse_execution" else "task_oracle"
    )


def test_refinement_has_no_node_count_or_reader_numerical_constraint():
    case = get_case("plate-with-hole-tension")
    assert (
        refinement_errors(case, {"hole_max_s11": 313.0}, {"hole_max_s11": 312.5}) == []
    )


@pytest.mark.parametrize(
    ("case_id", "physical_definition"),
    [
        ("cantilever-uniform-load", "abs(RM3)"),
        ("circular-shaft-pure-torsion", "abs(左端RM1)"),
        ("thick-cylinder-internal-pressure", "r-z截面按面积加权"),
        ("plate-with-hole-tension", "圆柱孔壁表面全局S11的最大值"),
        ("solid-cantilever-directional-bending", "位移最大值"),
        ("nafems-le1-elliptic-membrane", "D=(2,0)处切向正应力，即全局S22"),
        ("nafems-le2-cylindrical-shell-patch", "物理外表面的圆周切向正应力"),
        (
            "nafems-le3-hemisphere-point-loads",
            "点载荷与极点约束仍须施加在题设A、C、E位置",
        ),
        ("nafems-le4-axisymmetric-hyperbolic-shell", "壳中面的子午向/环向正应力"),
        ("nafems-le5-z-section-cantilever", "A=(2.5,-1,-1)处壳中面的全局轴向S11"),
        ("nafems-le6-skew-plate", "物理下表面的面内最大主应力"),
        ("nafems-le7-axisymmetric-cylinder-sphere", "D=(r=1,z=1.4034 m)物理外表面"),
        (
            "nafems-le8-axisymmetric-crown-knuckle-shell",
            "D=(r=0.150763,z=0.5505748 m)物理外表面",
        ),
        ("nafems-le9-branched-axisymmetric-shell", "不能混入球壳或下圆柱"),
        ("nafems-le10-thick-plate-pressure", "D=(2,0,0.6)处全局S22"),
    ],
)
def test_physical_quantities_survive_removal_of_reader_constraints(
    case_id, physical_definition
):
    goal = get_case(case_id).goal()
    assert physical_definition in goal
    for obsolete in (
        "网格必须包含精确",
        "网格必须精确包含",
        "ELEMENT_NODAL",
        "SPOS",
        "SECTION INTEGRATION=GAUSS",
        "EVOL",
        "读取器要求单个实例",
    ):
        assert obsolete not in goal
    assert "插值或外推" in goal
    assert "本次实际ODB" in goal
    assert "不得把指定位置或分量替换成附近最大值" in goal


@pytest.mark.asyncio
async def test_vm184_surface_load_spelling_does_not_create_parser_or_review_gate(
    tmp_path,
):
    # 合成执行证据，只验证评分职责；此片段不是可独立求解的模型。
    inp_text = "*Heading\n*Dsload, follower=NO\nEND_FACE, TRVEC, 1.0, 1, 0, 0\n"
    case, request, runner, initial, executions, decision = delivered_case(
        tmp_path, "solid-cantilever-directional-bending", inp_text=inp_text
    )
    attempt = await PublicMechanicsRunVerifier(case).verify(
        request, runner, initial, executions, decision
    )
    assert attempt.passed
    assert attempt.findings == attempt.advisories == ()
    assert "script_review_status" not in attempt.model_dump()
