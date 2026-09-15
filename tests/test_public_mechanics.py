import ast
from copy import deepcopy
from pathlib import Path

import pytest

from abaqus_sim_agent.coding.workspace import FileSnapshot, WorkspaceSnapshot
from abaqus_sim_agent.evaluation.public_mechanics_cases import get_case, mechanics_cases
from abaqus_sim_agent.evaluation.public_mechanics_runner import execution_key
from abaqus_sim_agent.execution import AbaqusExecutionResult, AbaqusInvocation
from abaqus_sim_agent.execution.abaqus2024 import (
    ArtifactChange,
    ArtifactChangeKind,
    ArtifactFingerprint,
)
from abaqus_sim_agent.verification.public_mechanics_input import check_input
from abaqus_sim_agent.verification.public_mechanics_run import (
    assess_measurements,
    compare_metrics,
    provenance_errors,
    refinement_errors,
)

TRUSS = """*Heading
*Part, name=Rod
*Node
1,0,0,0
2,1000,0,0
*Element,type=T3D2
1,1,2
*Elset,elset=All
1
*Solid Section,elset=All,material=Steel
100,
*End Part
*Assembly,name=Assembly
*Instance,name=Rod-1,part=Rod
*End Instance
*Nset,nset=Left,instance=Rod-1
1
*Nset,nset=Right,instance=Rod-1
2
*Nset,nset=Whole,instance=Rod-1
1,2
*End Assembly
*Material,name=Steel
*Elastic
200000,.3
*Boundary
Left,1,1
Whole,2,3
*Step,name=Load,nlgeom=NO
*Static
1,1
*Cload
Right,1,10000
*End Step
"""


def rod_measurement():
    return {
        "nodes": {"1": [0.0, 0.0, 0.0], "2": [1000.0, 0.0, 0.0]},
        "elements": {"1": {"type": "T3D2", "nodes": [1, 2]}},
        "elastic": [[200000.0, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
    }


def check(tmp_path, text, case="uniform-axial-bar", measured=None):
    path = tmp_path / "model.inp"
    path.write_text(text, encoding="utf-8")
    return check_input(path, get_case(case), measured or rod_measurement())


def test_six_distinct_cases_and_independent_reference_formulas():
    cases = mechanics_cases()
    assert len({c.case_id for c in cases}) == 6
    assert cases[0].metrics["tip_u1"].target == 10000 * 1000 / (200000 * 100)
    assert cases[1].metrics["tip_u1"].target == pytest.approx(
        10000 / 200000 * (400 / 100 + 600 / 200)
    )
    inertia = 20 * 50**3 / 12
    assert cases[2].metrics["tip_u2"].target == -1000 * 1000**3 / (3 * 200000 * inertia)
    assert cases[3].metrics["tip_u2"].target == pytest.approx(
        -(1000**4) / (8 * 200000 * inertia)
    )
    assert all("analysis.json" not in c.goal() for c in cases)
    assert cases[4].refinement
    assert cases[5].refinement


def test_valid_truss_and_declared_zero_poisson_compaction(tmp_path):
    assert check(tmp_path, TRUSS) == []
    c = get_case("uniform-axial-bar").model_copy(update={"poisson": 0.0})
    m = rod_measurement()
    m["elastic"] = [[200000.0]]
    path = tmp_path / "zero.inp"
    path.write_text(TRUSS.replace("200000,.3", "200000,0"))
    assert check_input(path, c, m) == []
    assert check_input(path, get_case("uniform-axial-bar"), m)


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("100,\n*End Part", "200,\n*End Part"),
        ("Right,1,10000", "Right,1,9990"),
        ("Left,1,1", "Left,1,1\nRight,1,1"),
        ("200000,.3", "210000,.3"),
        ("nlgeom=NO", "nlgeom=YES"),
        ("*End Step", "*Temperature\nWhole,100\n*End Step"),
        ("Right,1,10000", "Right,1,10000\nRight,2,1\nRight,2,-1"),
    ],
)
def test_wrong_physics_cannot_pass_by_matching_result_json(tmp_path, before, after):
    assert check(tmp_path, TRUSS.replace(before, after))


