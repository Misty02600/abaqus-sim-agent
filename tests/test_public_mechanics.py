import ast
import math
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
    model_identity_errors,
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


def test_twenty_distinct_cases_and_independent_reference_formulas():
    cases = mechanics_cases()
    assert len({c.case_id for c in cases}) == 20
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


def test_circular_shaft_torsion_contract(tmp_path):
    text = TRUSS.replace("T3D2", "B31").replace(
        "*Solid Section,elset=All,material=Steel\n100,",
        "*Beam Section,elset=All,material=Steel,section=CIRC\n10\n0,1,0",
    )
    text = text.replace("Left,1,1\nWhole,2,3", "Left,ENCASTRE").replace(
        "Right,1,10000", "Right,4,100000"
    )
    m = rod_measurement()
    m["elements"]["1"]["type"] = "B31"
    assert check(tmp_path, text, "circular-shaft-pure-torsion", m) == []
    assert check(
        tmp_path,
        text.replace("section=CIRC\n10", "section=CIRC\n12"),
        "circular-shaft-pure-torsion",
        m,
    )
    assert check(
        tmp_path,
        text.replace("Right,4,100000", "Right,4,90000"),
        "circular-shaft-pure-torsion",
        m,
    )
    assert check(
        tmp_path,
        text.replace("Right,4,100000", "Right,2,100000"),
        "circular-shaft-pure-torsion",
        m,
    )