def test_disconnected_or_unused_mesh_is_rejected(tmp_path):
    m = rod_measurement()
    m["nodes"]["3"] = [500.0, 0.0, 0.0]
    assert any("orphan" in e for e in check(tmp_path, TRUSS, measured=m))


def test_distributed_beam_is_not_equivalent_to_tip_force(tmp_path):
    text = TRUSS.replace("T3D2", "B31").replace(
        "*Solid Section,elset=All,material=Steel\n100,",
        "*Beam Section,elset=All,material=Steel,section=RECT\n20,50\n0,0,-1",
    )
    text = text.replace("Left,1,1\nWhole,2,3", "Left,ENCASTRE").replace(
        "*Cload\nRight,1,10000", "*Dload\nRod-1.All,PY,-1"
    )
    m = rod_measurement()
    m["elements"]["1"]["type"] = "B31"
    assert check(tmp_path, text, "cantilever-uniform-load", m) == []
    zeros = text.replace(
        "Rod-1.All,PY,-1", "Rod-1.All,PX,0\nRod-1.All,PY,-1\nRod-1.All,PZ,0"
    )
    assert check(tmp_path, zeros, "cantilever-uniform-load", m) == []
    assert check(
        tmp_path,
        text.replace("*Dload\nRod-1.All,PY,-1", "*Cload\nRight,2,-1000"),
        "cantilever-uniform-load",
        m,
    )
    assert check(
        tmp_path, text.replace("0,0,-1", "0,1,0"), "cantilever-uniform-load", m
    )


def test_reported_reference_without_matching_odb_is_rejected():
    c = get_case("uniform-axial-bar")
    values = {k: v.target for k, v in c.metrics.items()}
    assert compare_metrics(c, values, values) == []
    measured = {**values, "tip_u1": 0.25}
    assert len(compare_metrics(c, measured, values)) == 2
    assert compare_metrics(c, values, {**values, "tip_u1": True})
    assert compare_metrics(c, {**values, "tip_u1": float("nan")}, values)