def test_plane_stress_uniform_tension_contract(tmp_path):
    text = """*Heading
*Part, name=Plate
*Node
1,0,0
2,100,0
3,100,20
4,0,20
*Element,type=CPS4
1,1,2,3,4
*Elset,elset=All
1
*Solid Section,elset=All,material=Steel
2,
*End Part
*Assembly,name=Assembly
*Instance,name=Plate-1,part=Plate
*End Instance
*Nset,nset=Left,instance=Plate-1
1,4
*Nset,nset=Right,instance=Plate-1
2,3
*Nset,nset=Pin,instance=Plate-1
1
*End Assembly
*Material,name=Steel
*Elastic
200000,.3
*Boundary
Left,1,1
Pin,2,2
Right,1,1,.05
*Step,name=Load,nlgeom=NO
*Static
1,1
*End Step
"""
    m = {
        "nodes": {
            "1": [0.0, 0.0, 0.0],
            "2": [100.0, 0.0, 0.0],
            "3": [100.0, 20.0, 0.0],
            "4": [0.0, 20.0, 0.0],
        },
        "elements": {"1": {"type": "CPS4", "nodes": [1, 2, 3, 4]}},
        "elastic": [[200000.0, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
    }
    assert check(tmp_path, text, "plane-stress-uniform-tension", m) == []
    assert check(
        tmp_path,
        text.replace(
            "*Solid Section,elset=All,material=Steel\n2,",
            "*Solid Section,elset=All,material=Steel\n1,",
        ),
        "plane-stress-uniform-tension",
        m,
    )
    assert check(
        tmp_path,
        text.replace("Right,1,1,.05", "Right,1,1,.04"),
        "plane-stress-uniform-tension",
        m,
    )
    assert check(
        tmp_path,
        text.replace("Pin,2,2", "Left,2,2"),
        "plane-stress-uniform-tension",
        m,
    )


def test_axisymmetric_thick_cylinder_contract(tmp_path):
    text = """*Heading
*Part, name=Cylinder
*Node
1,20,0
2,40,0
3,40,20
4,20,20
*Element,type=CAX4
1,1,2,3,4
*Elset,elset=All
1
*Solid Section,elset=All,material=Steel
,
*End Part
*Assembly,name=Assembly
*Instance,name=Cylinder-1,part=Cylinder
*End Instance
*Nset,nset=Pin,instance=Cylinder-1
2
*Surface,type=ELEMENT,name=Inner
Cylinder-1.All,S4
*End Assembly
*Material,name=Steel
*Elastic
200000,.3
*Boundary
Pin,2,2
*Step,name=Load,nlgeom=NO
*Static
1,1
*Dsload
Inner,P,100
*End Step
"""
    m = {
        "nodes": {
            "1": [20.0, 0.0, 0.0],
            "2": [40.0, 0.0, 0.0],
            "3": [40.0, 20.0, 0.0],
            "4": [20.0, 20.0, 0.0],
        },
        "elements": {"1": {"type": "CAX4", "nodes": [1, 2, 3, 4]}},
        "elastic": [[200000.0, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
    }
    assert check(tmp_path, text, "thick-cylinder-internal-pressure", m) == []
    assert check(
        tmp_path,
        text.replace("Inner,P,100", "Inner,P,90"),
        "thick-cylinder-internal-pressure",
        m,
    )
    assert check(
        tmp_path,
        text.replace("Cylinder-1.All,S4", "Cylinder-1.All,S2"),
        "thick-cylinder-internal-pressure",
        m,
    )
    assert check(
        tmp_path,
        text.replace("Pin,2,2", "Pin,1,1"),
        "thick-cylinder-internal-pressure",
        m,
    )
    wrong = deepcopy(m)
    wrong["elements"]["1"]["type"] = "CPS4"
    assert check(tmp_path, text, "thick-cylinder-internal-pressure", wrong)


def test_nafems_le6_skew_plate_contract(tmp_path):
    text = """*Heading
*Part, name=Plate
*Node
1,0,0,0
2,.5,0,0
3,1,0,0
4,.4330127018922193,.25,0
5,.9330127018922193,.25,0
6,1.4330127018922194,.25,0
7,.8660254037844386,.5,0
8,1.3660254037844386,.5,0
9,1.8660254037844386,.5,0
*Element,type=S4
1,1,2,5,4
2,2,3,6,5
3,4,5,8,7
4,5,6,9,8
*Elset,elset=All
1,2,3,4
*Shell Section,elset=All,material=Steel
.01,
*End Part
*Assembly,name=Assembly
*Instance,name=Plate-1,part=Plate
*End Instance
*Nset,nset=Edges,instance=Plate-1
1,2,3,4,6,7,8,9
*Nset,nset=A,instance=Plate-1
1
*Nset,nset=B,instance=Plate-1
3
*Elset,elset=AllShell,instance=Plate-1
1,2,3,4
*Surface,type=ELEMENT,name=LoadSurf
AllShell,SPOS
*End Assembly
*Material,name=Steel
*Elastic
210000000000,.3
*Boundary
Edges,3,3
A,1,2
B,2,2
*Step,name=Load,nlgeom=NO
*Static
1,1
*Dsload
LoadSurf,P,700
*End Step
"""
    m = {
        "nodes": {
            "1": [0.0, 0.0, 0.0],
            "2": [0.5, 0.0, 0.0],
            "3": [1.0, 0.0, 0.0],
            "4": [0.4330127018922193, 0.25, 0.0],
            "5": [0.9330127018922193, 0.25, 0.0],
            "6": [1.4330127018922194, 0.25, 0.0],
            "7": [0.8660254037844386, 0.5, 0.0],
            "8": [1.3660254037844386, 0.5, 0.0],
            "9": [1.8660254037844386, 0.5, 0.0],
        },
        "elements": {
            "1": {"type": "S4", "nodes": [1, 2, 5, 4]},
            "2": {"type": "S4", "nodes": [2, 3, 6, 5]},
            "3": {"type": "S4", "nodes": [4, 5, 8, 7]},
            "4": {"type": "S4", "nodes": [5, 6, 9, 8]},
        },
        "elastic": [[210000000000.0, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
    }
    assert check(tmp_path, text, "nafems-le6-skew-plate", m) == []

    defects = [
        text.replace(".01,\n*End Part", ".02,\n*End Part"),
        text.replace("LoadSurf,P,700", "LoadSurf,P,7000"),
        text.replace("AllShell,SPOS", "AllShell,SNEG"),
        text.replace("LoadSurf,P,700", "LoadSurf,P,-700"),
        text.replace("*Dsload\nLoadSurf,P,700", "*Dload\nAllShell,P,700"),
        text.replace("B,2,2", "B,1,2"),
        text.replace("Edges,3,3", "Edges,3,3\n5,3,3"),
    ]
    for defective in defects:
        assert check(tmp_path, defective, "nafems-le6-skew-plate", m)

    missing_center = deepcopy(m)
    missing_center["nodes"]["5"][0] += 0.01
    assert check(tmp_path, text, "nafems-le6-skew-plate", missing_center)

    wrong_geometry = deepcopy(m)
    wrong_geometry["nodes"]["9"][1] = 0.6
    assert check(tmp_path, text, "nafems-le6-skew-plate", wrong_geometry)

    wrong_normal = deepcopy(m)
    wrong_normal["elements"]["1"]["nodes"] = [1, 4, 5, 2]
    assert check(tmp_path, text, "nafems-le6-skew-plate", wrong_normal)


def test_nafems_le1_elliptic_membrane_contract(tmp_path):
    root2 = 2**0.5
    nodes = {
        "1": [2.0, 0.0, 0.0],
        "2": [3.25, 0.0, 0.0],
        "3": [0.0, 2.75, 0.0],
        "4": [0.0, 1.0, 0.0],
        "5": [2.625, 0.0, 0.0],
        "6": [3.25 / root2, 2.75 / root2, 0.0],
        "7": [0.0, 1.875, 0.0],
        "8": [2.0 / root2, 1.0 / root2, 0.0],
    }
    text = """*Heading
*Part, name=Membrane
*Node
1,2,0
2,3.25,0
3,0,2.75
4,0,1
5,2.625,0
6,2.29809703885628,1.94454364826301
7,0,1.875
8,1.41421356237310,.707106781186548
*Element,type=CPS8
1,1,2,3,4,5,6,7,8
*Elset,elset=All
1
*Solid Section,elset=All,material=Steel
.1,
*End Part
*Assembly,name=Assembly
*Instance,name=Membrane-1,part=Membrane
*End Instance
*Nset,nset=XSYM,instance=Membrane-1
3,4,7
*Nset,nset=YSYM,instance=Membrane-1
1,2,5
*Surface,type=ELEMENT,name=Outer
Membrane-1.All,S2
*End Assembly
*Material,name=Steel
*Elastic
210000000000,.3
*Boundary
XSYM,XSYMM
YSYM,YSYMM
*Step,name=Load,nlgeom=NO
*Static
1,1
*Dsload
Outer,P,-10000000
*End Step
"""
    m = {
        "nodes": nodes,
        "elements": {"1": {"type": "CPS8", "nodes": list(range(1, 9))}},
        "elastic": [[210000000000.0, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
    }
    assert check(tmp_path, text, "nafems-le1-elliptic-membrane", m) == []

    defects = [
        text.replace(".1,\n*End Part", ".2,\n*End Part"),
        text.replace("Outer,P,-10000000", "Outer,P,10000000"),
        text.replace("Outer,P,-10000000", "Outer,P,-9000000"),
        text.replace("YSYM,YSYMM", "YSYM,XSYMM"),
        text.replace("Membrane-1.All,S2", "Membrane-1.All,S4"),
    ]
    for defective in defects:
        assert check(tmp_path, defective, "nafems-le1-elliptic-membrane", m)

    wrong_geometry = deepcopy(m)
    wrong_geometry["nodes"]["8"] = [1.0, 0.3, 0.0]
    assert check(tmp_path, text, "nafems-le1-elliptic-membrane", wrong_geometry)

    wrong_element = deepcopy(m)
    wrong_element["elements"]["1"]["type"] = "CPS4"
    wrong_element["elements"]["1"]["nodes"] = [1, 2, 3, 4]
    assert check(tmp_path, text, "nafems-le1-elliptic-membrane", wrong_element)


def test_nafems_le10_thick_plate_contract(tmp_path):
    root2 = 2**0.5
    nodes = {
        "1": [2.0, 0.0, 0.0],
        "2": [3.25, 0.0, 0.0],
        "3": [0.0, 2.75, 0.0],
        "4": [0.0, 1.0, 0.0],
        "5": [2.0, 0.0, 0.6],
        "6": [3.25, 0.0, 0.6],
        "7": [0.0, 2.75, 0.6],
        "8": [0.0, 1.0, 0.6],
        "9": [2.625, 0.0, 0.0],
        "10": [3.25 / root2, 2.75 / root2, 0.0],
        "11": [0.0, 1.875, 0.0],
        "12": [2.0 / root2, 1.0 / root2, 0.0],
        "13": [2.625, 0.0, 0.6],
        "14": [3.25 / root2, 2.75 / root2, 0.6],
        "15": [0.0, 1.875, 0.6],
        "16": [2.0 / root2, 1.0 / root2, 0.6],
        "17": [2.0, 0.0, 0.3],
        "18": [3.25, 0.0, 0.3],
        "19": [0.0, 2.75, 0.3],
        "20": [0.0, 1.0, 0.3],
    }
    text = """*Heading
*Part, name=Plate
*Node
1,2,0,0
2,3.25,0,0
3,0,2.75,0
4,0,1,0
5,2,0,.6
6,3.25,0,.6
7,0,2.75,.6
8,0,1,.6
9,2.625,0,0
10,2.29809703885628,1.94454364826301,0
11,0,1.875,0
12,1.41421356237310,.707106781186548,0
13,2.625,0,.6
14,2.29809703885628,1.94454364826301,.6
15,0,1.875,.6
16,1.41421356237310,.707106781186548,.6
17,2,0,.3
18,3.25,0,.3
19,0,2.75,.3
20,0,1,.3
*Element,type=C3D20
1,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20
*Elset,elset=All
1
*Solid Section,elset=All,material=Steel
,
*End Part
*Assembly,name=Assembly
*Instance,name=Plate-1,part=Plate
*End Instance
*Nset,nset=XSYM,instance=Plate-1
3,4,7,8,11,15,19,20
*Nset,nset=YSYM,instance=Plate-1
1,2,5,6,9,13,17,18
*Nset,nset=Outer,instance=Plate-1
2,3,6,7,10,14,18,19
*Nset,nset=OuterMid,instance=Plate-1
18,19
*Elset,elset=AllSolid,instance=Plate-1
1
*End Assembly
*Material,name=Steel
*Elastic
210000000000,.3
*Boundary
XSYM,1,1
YSYM,2,2
Outer,1,2
OuterMid,3,3
*Step,name=Load,nlgeom=NO
*Static
1,1
*Dload
AllSolid,P2,1000000
*End Step
"""
    m = {
        "nodes": nodes,
        "elements": {"1": {"type": "C3D20", "nodes": list(range(1, 21))}},
        "elastic": [[210000000000.0, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
        "volume": 3.141592653589793 / 4 * (3.25 * 2.75 - 2.0) * 0.6,
    }
    assert check(tmp_path, text, "nafems-le10-thick-plate-pressure", m) == []

    defects = [
        text.replace("AllSolid,P2,1000000", "AllSolid,P2,900000"),
        text.replace("AllSolid,P2,1000000", "AllSolid,P1,1000000"),
        text.replace("OuterMid,3,3", "OuterMid,2,2"),
    ]
    for defective in defects:
        assert check(tmp_path, defective, "nafems-le10-thick-plate-pressure", m)

    missing_d = deepcopy(m)
    missing_d["nodes"]["5"][0] += 0.01
    assert check(tmp_path, text, "nafems-le10-thick-plate-pressure", missing_d)

    wrong_volume = deepcopy(m)
    wrong_volume["volume"] *= 0.9
    assert check(tmp_path, text, "nafems-le10-thick-plate-pressure", wrong_volume)


def test_nafems_le3_hemisphere_point_loads_contract(tmp_path):
    root2 = 2**0.5
    nodes = {
        "1": [10.0, 0.0, 0.0],
        "2": [10.0 / root2, 10.0 / root2, 0.0],
        "3": [0.0, 10.0, 0.0],
        "4": [0.0, 0.0, 10.0],
        "5": [9.238795325112868, 3.826834323650898, 0.0],
        "6": [3.826834323650898, 9.238795325112868, 0.0],
        "7": [0.0, 10.0 / root2, 10.0 / root2],
        "8": [10.0 / root2, 0.0, 10.0 / root2],
    }
    text = """*Heading
*Part, name=Hemisphere
*Node
1,10,0,0
2,7.071067811865475,7.071067811865475,0
3,0,10,0
4,0,0,10
5,9.238795325112868,3.826834323650898,0
6,3.826834323650898,9.238795325112868,0
7,0,7.071067811865475,7.071067811865475
8,7.071067811865475,0,7.071067811865475
*Element,type=S8R5
1,1,2,3,4,5,6,7,8
*Elset,elset=All
1
*Shell Section,elset=All,material=Steel
.04,
*End Part
*Assembly,name=Assembly
*Instance,name=Hemisphere-1,part=Hemisphere
*End Instance
*Nset,nset=XSYM,instance=Hemisphere-1
3,4,7
*Nset,nset=YSYM,instance=Hemisphere-1
1,4,8
*Nset,nset=A,instance=Hemisphere-1
1
*Nset,nset=C,instance=Hemisphere-1
3
*Nset,nset=E,instance=Hemisphere-1
4
*End Assembly
*Material,name=Steel
*Elastic,type=ISO
68250000000,.3
*Boundary
XSYM,XSYMM
YSYM,YSYMM
E,3,3
*Step,name=Load,nlgeom=NO
*Static
1,1
*Cload
A,1,2000
C,2,-2000
*End Step
"""
    m = {
        "nodes": nodes,
        "elements": {"1": {"type": "S8R5", "nodes": list(range(1, 9))}},
        "elastic": [[68.25e9, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
    }
    assert check(tmp_path, text, "nafems-le3-hemisphere-point-loads", m) == []

    implicit_elset = text.replace(
        "*Element,type=S8R5\n1,1,2,3,4,5,6,7,8\n*Elset,elset=All\n1\n",
        "*Element,type=S8R5,elset=All\n1,1,2,3,4,5,6,7,8\n",
    )
    assert check(tmp_path, implicit_elset, "nafems-le3-hemisphere-point-loads", m) == []

    defects = [
        text.replace(".04,\n*End Part", ".05,\n*End Part"),
        text.replace("68250000000,.3", "68000000000,.3"),
        text.replace("68250000000,.3", "68250000000,.29"),
        text.replace("A,1,2000", "A,1,1900"),
        text.replace("C,2,-2000", "C,2,2000"),
        text.replace("C,2,-2000", "C,1,-2000"),
        text.replace("XSYM,XSYMM", "XSYM,YSYMM"),
        text.replace("E,3,3\n", ""),
        text.replace("E,3,3", "E,3,3\nA,3,3"),
        text.replace("*Cload\nA,1,2000", "*Cload\nA,1,2000\nA,2,1"),
    ]
    for defective in defects:
        assert check(tmp_path, defective, "nafems-le3-hemisphere-point-loads", m)

    wrong_radius = deepcopy(m)
    wrong_radius["nodes"]["2"] = [7.0, 7.0, 0.0]
    assert check(tmp_path, text, "nafems-le3-hemisphere-point-loads", wrong_radius)

    missing_a = deepcopy(m)
    missing_a["nodes"]["1"] = [9.999, 0.0, 0.141417820659208]
    assert check(tmp_path, text, "nafems-le3-hemisphere-point-loads", missing_a)

    wrong_element = deepcopy(m)
    wrong_element["elements"]["1"]["type"] = "S4R"
    wrong_element["elements"]["1"]["nodes"] = [1, 2, 3, 4]
    assert check(tmp_path, text, "nafems-le3-hemisphere-point-loads", wrong_element)


def _z_section_shell_fixture():
    node_labels = {}
    nodes = {}
    elements = {}

    def section_point(s):
        if s <= 1.0:
            return -1.0, -1.0 + s
        if s <= 2.0:
            return -1.0 + 2.0 * (s - 1.0), 0.0
        return 1.0, s - 2.0

    def node(x, s):
        key = (float(x), float(s))
        if key not in node_labels:
            label = len(node_labels) + 1
            y, z = section_point(float(s))
            node_labels[key] = label
            nodes[str(label)] = [float(x), y, z]
        return node_labels[key]

    for xa, xb in ((0.0, 5.0), (5.0, 10.0)):
        xm = 0.5 * (xa + xb)
        for sa, sb in ((0.0, 1.0), (1.0, 2.0), (2.0, 3.0)):
            sm = 0.5 * (sa + sb)
            connectivity = [
                node(xa, sa),
                node(xb, sa),
                node(xb, sb),
                node(xa, sb),
                node(xm, sa),
                node(xb, sm),
                node(xm, sb),
                node(xa, sm),
            ]
            label = len(elements) + 1
            elements[str(label)] = {"type": "S8R", "nodes": connectivity}

    node_rows = "\n".join(
        f"{label},{point[0]:g},{point[1]:g},{point[2]:g}"
        for label, point in ((int(k), v) for k, v in nodes.items())
    )
    element_rows = "\n".join(
        f"{label},{','.join(str(n) for n in data['nodes'])}"
        for label, data in ((int(k), v) for k, v in elements.items())
    )
    fix = sorted(int(k) for k, p in nodes.items() if p[0] == 0.0)
    lower = [node_labels[(10.0, s)] for s in (0.0, 0.5, 1.0)]
    upper = [node_labels[(10.0, s)] for s in (2.0, 2.5, 3.0)]
    text = f"""*Heading
*Node
{node_rows}
*Element,type=S8R
{element_rows}
*Elset,elset=All
1,2,3,4,5,6
*Nset,nset=Fix
{",".join(str(n) for n in fix)}
*Shell Section,elset=All,material=Steel
.1,
*Material,name=Steel
*Elastic
210000000000,.3
*Boundary
Fix,ENCASTRE
*Step,name=Load,nlgeom=NO
*Static
1,1
*Cload
{lower[0]},3,-100000
{lower[1]},3,-400000
{lower[2]},3,-100000
{upper[0]},3,100000
{upper[1]},3,400000
{upper[2]},3,100000
*End Step
"""
    measurement = {
        "nodes": nodes,
        "elements": elements,
        "elastic": [[210.0e9, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
    }
    return text, measurement, node_labels, lower, upper


def test_nafems_le5_z_section_cantilever_contract(tmp_path):
    text, measurement, labels, lower, upper = _z_section_shell_fixture()
    case_id = "nafems-le5-z-section-cantilever"
    assert check(tmp_path, text, case_id, measurement) == []

    defects = [
        text.replace(".1,\n*Material", ".2,\n*Material"),
        text.replace("Fix,ENCASTRE", "Fix,1,5"),
        text.replace(f"{lower[0]},3,-100000", f"{lower[0]},3,-90000"),
        text.replace(f"{lower[0]},3,-100000", f"{lower[0]},2,-100000"),
        text.replace(
            "*End Step",
            f"*Cload\n{lower[0]},3,1000\n{upper[0]},3,1000\n*End Step",
        ),
        text.replace("*End Step", "*Dload\nAll,P,1\n*End Step"),
    ]
    for defective in defects:
        assert check(tmp_path, defective, case_id, measurement)

    missing_a = deepcopy(measurement)
    a_label = str(labels[(2.5, 0.0)])
    missing_a["nodes"][a_label][0] = 2.6
    assert check(tmp_path, text, case_id, missing_a)

    wrong_geometry = deepcopy(measurement)
    off_surface = str(labels[(5.0, 0.5)])
    wrong_geometry["nodes"][off_surface][1] = -0.9
    assert check(tmp_path, text, case_id, wrong_geometry)

    wrong_element = deepcopy(measurement)
    wrong_element["elements"]["1"]["type"] = "S4R"
    wrong_element["elements"]["1"]["nodes"] = wrong_element["elements"]["1"]["nodes"][
        :4
    ]
    assert check(tmp_path, text, case_id, wrong_element)


def _le2_cylindrical_shell_fixture():
    polar = {
        1: (10.0, 0.0),
        2: (10.0, 0.125),
        3: (10.0, 0.25),
        4: (10.0, 0.375),
        5: (10.0, 0.5),
        6: (7.5, 0.5),
        7: (5.0, 0.5),
        8: (2.5, 0.5),
        9: (0.0, 0.5),
        10: (0.0, 0.375),
        11: (0.0, 0.25),
        12: (0.0, 0.125),
        13: (0.0, 0.0),
        14: (2.5, 0.0),
        15: (5.0, 0.0),
        16: (7.5, 0.0),
        52: (8.33335, 0.275),
        54: (5.83335, 0.4),
        56: (3.33335, 0.275),
        58: (5.83335, 0.15),
        101: (6.6667, 0.3),
    }
    nodes = {
        str(label): [
            math.cos(math.radians(theta)),
            math.sin(math.radians(theta)),
            z,
        ]
        for label, (theta, z) in polar.items()
    }
    elements = {
        "1": {"type": "S8R", "nodes": [1, 3, 101, 15, 2, 52, 58, 16]},
        "2": {"type": "S8R", "nodes": [3, 5, 7, 101, 4, 6, 54, 52]},
        "3": {"type": "S8R", "nodes": [101, 7, 9, 11, 54, 8, 10, 56]},
        "4": {"type": "S8R", "nodes": [15, 101, 11, 13, 58, 56, 12, 14]},
    }
    text = """*HEADING
*PREPRINT,ECHO=YES,HISTORY=NO,MODEL=NO
*NODE
1,1.,10.,0.0,1.,0.,0.
5,1.,10.,0.5,1.,0.,0.
9,1.,0.,0.5,1.,0.,0.
13,1.,0.,0.0,1.,0.,0.
17,1.,10.,0.0,1.,0.,0.
101,1.,6.6667,0.3,1.,0.,0.
*NGEN,NSET=EDGECD
1,5
*NGEN,NSET=EDGEBC
5,9
*NGEN,NSET=EDGEAB
9,13
*NGEN,NSET=EDGEAD1
13,17
*NSET,NSET=EDGEAD
1,13,14,15,16
*NSET,NSET=OUTER
EDGECD,EDGEBC,EDGEAB,EDGEAD
*NGEN,NSET=INNER
1,101,50
3,101,49
5,101,48
7,101,47
9,101,46
11,101,45
13,101,44
15,101,43
*NSET,NSET=ALL
INNER,OUTER
*NMAP,NSET=ALL,TYPE=CYLINDRICAL
0.,0.,0.,0.,0.,1.
1.,0.,0.
*ORIENTATION,NAME=ORI,SYSTEM=CYLINDRICAL
0.,0.,0.,0.,0.,1.
1,0.
*ELEMENT,TYPE=S8R,ELSET=ALLELS
1,1,3,101,15,2,52,58,16
2,3,5,7,101,4,6,54,52
3,101,7,9,11,54,8,10,56
4,15,101,11,13,58,56,12,14
*MATERIAL,NAME=MAT
*ELASTIC,TYPE=ISOTROPIC
2.1E11,0.3
*SHELL SECTION,MATERIAL=MAT,ELSET=ALLELS,ORIENTATION=ORI
0.01,
*TRANSFORM,TYPE=C,NSET=ALL
0.,0.,0.,0.,0.,1.
*BOUNDARY
EDGEAB,1,6
EDGEAD,ZSYMM
EDGEBC,ZSYMM
*STEP,PERTURBATION
*STATIC
*CLOAD
1,6,0.041667E3
2,6,0.166667E3
3,6,0.083333E3
4,6,0.166667E3
5,6,0.041667E3
*END STEP
"""
    measurement = {
        "nodes": nodes,
        "elements": elements,
        "elastic": [[210.0e9, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Step-1"],
    }
    return text, measurement


def test_nafems_le2_cylindrical_shell_patch_contract(tmp_path):
    text, measurement = _le2_cylindrical_shell_fixture()
    case_id = "nafems-le2-cylindrical-shell-patch"
    assert check(tmp_path, text, case_id, measurement) == []

    defects = [
        text.replace("2.1E11,0.3", "2.0E11,0.3"),
        text.replace("0.01,\n*TRANSFORM", "0.02,\n*TRANSFORM"),
        text.replace("EDGEAB,1,6", "EDGEAB,1,5"),
        text.replace("1,6,0.041667E3", "1,6,0.031667E3"),
        text.replace("1,6,0.041667E3", "101,6,0.041667E3"),
        text.replace("*TRANSFORM,TYPE=C,NSET=ALL", "*TRANSFORM,TYPE=R,NSET=ALL"),
        text.replace(
            "*ORIENTATION,NAME=ORI,SYSTEM=CYLINDRICAL\n0.,0.,0.,0.,0.,1.",
            "*ORIENTATION,NAME=ORI,SYSTEM=CYLINDRICAL\n0.,0.,0.,0.,1.,0.",
        ),
    ]
    for defective in defects:
        assert check(tmp_path, defective, case_id, measurement)


def _le4_axisymmetric_shell_fixture():
    r_mid = math.sqrt(1.0 + 0.5**2)
    r_top = math.sqrt(2.0)
    text = f"""*Heading
*Node
1,{r_top:.15g},1.0
2,{r_mid:.15g},0.5
3,1.0,0.0
*Element,type=SAX1,elset=All
1,1,2
2,2,3
*Shell Section,elset=All,material=Steel
0.01,
*Material,name=Steel
*Elastic
210000000000,0.3
*Boundary
3,2
*Step,name=Load,nlgeom=NO
*Static
1,1
*Dload
All,P,1000000
*End Step
"""
    measurement = {
        "nodes": {
            "1": [r_top, 1.0, 0.0],
            "2": [r_mid, 0.5, 0.0],
            "3": [1.0, 0.0, 0.0],
        },
        "elements": {
            "1": {"type": "SAX1", "nodes": [1, 2]},
            "2": {"type": "SAX1", "nodes": [2, 3]},
        },
        "elastic": [[210.0e9, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
    }
    return text, measurement


def test_nafems_le4_axisymmetric_hyperbolic_shell_contract(tmp_path):
    text, measurement = _le4_axisymmetric_shell_fixture()
    case_id = "nafems-le4-axisymmetric-hyperbolic-shell"
    assert check(tmp_path, text, case_id, measurement) == []

    partial_pressure = text.replace(
        "*Shell Section,elset=All,material=Steel",
        "*Elset,elset=Half\n1\n*Shell Section,elset=All,material=Steel",
    ).replace("All,P,1000000", "Half,P,1000000")
    defects = [
        text.replace("210000000000,0.3", "200000000000,0.3"),
        text.replace("0.01,\n*Material", "0.02,\n*Material"),
        text.replace("3,2\n*Step", "2,2\n*Step"),
        text.replace("All,P,1000000", "All,P,900000"),
        text.replace("All,P,1000000", "All,P,-1000000"),
        partial_pressure,
    ]
    for defective in defects:
        assert check(tmp_path, defective, case_id, measurement)

    wrong_element = deepcopy(measurement)
    wrong_element["elements"]["1"]["type"] = "CAX4"
    assert check(tmp_path, text, case_id, wrong_element)

    off_hyperbola = deepcopy(measurement)
    off_hyperbola["nodes"]["2"] = [1.2, 0.5, 0.0]
    assert check(tmp_path, text, case_id, off_hyperbola)

    missing_k = deepcopy(measurement)
    missing_k["nodes"]["3"] = [math.sqrt(1.0 + 0.01**2), 0.01, 0.0]
    assert check(tmp_path, text, case_id, missing_k)


def _le7_axisymmetric_sphere_cylinder_fixture():
    sphere_mid = math.sqrt(0.75)
    text = f"""*Heading
*Node
1,0.0,2.5
2,{sphere_mid:.15g},2.0
3,1.0,1.5
4,1.0,1.4034
5,1.0,0.0
*Element,type=SAX1,elset=All
1,1,2
2,2,3
3,3,4
4,4,5
*Shell Section,elset=All,material=Steel
0.025,
*Material,name=Steel
*Elastic
210000000000,0.3
*Boundary
1,1
1,6
5,2
*Step,name=Load,nlgeom=NO
*Static
1,1
*Dload
All,P,1000000
*End Step
"""
    measurement = {
        "nodes": {
            "1": [0.0, 2.5, 0.0],
            "2": [sphere_mid, 2.0, 0.0],
            "3": [1.0, 1.5, 0.0],
            "4": [1.0, 1.4034, 0.0],
            "5": [1.0, 0.0, 0.0],
        },
        "elements": {
            "1": {"type": "SAX1", "nodes": [1, 2]},
            "2": {"type": "SAX1", "nodes": [2, 3]},
            "3": {"type": "SAX1", "nodes": [3, 4]},
            "4": {"type": "SAX1", "nodes": [4, 5]},
        },
        "elastic": [[210.0e9, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
    }
    return text, measurement


def test_nafems_le7_axisymmetric_sphere_cylinder_contract(tmp_path):
    text, measurement = _le7_axisymmetric_sphere_cylinder_fixture()
    case_id = "nafems-le7-axisymmetric-cylinder-sphere"
    assert check(tmp_path, text, case_id, measurement) == []

    partial_pressure = text.replace(
        "*Shell Section,elset=All,material=Steel",
        "*Elset,elset=Half\n1,2\n*Shell Section,elset=All,material=Steel",
    ).replace("All,P,1000000", "Half,P,1000000")
    defects = [
        text.replace("210000000000,0.3", "200000000000,0.3"),
        text.replace("0.025,\n*Material", "0.02,\n*Material"),
        text.replace("1,6\n5,2", "1,5\n5,2"),
        text.replace("All,P,1000000", "All,P,900000"),
        text.replace("All,P,1000000", "All,P,-1000000"),
        partial_pressure,
    ]
    for defective in defects:
        assert check(tmp_path, defective, case_id, measurement)

    wrong_element = deepcopy(measurement)
    wrong_element["elements"]["1"]["type"] = "CAX4"
    assert check(tmp_path, text, case_id, wrong_element)

    off_sphere = deepcopy(measurement)
    off_sphere["nodes"]["2"] = [0.8, 2.0, 0.0]
    assert check(tmp_path, text, case_id, off_sphere)

    missing_d = deepcopy(measurement)
    missing_d["nodes"]["4"] = [1.0, 1.4, 0.0]
    assert check(tmp_path, text, case_id, missing_d)


def _le8_axisymmetric_crown_knuckle_fixture():
    text = """*Heading
*Node
1,0.0,0.5
2,0.150763,0.5505748
3,0.1875,0.562613
4,0.25,0.500113
5,0.25,0.0
*Element,type=SAX1,elset=All
1,1,2
2,2,3
3,3,4
4,4,5
*Shell Section,elset=All,material=Steel
0.01,
*Material,name=Steel
*Elastic
210000000000,0.3
*Boundary
1,1
1,6
5,2
*Step,name=Load,nlgeom=NO
*Static
1,1
*Dload
All,P,1000000
*End Step
"""
    measurement = {
        "nodes": {
            "1": [0.0, 0.5, 0.0],
            "2": [0.150763, 0.5505748, 0.0],
            "3": [0.1875, 0.562613, 0.0],
            "4": [0.25, 0.500113, 0.0],
            "5": [0.25, 0.0, 0.0],
        },
        "elements": {
            "1": {"type": "SAX1", "nodes": [1, 2]},
            "2": {"type": "SAX1", "nodes": [2, 3]},
            "3": {"type": "SAX1", "nodes": [3, 4]},
            "4": {"type": "SAX1", "nodes": [4, 5]},
        },
        "elastic": [[210.0e9, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
    }
    return text, measurement


def test_nafems_le8_axisymmetric_crown_knuckle_contract(tmp_path):
    text, measurement = _le8_axisymmetric_crown_knuckle_fixture()
    case_id = "nafems-le8-axisymmetric-crown-knuckle-shell"
    assert check(tmp_path, text, case_id, measurement) == []

    partial_pressure = text.replace(
        "*Shell Section,elset=All,material=Steel",
        "*Elset,elset=Half\n1,2\n*Shell Section,elset=All,material=Steel",
    ).replace("All,P,1000000", "Half,P,1000000")
    defects = [
        text.replace("210000000000,0.3", "200000000000,0.3"),
        text.replace("0.01,\n*Material", "0.02,\n*Material"),
        text.replace("1,6\n5,2", "1,5\n5,2"),
        text.replace("All,P,1000000", "All,P,900000"),
        text.replace("All,P,1000000", "All,P,-1000000"),
        partial_pressure,
    ]
    for defective in defects:
        assert check(tmp_path, defective, case_id, measurement)

    wrong_element = deepcopy(measurement)
    wrong_element["elements"]["1"]["type"] = "CAX4"
    assert check(tmp_path, text, case_id, wrong_element)

    off_crown = deepcopy(measurement)
    off_crown["nodes"]["1"] = [0.01, 0.5, 0.0]
    assert check(tmp_path, text, case_id, off_crown)

    missing_d = deepcopy(measurement)
    missing_d["nodes"]["2"] = [0.15, 0.55, 0.0]
    assert check(tmp_path, text, case_id, missing_d)


def _le9_branched_axisymmetric_shell_fixture():
    sphere_mid_r = 0.38268343236509
    sphere_mid_z = 0.783227467488886
    text = f"""*Heading
*Node
1,0.707107,0.0
2,0.707107,0.875
3,0.707107,1.0
4,0.707107,1.125
5,0.707107,2.0
101,0.0,0.707107
102,{sphere_mid_r:.15g},{sphere_mid_z:.15g}
103,0.707107,1.0
*Element,type=SAX1,elset=All
1,1,2
2,2,3
3,3,4
4,4,5
101,101,102
102,102,103
*Elset,elset=Upper
3,4
*Elset,elset=Sphere
101,102
*MPC
TIE,3,103
*Shell Section,elset=All,material=Steel,section integration=GAUSS
0.01,3
*Material,name=Steel
*Elastic
210000000000,0.3
*Boundary
1,1
1,2
1,6
*Step,name=Load,nlgeom=NO
*Static
1,1
*Dload
Upper,P,-1000000
Sphere,P,-1000000
*End Step
"""
    measurement = {
        "nodes": {
            "1": [0.707107, 0.0, 0.0],
            "2": [0.707107, 0.875, 0.0],
            "3": [0.707107, 1.0, 0.0],
            "4": [0.707107, 1.125, 0.0],
            "5": [0.707107, 2.0, 0.0],
            "101": [0.0, 0.707107, 0.0],
            "102": [sphere_mid_r, sphere_mid_z, 0.0],
            "103": [0.707107, 1.0, 0.0],
        },
        "elements": {
            "1": {"type": "SAX1", "nodes": [1, 2]},
            "2": {"type": "SAX1", "nodes": [2, 3]},
            "3": {"type": "SAX1", "nodes": [3, 4]},
            "4": {"type": "SAX1", "nodes": [4, 5]},
            "101": {"type": "SAX1", "nodes": [101, 102]},
            "102": {"type": "SAX1", "nodes": [102, 103]},
        },
        "elastic": [[210.0e9, 0.3]],
        "assembly_node_count": 0,
        "step_names": ["Load"],
    }
    return text, measurement


def test_nafems_le9_branched_axisymmetric_shell_contract(tmp_path):
    text, measurement = _le9_branched_axisymmetric_shell_fixture()
    case_id = "nafems-le9-branched-axisymmetric-shell"
    assert check(tmp_path, text, case_id, measurement) == []
    assert model_identity_errors(get_case(case_id), measurement) == []

    defects = [
        text.replace("*MPC\nTIE,3,103\n", ""),
        text.replace("TIE,3,103", "TIE,3,5"),
        text.replace("section integration=GAUSS", "section integration=SIMPSON"),
        text.replace("0.01,3", "0.01,5"),
        text.replace("0.01,3", "0.02,3"),
        text.replace("210000000000,0.3", "200000000000,0.3"),
        text.replace("1,6\n*Step", "1,5\n*Step"),
        text.replace("Upper,P,-1000000", "Upper,P,-900000"),
        text.replace("Upper,P,-1000000", "All,P,-1000000"),
        text.replace("Sphere,P,-1000000", "Sphere,P,1000000"),
    ]
    for defective in defects:
        assert check(tmp_path, defective, case_id, measurement)

    missing_c_pair = deepcopy(measurement)
    missing_c_pair["nodes"]["103"] = [0.706, 1.0, 0.0]
    assert model_identity_errors(get_case(case_id), missing_c_pair)

    crossed_branch = deepcopy(measurement)
    crossed_branch["elements"]["102"]["nodes"] = [102, 4]
    assert model_identity_errors(get_case(case_id), crossed_branch)


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


def _le11_thermal_solid_fixture():
    root2 = math.sqrt(2.0) / 2.0
    points = {
        1: (1.0, 0.0, 0.0),
        2: (1.4, 0.0, 0.0),
        3: (0.7071, 0.0, 0.7071),
        4: (1.2124, 0.0, 0.7),
        5: (1.1062, 0.0, 1.045),
        6: (1.0, 0.0, 1.39),
        7: (0.7071, 0.0, 1.79),
        8: (1.0, 0.0, 1.79),
        9: (0.0, 1.0, 0.0),
        10: (0.0, 1.4, 0.0),
        11: (0.0, 0.7071, 0.7071),
        12: (0.0, 1.2124, 0.7),
        13: (0.0, 1.1062, 1.045),
        14: (0.0, 1.0, 1.39),
        15: (0.0, 0.7071, 1.79),
        16: (0.0, 1.0, 1.79),
        17: (1.1 * root2, 1.1 * root2, 0.35),
        18: (0.9 * root2, 0.9 * root2, 1.0),
        19: (0.9 * root2, 0.9 * root2, 0.9),
        20: (0.85 * root2, 0.85 * root2, 1.5),
    }
    node_rows = "\n".join(
        f"{label},{point[0]:.12g},{point[1]:.12g},{point[2]:.12g}"
        for label, point in points.items()
    )
    temperature_rows = "\n".join(
        f"{label},{math.hypot(point[0], point[1]) + point[2]:.12g}"
        for label, point in points.items()
    )
    text = f"""*Heading
*Node
{node_rows}
*Element,type=C3D20,elset=All
1,{",".join(str(label) for label in points)}
*Nset,nset=Y0
1,2,3,4,5,6,7,8
*Nset,nset=X0
9,10,11,12,13,14,15,16
*Nset,nset=Z0
1,2,9,10
*Nset,nset=TOP
7,8,15,16
*Solid Section,elset=All,material=Mat
*Material,name=Mat
*Elastic
210e9,.3
*Expansion
2.3e-4
*Boundary
Y0,2
X0,1
Z0,3
TOP,3
*Step,name=Thermal,nlgeom=NO
*Static
*Temperature
{temperature_rows}
*End Step
"""
    measurement = {
        "nodes": {str(label): list(point) for label, point in points.items()},
        "elements": {
            "1": {"type": "C3D20", "nodes": list(points)},
        },
        "elastic": [[210.0e9, 0.3]],
        "expansion": [[2.3e-4]],
        "assembly_node_count": 0,
        "step_names": ["Thermal"],
    }
    return text, measurement


def test_nafems_le11_thermal_solid_contract(tmp_path):
    text, measurement = _le11_thermal_solid_fixture()
    case_id = "nafems-le11-thermal-solid"
    assert check(tmp_path, text, case_id, measurement) == []
    assert model_identity_errors(get_case(case_id), measurement) == []

    defects = (
        text.replace("2.3e-4", "2.0e-4", 1),
        text.replace("1,1\n2,1.4", "1,1.1\n2,1.4", 1),
        text.replace("type=C3D20", "type=C3D8"),
        text.replace("TOP,3\n", ""),
        text.replace("*End Step", "*Cload\n1,1,1\n*End Step"),
        text.replace("210e9,.3", "200e9,.3"),
    )
    for index, defective in enumerate(defects, start=1):
        assert defective != text, index
        assert check(tmp_path, defective, case_id, measurement), index

    wrong_expansion = deepcopy(measurement)
    wrong_expansion["expansion"] = [[2.0e-4]]
    assert model_identity_errors(get_case(case_id), wrong_expansion)


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
    for p in sorted(root.glob("*.py")):
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