def test_solid_load_cases_must_be_independent_centered_and_match_volume(tmp_path):
    nodes = {
        str(i + 1): p
        for i, p in enumerate(
            [
                [0.0, 0.0, 0.0],
                [6.0, 0.0, 0.0],
                [6.0, 0.2, 0.0],
                [0.0, 0.2, 0.0],
                [0.0, 0.0, 0.1],
                [6.0, 0.0, 0.1],
                [6.0, 0.2, 0.1],
                [0.0, 0.2, 0.1],
            ]
        )
    }
    m = {
        "nodes": nodes,
        "elements": {"1": {"type": "C3D8", "nodes": list(range(1, 9))}},
        "elastic": [[1e7, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["X", "Y", "Z"],
        "volume": 0.12,
    }
    text = TRUSS.split("*Step")[0]
    text = text.replace(
        "1,0,0,0\n2,1000,0,0",
        "\n".join(k + "," + ",".join(map(str, v)) for k, v in nodes.items()),
    ).replace("*Element,type=T3D2\n1,1,2", "*Element,type=C3D8\n1,1,2,3,4,5,6,7,8")
    text = (
        text.replace("100,\n*End Part", ",\n*End Part")
        .replace("Left,instance=Rod-1\n1", "Left,instance=Rod-1\n1,4,5,8")
        .replace("Right,instance=Rod-1\n2", "Right,instance=Rod-1\n2,3,6,7")
        .replace("200000,.3", "10000000,.3")
        .replace("Left,1,1\nWhole,2,3", "Left,ENCASTRE")
    )
    text += "\n".join(
        f"*Step,name={a},nlgeom=NO\n*Static\n1,1\n*Cload,op=NEW\nRight,{i + 1},.25\n*End Step"
        for i, a in enumerate("XYZ")
    )
    assert check(tmp_path, text, "solid-cantilever-directional-bending", m) == []
    split = text
    for dof in (1, 2, 3):
        split = split.replace(
            f"Right,{dof},.25",
            "*Cload,op=NEW\n"
            + "\n*Cload,op=NEW\n".join(f"{node},{dof},.25" for node in (2, 3, 6, 7)),
        )
    # 空OP=NEW与本步多个新载荷块共存；不能删除本步已经加入的载荷。
    assert check(tmp_path, split, "solid-cantilever-directional-bending", m) == []
    assert check(
        tmp_path,
        split.replace("7,2,.25", "7,2,.5"),
        "solid-cantilever-directional-bending",
        m,
    )
    assert check(
        tmp_path,
        text.replace("*Cload,op=NEW", "*Cload"),
        "solid-cantilever-directional-bending",
        m,
    )
    wrong = deepcopy(m)
    wrong["volume"] *= 0.5
    assert check(tmp_path, text, "solid-cantilever-directional-bending", wrong)


def test_refinement_is_not_just_a_second_filename():
    c = get_case("plate-with-hole-tension")
    coarse = {"nodes": {1: [], 2: []}, "metrics": {"hole_max_s11": 314.0}}
    fine = {"nodes": {1: [], 2: [], 3: []}, "metrics": {"hole_max_s11": 313.0}}
    assert refinement_errors(c, coarse, fine) == []
    assert refinement_errors(c, coarse, coarse)
    assert refinement_errors(c, coarse, coarse, require_more_nodes=False) == []
    assert refinement_errors(c, coarse, {**fine, "metrics": {"hole_max_s11": 320.0}})


def test_provenance_requires_current_inputs_order_and_no_cae_solver(tmp_path):
    files = [
        "build_model.py",
        "extract_results.py",
        "model.cae",
        "model.inp",
        "model.odb",
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
        ["model.odb"],
        ("abaqus", "job=model"),
    )
    extract = execution(
        AbaqusInvocation.ABAQUS_PYTHON,
        ["extract_results.py"],
        ["results.json"],
        ("abaqus", "python"),
    )
    c = get_case("uniform-axial-bar")
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
    assert provenance_errors(c, edited, [cae, solver, extract])
    assert (
        provenance_errors(
            c, edited, [cae, solver, extract], require_current_scripts=False
        )
        == []
    )
    assert provenance_errors(c, current, [solver, cae, extract])
    assert provenance_errors(
        c, current, [solver, cae, extract], require_current_scripts=False
    )
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
    illegal = execution(
        AbaqusInvocation.CAE_NOGUI,
        ["build_model.py"],
        ["model.cae", "model.inp", "model.odb"],
        ("abaqus", "cae"),
    )
    assert provenance_errors(c, current, [illegal, solver, extract])
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
    for p in root.glob("public_mechanics_*.py"):
        ast.parse(p.read_text(encoding="utf-8"), feature_version=(3, 10))


def test_results_first_makes_implementation_diagnostics_advisory(tmp_path):
    c = get_case("uniform-axial-bar")
    m = {**rod_measurement(), "metrics": {k: v.target for k, v in c.metrics.items()}}
    path = tmp_path / "model.inp"
    path.write_text(TRUSS + "\n*Monitor,node=2,dof=1\n", encoding="utf-8")
    errors, advisories = assess_measurements(
        c, {"model": path}, {"model": m}, m["metrics"]
    )
    assert errors == []
    assert advisories
    strict_errors, _ = assess_measurements(
        c, {"model": path}, {"model": m}, m["metrics"], results_first=False
    )
    assert strict_errors


@pytest.mark.parametrize("defect", ["geometry", "material", "results", "reported"])
def test_results_first_still_rejects_wrong_physics_or_fabricated_json(tmp_path, defect):
    c = get_case("uniform-axial-bar")
    m = {**rod_measurement(), "metrics": {k: v.target for k, v in c.metrics.items()}}
    reported = dict(m["metrics"])
    if defect == "geometry":
        m["nodes"]["2"][0] *= 2
    elif defect == "material":
        m["elastic"][0][0] *= 2
    elif defect == "results":
        m["metrics"]["tip_u1"] *= 2
    else:
        reported["reaction_x"] *= 2
    path = tmp_path / "model.inp"
    path.write_text(TRUSS, encoding="utf-8")
    errors, _ = assess_measurements(c, {"model": path}, {"model": m}, reported)
    assert errors
