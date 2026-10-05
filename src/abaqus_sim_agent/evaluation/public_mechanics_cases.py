"""公开力学问题的 Abaqus 端到端合同；不是产品建模 IR。"""

import hashlib
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter

from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.evaluation.case_metadata import CaseMetadata, SourceRef

PUBLIC_MECHANICS_PROTOCOL = "public-mechanics-e2e-v27-bounded-feedback"

# 物理Case身份决定评分范围；标签和载荷工况不增加权重。
BASIC_LINEAR_CASE_IDS = (
    "cantilever-uniform-load",
    "circular-shaft-pure-torsion",
    "thick-cylinder-internal-pressure",
    "plate-with-hole-tension",
    "solid-cantilever-directional-bending",
    "nafems-le1-elliptic-membrane",
    "nafems-le2-cylindrical-shell-patch",
    "nafems-le3-hemisphere-point-loads",
    "nafems-le4-axisymmetric-hyperbolic-shell",
    "nafems-le5-z-section-cantilever",
    "nafems-le6-skew-plate",
    "nafems-le7-axisymmetric-cylinder-sphere",
    "nafems-le8-axisymmetric-crown-knuckle-shell",
    "nafems-le9-branched-axisymmetric-shell",
    "nafems-le10-thick-plate-pressure",
)


class Metric(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    target: float
    relative_tolerance: float = Field(gt=0)
    absolute_tolerance: float = Field(default=1e-8, gt=0)
    meaning: str


class MechanicsCase(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    case_id: str
    family: Literal[
        "truss",
        "beam",
        "shaft",
        "plane_stress",
        "elliptic_membrane",
        "thick_plate",
        "axisymmetric_pressure",
        "axisymmetric_shell",
        "skew_plate",
        "hemisphere_shell",
        "z_section_shell",
        "cylindrical_shell_patch",
        "thermal_solid",
        "hole",
        "solid_beam",
    ]
    description: str
    metadata: CaseMetadata
    adaptation: str
    length: float
    width: float
    height: float
    young: float
    poisson: float
    load: float
    inner_radius: float = 0.0
    inner_semi_axis_x: float = 0.0
    inner_semi_axis_y: float = 0.0
    skew_angle_degrees: float = 0.0
    radius: float = 0.0
    arc_angle_degrees: float = 0.0
    sample_angle_degrees: float = 0.0
    sample_axial_position: float = 0.0
    sample_radius: float = 0.0
    crown_radius: float = 0.0
    knuckle_radius: float = 0.0
    branch_sphere_radius: float = 0.0
    branch_center_axial_position: float = 0.0
    thermal_expansion: float = 0.0
    axisymmetric_shell_profile: (
        Literal[
            "hyperbolic",
            "sphere_cylinder",
            "crown_knuckle_cylinder",
            "branched_sphere_cylinder",
        ]
        | None
    ) = None
    segments: tuple[tuple[float, float], ...] = ()
    distributed: bool = False
    refinement: bool = False
    metrics: dict[str, Metric]

    def physical_description(self) -> str:
        if self.family == "thermal_solid":
            return self.description
        if self.family == "solid_beam":
            return (
                f"in-lbf-psi 单位。3D实体梁：0 ≤ x ≤ {self.length:g} in，"
                f"0 ≤ y ≤ {self.width:g} in，0 ≤ z ≤ {self.height:g} in。"
                f"E={self.young:g} psi、nu={self.poisson:g}；x=0全固定。"
                f"依次建立+X、+Y、+Z端面合力各{self.load:g} lbf的三个独立线性载荷工况。"
            )
        return self.description

    def run_budget(self) -> CodingRunBudget:
        return CodingRunBudget(
            max_clarifications=0,
            max_model_requests=200,
            max_api_lookup_calls=None,
            max_concept_lookup_calls=None,
            max_diagnostic_retrieval_calls=None,
            max_static_preflight_calls=None,
            max_abaqus_calls=80,
            max_solver_calls=10,
            wall_time_seconds=5400.0,
        )

    def goal(self) -> str:
        metric_text = "\n".join(
            f"- {name}: {m.meaning}。" for name, m in self.metrics.items()
        )
        extra = (
            "必须保存粗、细两套实际网格及结果：model_coarse.inp/model_coarse.odb 与 model.inp/model.odb；"
            "说明网格如何加密，节点数仅作辅助检查。两者物理问题不变，目标位移或孔表面应力相对细网格的变化不超过1%。"
            "这只是本题的加密一致性检查，不是通用收敛阈值。"
            if self.refinement
            else "自行检查目标量的网格收敛性，在report.md记录判断依据；必要时加密网格。"
        )
        return f"""使用 Abaqus 2024 在空工作区自主完成以下完整仿真，而非仅写理论分析。
{self.physical_description()}
题设的线弹性、小变形、静力假设固定，使用 Abaqus/Standard Static General、NLGEOM=OFF。
{self.adaptation}
单元和网格在本题允许的表示范围内自行选择，不得改变题设参数、载荷、约束或目标量来拟合参考。
所有必需物理输入已给全；接口/网格等实现选择可在预算内查证，不用请求用户选择 API。
建模脚本 build_model.py 只能生成 model.cae 和 INP，不能提交 Job；求解必须用独立 solver 工具。
使用独立 extract_results.py 从本次 ODB 读取结果，写 results.json；不得硬编码参考结果。
保留所需 U/RF/S 场；梁/圆轴还需能读取 UR/RM（来自 U/RF 输出），3D实体题须请求 EVOL 供宿主核对体积。
{extra}
当前独立ODB读取器要求单个实例；集合名称、INP分块及API写法自行选择。装配、约束和载荷的实现说明供脚本审查。
按题给全局坐标建立几何以便独立取数，使用常数各向同性Elastic；梁截面的物理尺寸和方向保持题设。
results.json 的顶层数值字段（含方向；每项必须来自 ODB 或由其截面合力恢复）为：
{metric_text}
同时交付简短 report.md，说明模型选择、读取定义、网格收敛检查与仍存在的限制。
只有脚本、CAE/INP/ODB、JSON 和实际三阶段证据齐全后才请求宿主验收。
首次完成声明即计一次提交，缺失交付也计入；最多三次提交、两轮验收反馈，所有阶段共用每题累计预算。
提交前自主调试不计验收反馈轮数，但正常消耗预算。验收通过即停止；不能继续时明确报告失败。
宿主以独立ODB数值、反力、几何材料尺度及收敛结果为主要验收依据；不提供参考答案及相对答案的偏差。
完整INP检查是建议，不因实现写法不同直接判错；脚本审查由评测者单独记录，不由Agent自我认证。
"""


def _m(target: float, meaning: str, tol: float = 0.01) -> Metric:
    return Metric(target=float(target), relative_tolerance=tol, meaning=meaning)


class _Common(TypedDict):
    young: float
    poisson: float
    length: float


def mechanics_cases() -> tuple[MechanicsCase, ...]:
    axial = "经典轴向杆关系；参数沿用 foundation_public_mechanics_v1，Abaqus 端到端补充 nu=0.3。"
    beam = "经典 Euler–Bernoulli 悬臂梁参考；参数沿用 foundation_public_mechanics_v1。"
    common: _Common = {"young": 200000.0, "poisson": 0.3, "length": 1000.0}
    return (
        MechanicsCase(
            **common,
            case_id="uniform-axial-bar",
            family="truss",
            width=0.0,
            height=0.0,
            load=10000.0,
            segments=((1000.0, 100.0),),
            metadata=CaseMetadata(
                description="检查轴向载荷、伸长与反力和应力提取。",
                tags=("axial-load", "displacement", "reaction-balance"),
                source=SourceRef(
                    kind="project_analytic",
                    reference=axial,
                    url=None,
                    document_version=None,
                ),
            ),
            description="mm-N-MPa 单位。一维轴向杆沿 X 从0到1000 mm，A=100 mm²，E=200000 MPa、nu=0.3。左端轴向固定，右端+X 10000 N。",
            adaptation="允许 T3D2/T3D3。所有杆节点 U2=U3=0，仅代表一维轴向运动；U1 仅在左端约束。截面积是全部截面物理输入，不另猜三维形状。",
            metrics={
                "tip_u1": _m(0.5, "右端U1/mm", 0.001),
                "reaction_x": _m(-10000, "左端RF1合力/N", 0.0001),
                "mean_s11": _m(100, "积分点轴向S11均值/MPa", 0.001),
            },
        ),
        MechanicsCase(
            **common,
            case_id="stepped-axial-bar",
            family="truss",
            width=0.0,
            height=0.0,
            load=10000.0,
            segments=((400.0, 100.0), (600.0, 200.0)),
            metadata=CaseMetadata(
                description="检查分段截面、界面位移连续性及各段应力。",
                tags=(
                    "axial-load",
                    "section-assignment",
                    "displacement",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="project_analytic",
                    reference=axial,
                    url=None,
                    document_version=None,
                ),
            ),
            description="mm-N-MPa 单位。一维轴向杆：x=0..400 mm 的A=100 mm²，x=400..1000 mm 的A=200 mm²，E=200000 MPa、nu=0.3。x=400共节点连续连接；左端轴向固定，右端+X 10000 N。",
            adaptation="允许 T3D2/T3D3；不能让一个单元跨越x=400截面界面。所有节点U2=U3=0，U1只约束左端。不用平均截面积替代分段。",
            metrics={
                "tip_u1": _m(0.35, "右端U1/mm", 0.001),
                "interface_u1": _m(0.2, "x=400界面U1/mm", 0.001),
                "reaction_x": _m(-10000, "左端RF1/N", 0.0001),
                "segment_1_s11": _m(100, "第一段积分点轴向应力均值/MPa", 0.001),
                "segment_2_s11": _m(50, "第二段积分点轴向应力均值/MPa", 0.001),
            },
        ),
        MechanicsCase(
            **common,
            case_id="cantilever-tip-load",
            family="beam",
            width=20.0,
            height=50.0,
            load=1000.0,
            metadata=CaseMetadata(
                description="检查端载梁截面方向、挠度、转角及反力矩。",
                tags=(
                    "bending",
                    "section-orientation",
                    "displacement",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="project_analytic",
                    reference=beam,
                    url=None,
                    document_version=None,
                ),
            ),
            description="mm-N-MPa 单位。沿X长1000 mm的三维wire梁，矩形截面全局Y向高50 mm、Z向宽20 mm，E=200000 MPa、nu=.3；左端固支全部梁自由度，右端-Y 1000 N。",
            adaptation="允许 B31/B32 梁；自行设置正确截面方向。根部名义弯曲应力定义为 abs(左端RM3)*(50/2)/(20*50^3/12)，不是取约束角点实体应力峰值。RF输出对应的RM、U对应的UR须按实际ODB字段读取。",
            metrics={
                "tip_u2": _m(-8, "右端U2/mm"),
                "tip_ur3": _m(-0.012, "右端UR3/rad"),
                "reaction_y": _m(1000, "左端RF2/N", 0.0001),
                "reaction_mz": _m(1e6, "左端RM3/Nmm", 0.0001),
                "root_bending_stress": _m(
                    120, "由ODB根部力矩恢复的名义弯曲应力/MPa", 0.001
                ),
            },
        ),
        MechanicsCase(
            **common,
            case_id="cantilever-uniform-load",
            family="beam",
            width=20.0,
            height=50.0,
            load=1.0,
            distributed=True,
            metadata=CaseMetadata(
                description="检查真实均布载荷、梁挠度与反力矩，区分同合力端载。",
                tags=(
                    "distributed-load",
                    "bending",
                    "displacement",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="project_analytic",
                    reference=beam,
                    url=None,
                    document_version=None,
                ),
            ),
            description="mm-N-MPa 单位。沿X长1000 mm的三维wire梁，矩形截面Y向高50 mm、Z向宽20 mm，E=200000 MPa、nu=.3；左端全固支，全跨度-Y均布线载荷1 N/mm。",
            adaptation="允许 B31/B32；使用真正的均布线载荷，不以端部集中力替代。截面方向自行实现；根部名义弯曲应力由abs(RM3)*25/(20*50^3/12)恢复，RM来自ODB而非参考常数。",
            metrics={
                "tip_u2": _m(-3, "右端U2/mm"),
                "tip_ur3": _m(-0.004, "右端UR3/rad"),
                "reaction_y": _m(1000, "左端RF2/N", 0.0001),
                "reaction_mz": _m(5e5, "左端RM3/Nmm", 0.0001),
                "root_bending_stress": _m(
                    60, "由ODB力矩恢复的根部名义弯曲应力/MPa", 0.001
                ),
            },
        ),
        MechanicsCase(
            **common,
            case_id="circular-shaft-pure-torsion",
            family="shaft",
            width=20.0,
            height=0.0,
            load=100000.0,
            metadata=CaseMetadata(
                description="检查扭矩方向、转角及由ODB反力矩恢复的名义剪应力。",
                tags=("torsion", "displacement", "reaction-balance"),
                source=SourceRef(
                    kind="project_analytic",
                    reference="Saint-Venant circular-shaft torsion: phi=T*L/(G*J), tau_max=T*R/J.",
                    url=None,
                    document_version=None,
                ),
            ),
            description=(
                "mm-N-MPa 单位。沿X长1000 mm的圆截面wire轴，直径20 mm，"
                "E=200000 MPa、nu=0.3；左端固支，右端施加绕+X的100000 Nmm纯扭矩。"
            ),
            adaptation=(
                "允许B31/B32，使用实心圆截面；右端只施加绕X轴的集中力矩，不叠加横向力或轴力。"
                "表面名义剪应力由abs(左端RM1)*(d/2)/(pi*d^4/32)从ODB反力矩恢复，"
                "不是把解析参考值直接写入results.json。"
            ),
            metrics={
                "tip_ur1": _m(0.08276057040778557, "右端UR1/rad", 0.01),
                "reaction_mx": _m(-100000.0, "左端RM1/Nmm", 0.0001),
                "surface_shear_nominal": _m(
                    63.66197723675813,
                    "由ODB反力矩恢复的圆轴表面名义剪应力/MPa",
                    0.001,
                ),
            },
        ),
        MechanicsCase(
            case_id="plane-stress-uniform-tension",
            family="plane_stress",
            length=100.0,
            width=20.0,
            height=2.0,
            young=200000.0,
            poisson=0.3,
            load=0.05,
            metadata=CaseMetadata(
                description="检查二维轴向变形、自由收缩边界及均匀应力。",
                tags=("axial-load", "displacement", "reaction-balance"),
                source=SourceRef(
                    kind="project_analytic",
                    reference="Uniform plane-stress extension: epsilon_x=delta/L, "
                    "sigma_11=E*epsilon_x, resultant=sigma_11*thickness*width.",
                    url=None,
                    document_version=None,
                ),
            ),
            description=(
                "mm-N-MPa 单位。二维平面应力矩形板：x=0..100 mm、y=0..20 mm、厚度2 mm，"
                "E=200000 MPa、nu=0.3。左边界仅约束U1，左下角额外约束U2去除刚体平移；"
                "右边界施加U1=+0.05 mm，其他方向自由。"
            ),
            adaptation=(
                "必须使用完整二维plane-stress连续体，不用3D实体、梁、壳或平面应变替代；"
                "允许CPS4/CPS4R/CPS8/CPS8R。厚度必须为2 mm。"
                "本题载荷就是右边界规定U1=+0.05 mm，不另加节点力、压力或面力；"
                "不得把整个左边界U2锁死而抑制泊松收缩。"
            ),
            metrics={
                "tip_u1": _m(0.05, "右边界U1均值/mm", 0.0001),
                "reaction_x": _m(-4000.0, "左边界RF1总和/N", 0.001),
                "mean_s11": _m(100.0, "积分点S11均值/MPa", 0.001),
            },
        ),
        MechanicsCase(
            case_id="thick-cylinder-internal-pressure",
            family="axisymmetric_pressure",
            length=40.0,
            width=20.0,
            height=0.0,
            inner_radius=20.0,
            young=200000.0,
            poisson=0.3,
            load=100.0,
            metadata=CaseMetadata(
                description="检查轴对称内压、径向位移及径向和环向应力的取数定义。",
                tags=(
                    "pressure",
                    "coordinate-components",
                    "displacement",
                    "local-stress",
                ),
                source=SourceRef(
                    kind="project_analytic",
                    reference="Lamé thick-cylinder solution for an open-ended axisymmetric cylinder: "
                    "sigma_r=A-B/r^2, sigma_theta=A+B/r^2, "
                    "u=((1-nu)Ar+(1+nu)B/r)/E. Cross-section area means are "
                    "mean(sigma_r)=A-B/(ab) and mean(sigma_theta)=A+B/(ab).",
                    url=None,
                    document_version=None,
                ),
            ),
            description=(
                "mm-N-MPa 单位。开口端厚壁圆筒的二维轴对称 r-z 截面："
                "内半径20 mm、外半径40 mm、轴向长度20 mm，E=200000 MPa、nu=0.3。"
                "内壁施加100 MPa均匀内压，外壁和两端自由；仅在(r=40,z=0)约束U2去除轴向刚体平移。"
            ),
            adaptation=(
                "必须使用二维axisymmetric连续体，不用plane stress/plane strain、3D实体、壳或梁替代；"
                "允许CAX4/CAX4R/CAX8/CAX8R。全局X是半径r、Y是轴向z。"
                "压力必须覆盖完整r=20 mm内壁，外壁不施加压力；不要额外约束径向位移。"
                "应力按Abaqus轴对称分量读取：S11为径向、S33为环向；"
                "应力QoI使用积分点结果按单元r-z截面积加权，避免把边界外推误差当成物理错误。"
            ),
            metrics={
                "inner_u1": _m(0.01966666666666667, "内壁平均径向U1/mm", 0.01),
                "outer_u1": _m(0.013333333333333334, "外壁平均径向U1/mm", 0.01),
                "area_mean_s11": _m(
                    -33.333333333333336, "r-z截面积加权平均径向S11/MPa", 0.01
                ),
                "area_mean_s33": _m(100.0, "r-z截面积加权平均环向S33/MPa", 0.01),
            },
        ),
        MechanicsCase(
            case_id="nafems-le6-skew-plate",
            family="skew_plate",
            length=1.0,
            width=1.0,
            height=0.01,
            skew_angle_degrees=30.0,
            young=210.0e9,
            poisson=0.3,
            load=700.0,
            metadata=CaseMetadata(
                description="检查斜板支承、壳下表面主应力及压力反力平衡。",
                tags=(
                    "pressure",
                    "bending",
                    "shell-section-output",
                    "local-stress",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="nafems",
                    reference="NAFEMS P18.LE6 / TNSB Rev. 3 (1990), also reproduced by the Abaqus 2024 "
                    "Benchmarks Guide: skew plate under normal pressure; reference lower-surface "
                    "maximum principal stress at plate center E = 0.802 MPa.",
                    url=HttpUrl(
                        "https://www.nafems.org/publications/resource_center/p18/"
                    ),
                    document_version="TNSB Rev. 3 (1990)",
                ),
            ),
            description=(
                "m-N-Pa 单位。NAFEMS P18.LE6 斜板：平面内为边长均为1 m、A点内角30°的平行四边形，"
                "厚度0.01 m，E=210 GPa、nu=0.3。取A=(0,0,0)、B=(1,0,0)、"
                "D=(cos30°,sin30°,0)、C=B+D；全板施加700 Pa沿-Z的均匀法向压力。"
                "四条边仅约束U3=0；A额外约束U1=U2=0，B额外约束U2=0以消除面内刚体运动。"
            ),
            adaptation=(
                "必须使用常规shell/plate表示和0.01 m均匀壳厚；允许S4/S4R/S8R/S8R5/S9R5。"
                "不得把边界改成固支，也不得额外约束转角。网格必须包含板中心E节点，以便宿主独立读取。"
                "center_lower_max_principal 定义为E点下表面ELEMENT_NODAL贡献先按应力分量平均、"
                "再计算面内最大主应力；reaction_z 为所有受U3约束边界节点RF3总和。"
            ),
            metrics={
                "center_lower_max_principal": _m(
                    0.802e6, "板中心E下表面最大主应力/Pa", 0.10
                ),
                "reaction_z": _m(350.0, "四边U3约束节点RF3总和/N", 0.002),
            },
        ),
        MechanicsCase(
            case_id="plate-with-hole-tension",
            family="hole",
            length=15.0,
            width=5.0,
            height=1.0,
            young=1000.0,
            poisson=0.0,
            load=100.0,
            refinement=True,
            metadata=CaseMetadata(
                description="检查孔边应力集中、局部应力口径及粗细网格一致性。",
                tags=(
                    "stress-concentration",
                    "local-stress",
                    "mesh-refinement",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="ansys",
                    reference="VM-WB-MECH-002",
                    url=HttpUrl(
                        "https://ansyshelp.ansys.com/public/Views/Secured/corp/v242/en/wb_vm/wbvt_VM-WB-MECH-002.html"
                    ),
                    document_version="2024 R2",
                ),
            ),
            description="m-N-Pa 单位。3D实体板x=0..15、y=0..5、z=0..1 m；贯穿圆孔轴线x=7.5,y=2.5，半径0.5 m；E=1000 Pa、nu=0。x=0端面全固定，x=15端面+X均匀拉伸面力100 Pa。",
            adaptation="必须保留完整3D实体，不用对称缩减；允许C3D4/C3D10/C3D8/C3D8R/C3D20/C3D20R。只在端面使用法向拉伸pressure或traction，不附加节点约束/载荷。孔表面应力定义为S11外推到孔壁节点的未平均ELEMENT_NODAL贡献最大值，全局坐标，不用全板最大值或Mises替代。该Abaqus读取口径是本题的显式移植定义。",
            metrics={
                "hole_max_s11": _m(312.5, "孔柱面未平均外推S11最大值/Pa", 0.03),
                "reaction_x": _m(-500, "左端RF1总和/N", 0.001),
            },
        ),
        MechanicsCase(
            case_id="solid-cantilever-directional-bending",
            family="solid_beam",
            length=6.0,
            width=0.2,
            height=0.1,
            young=1e7,
            poisson=0.3,
            load=1.0,
            refinement=True,
            metadata=CaseMetadata(
                description="检查三维实体梁三个独立工况的载荷隔离、端面位移及粗细网格一致性。",
                tags=(
                    "axial-load",
                    "bending",
                    "load-case-isolation",
                    "displacement",
                    "mesh-refinement",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="ansys",
                    reference="VM184",
                    url=HttpUrl(
                        "https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_vm/Hlp_V_VM184.html"
                    ),
                    document_version="2025 R2",
                ),
            ),
            description="三维实体悬臂梁，三个独立方向载荷；具体尺寸和材料由合同生成。",
            adaptation="必须3D实体，允许C3D4/C3D10/C3D8/C3D8R/C3D20/C3D20R。一个INP/ODB包含依次X/Y/Z三个静力步，每步只有该方向载荷，不叠加前步载荷。合力在x=6端面对称分配；具体实现需保持物理等价并在报告中说明。自由端目标是该面对应方向位移最大值。不得用梁/杆替代。",
            metrics={
                "tip_u1_x": _m(0.00003, "X工况端面最大U1/in", 0.03),
                "tip_u2_y": _m(0.108, "Y工况端面最大U2/in", 0.03),
                "tip_u3_z": _m(0.432, "Z工况端面最大U3/in", 0.03),
                "reaction_x_x": _m(-1, "X工况左端RF1/lbf", 0.001),
                "reaction_y_y": _m(-1, "Y工况左端RF2/lbf", 0.001),
                "reaction_z_z": _m(-1, "Z工况左端RF3/lbf", 0.001),
            },
        ),
        MechanicsCase(
            case_id="nafems-le1-elliptic-membrane",
            family="elliptic_membrane",
            length=3.25,
            width=2.75,
            height=0.1,
            inner_semi_axis_x=2.0,
            inner_semi_axis_y=1.0,
            young=210.0e9,
            poisson=0.3,
            load=10.0e6,
            metadata=CaseMetadata(
                description="检查曲边外压、对称边界和内边指定点的局部应力。",
                tags=("pressure", "symmetry", "local-stress", "reaction-balance"),
                source=SourceRef(
                    kind="nafems",
                    reference="NAFEMS P18.LE1 / TNSB Rev. 3 (1990), reproduced by the Abaqus 2024 "
                    "Benchmarks Guide: elliptic membrane; target tangential edge stress "
                    "sigma_yy at inner point D = 92.7 MPa.",
                    url=HttpUrl(
                        "https://www.nafems.org/publications/resource_center/p18/"
                    ),
                    document_version="TNSB Rev. 3 (1990)",
                ),
            ),
            description=(
                "m-N-Pa 单位。NAFEMS P18.LE1 四分之一椭圆环形膜：内椭圆半轴"
                "a=2.0 m、b=1.0 m，外椭圆半轴a=3.25 m、b=2.75 m，厚度0.1 m；"
                "E=210 GPa、nu=0.3。x=0直边施加X对称，y=0直边施加Y对称；"
                "外椭圆边施加10 MPa均匀向外压力，内椭圆边自由。"
            ),
            adaptation=(
                "必须使用二维plane-stress连续体和0.1 m厚度；使用二次平面应力单元"
                "CPS6/CPS6M/CPS8/CPS8R，并使曲边网格足以解析D=(2,0)处局部应力。"
                "网格必须包含精确D节点。d_sigma_yy 定义为D点未平均ELEMENT_NODAL S22贡献的均值；"
                "reaction_x/reaction_y 分别为x=0对称边RF1和y=0对称边RF2总和。"
            ),
            metrics={
                "d_sigma_yy": _m(92.7e6, "D点切向S22/Pa", 0.05),
                "reaction_x": _m(-2.75e6, "x=0对称边RF1总和/N", 0.002),
                "reaction_y": _m(-3.25e6, "y=0对称边RF2总和/N", 0.002),
            },
        ),
        MechanicsCase(
            case_id="nafems-le10-thick-plate-pressure",
            family="thick_plate",
            length=3.25,
            width=2.75,
            height=0.6,
            inner_semi_axis_x=2.0,
            inner_semi_axis_y=1.0,
            young=210.0e9,
            poisson=0.3,
            load=1.0e6,
            metadata=CaseMetadata(
                description="检查厚板弯曲中的支承集合、局部应力和总反力。",
                tags=(
                    "pressure",
                    "bending",
                    "symmetry",
                    "local-stress",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="nafems",
                    reference="NAFEMS P18.LE10 / TNSB Rev. 3 (1990), reproduced by the Abaqus "
                    "Benchmarks Guide: thick plate under uniform pressure; target direct "
                    "stress sigma_yy at D=(2,0,0.6) = -5.38 MPa.",
                    url=HttpUrl(
                        "https://www.nafems.org/publications/resource_center/p18/"
                    ),
                    document_version="TNSB Rev. 3 (1990)",
                ),
            ),
            description=(
                "m-N-Pa 单位。NAFEMS P18.LE10 四分之一椭圆环形厚板：内椭圆半轴"
                "a=2.0 m、b=1.0 m，外椭圆半轴a=3.25 m、b=2.75 m，厚度0.6 m；"
                "E=210 GPa、nu=0.3。x=0侧面约束U1，y=0侧面约束U2；"
                "外椭圆柱面约束U1=U2，外椭圆边在厚度中面z=0.3 m处约束U3。"
                "z=0.6 m上表面施加1 MPa均匀法向压力，z=0下表面自由。"
            ),
            adaptation=(
                "必须使用完整3D实体厚板，不用壳、平面应力/应变或轴对称替代；"
                "允许Abaqus官方LE10验证使用的二次实体C3D20/C3D20R/C3D10/C3D10HS/C3D10M。"
                "网格必须包含精确D=(2,0,0.6)节点，并足以解析厚板弯曲和D点局部S22。"
                "d_sigma_yy 定义为D点未平均ELEMENT_NODAL S22贡献的均值；"
                "reaction_z 为外椭圆边厚度中面U3约束节点RF3总和。"
            ),
            metrics={
                "d_sigma_yy": _m(-5.38e6, "D点全局S22/Pa", 0.10),
                "reaction_z": _m(
                    5.448699758569798e6,
                    "外椭圆边厚度中面U3约束节点RF3总和/N",
                    0.002,
                ),
            },
        ),
        MechanicsCase(
            case_id="nafems-le3-hemisphere-point-loads",
            family="hemisphere_shell",
            length=10.0,
            width=10.0,
            height=0.04,
            radius=10.0,
            young=68.25e9,
            poisson=0.3,
            load=2000.0,
            metadata=CaseMetadata(
                description="检查曲面壳点载荷、包含转角的对称条件和指定点位移。",
                tags=("symmetry", "displacement"),
                source=SourceRef(
                    kind="nafems",
                    reference="NAFEMS P18.LE3 / TNSB Rev. 3 (1990), reproduced by the Abaqus 2026 "
                    "Benchmarks Guide: hemisphere with point loads; target radial displacement "
                    "Ux at A=(10,0,0) = 0.185 m.",
                    url=HttpUrl(
                        "https://www.nafems.org/publications/resource_center/p18/"
                    ),
                    document_version="TNSB Rev. 3 (1990)",
                ),
            ),
            description=(
                "m-N-Pa 单位。NAFEMS P18.LE3 四分之一球壳：中面半径10 m、厚度0.04 m，"
                "E=68.25 GPa、nu=0.3。取A=(10,0,0)、C=(0,10,0)、E=(0,0,10)；"
                "A点沿+X径向施加2 kN，C点沿-Y径向施加2 kN。"
                "AE边(y=0)施加Y对称，CE边(x=0)施加X对称，E点额外约束U3=0。"
            ),
            adaptation=(
                "必须使用常规shell表示完整四分之一球面和0.04 m均匀壳厚；"
                "允许S8R/S8R5/S9R5。网格必须包含精确A、C、E节点，且所有壳节点位于半径10 m球面。"
                "XSYMM/YSYMM按Abaqus壳自由度语义验收，不能只约束平移自由度，也不能额外固支边界。"
                "a_u1 定义为A点最终帧U1。"
            ),
            metrics={
                "a_u1": _m(0.185, "A点径向U1/m", 0.02),
            },
        ),
        MechanicsCase(
            case_id="nafems-le5-z-section-cantilever",
            family="z_section_shell",
            length=10.0,
            width=2.0,
            height=0.1,
            young=210.0e9,
            poisson=0.3,
            load=1.2e6,
            metadata=CaseMetadata(
                description="检查开口Z形壳的扭矩施加和指定点中面轴向应力。",
                tags=("torsion", "shell-section-output", "local-stress"),
                source=SourceRef(
                    kind="nafems",
                    reference="NAFEMS P18.LE5 / TNSB Rev. 3 (1990), reproduced by the Abaqus 2026 "
                    "Benchmarks Guide: Z-section cantilever under end torque; reference "
                    "midsurface axial stress sigma_xx at A=(2.5,-1,-1) = -108 MPa.",
                    url=HttpUrl(
                        "https://www.nafems.org/publications/resource_center/p18/"
                    ),
                    document_version="TNSB Rev. 3 (1990)",
                ),
            ),
            description=(
                "m-N-Pa 单位。NAFEMS P18.LE5 Z形开口薄壁悬臂：轴线x=0..10 m；"
                "截面中面依次为(y,z)=(-1,-1)->(-1,0)->(1,0)->(1,1)，壳厚0.1 m；"
                "E=210 GPa、nu=0.3。x=0整条截面ENCASTRE。x=10端通过上下自由翼缘的"
                "反向Z向剪力形成绕+X的1.2 MN·m纯扭矩；总力为零，每侧翼缘剪力合力为0.6 MN。"
            ),
            adaptation=(
                "必须使用常规shell表示完整Z形中面和0.1 m均匀壳厚；允许S8R/S8R5/S9R5。"
                "所有壳节点必须位于三块中面之一：y=-1且z∈[-1,0]、z=0且y∈[-1,1]、"
                "y=1且z∈[0,1]。网格必须包含精确A=(2.5,-1,-1)节点。"
                "端部载荷只能是x=10处、y=±1翼缘上的Z向CLOAD；合力为零且"
                "sum(y*Fz)=+1.2e6 N·m。a_sigma_xx 定义为A点ELEMENT_NODAL S11的壳中面值："
                "若ODB有显式中面section point则直接取其贡献均值，否则分别平均负/正表面贡献后取二者中值。"
            ),
            metrics={
                "a_sigma_xx": _m(-108.0e6, "A点壳中面轴向S11/Pa", 0.03),
            },
        ),
        MechanicsCase(
            case_id="nafems-le2-cylindrical-shell-patch",
            family="cylindrical_shell_patch",
            length=0.5,
            width=0.0,
            height=0.01,
            radius=1.0,
            arc_angle_degrees=10.0,
            sample_angle_degrees=6.6667,
            sample_axial_position=0.3,
            young=210.0e9,
            poisson=0.3,
            load=1000.0,
            metadata=CaseMetadata(
                description="检查壳边弯矩、局部方向、外表面切向应力及反力矩。",
                tags=(
                    "bending",
                    "coordinate-components",
                    "shell-section-output",
                    "local-stress",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="nafems",
                    reference="NAFEMS P18.LE2 / TNSB Rev. 3 (1990), reproduced by the Abaqus 2026 "
                    "Benchmarks Guide: cylindrical shell bending patch test; reference outer-surface "
                    "tangential stress at E = 60 MPa.",
                    url=HttpUrl(
                        "https://www.nafems.org/publications/resource_center/p18/"
                    ),
                    document_version="TNSB Rev. 3 (1990)",
                ),
            ),
            description=(
                "m-N-Pa 单位。NAFEMS P18.LE2 圆柱壳扇区：中面半径1 m、轴向长度0.5 m、"
                "圆周角10°、壳厚0.01 m；E=210 GPa、nu=0.3。theta=0 的AB边固支；"
                "z=0 的AD边和z=0.5 m的BC边施加轴向对称约束。theta=10° 的DC边施加"
                "1000 N·m/m均匀法向边矩，因此该0.5 m边上的总外加矩为+500 N·m。"
            ),
            adaptation=(
                "必须使用常规shell表示半径1 m的圆柱中面；允许S4/S4R/S8R/S8R5/S9R5。"
                "网格必须包含精确E=(r=1 m, theta=6.6667°, z=0.3 m)节点。壳局部1方向须为"
                "圆周切向，使e_outer_s11表示E点外表面SPOS的切向S11；按所有相邻单元的"
                "ELEMENT_NODAL外推贡献取均值。DC边矩只允许绕圆柱轴正向施加，总矩500 N·m；"
                "reaction_m3为AB固支边绕同一轴的总反力矩。"
            ),
            metrics={
                "e_outer_s11": _m(60.0e6, "E点外表面圆周切向S11/Pa", 0.03),
                "reaction_m3": _m(-500.0, "AB固支边绕圆柱轴总反力矩/N·m", 0.002),
            },
        ),
        MechanicsCase(
            case_id="nafems-le4-axisymmetric-hyperbolic-shell",
            family="axisymmetric_shell",
            axisymmetric_shell_profile="hyperbolic",
            length=1.0,
            width=2**0.5,
            height=0.01,
            radius=1.0,
            young=210.0e9,
            poisson=0.3,
            load=1.0e6,
            metadata=CaseMetadata(
                description="检查双曲壳内压与中面子午及环向应力的区分。",
                tags=(
                    "pressure",
                    "shell-section-output",
                    "coordinate-components",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="nafems",
                    reference="NAFEMS P18.LE4 / TNSB Rev. 3 (1990), reproduced by the Abaqus 2026 "
                    "Benchmarks Guide: axisymmetric hyperbolic shell under uniform internal "
                    "pressure; midsurface stress at K is sigma_phi_phi=-50 MPa and "
                    "sigma_theta_theta=+50 MPa.",
                    url=HttpUrl(
                        "https://www.nafems.org/publications/resource_center/p18/"
                    ),
                    document_version="TNSB Rev. 3 (1990)",
                ),
            ),
            description=(
                "m-N-Pa 单位。NAFEMS P18.LE4 轴对称双曲壳：母线满足 r=sqrt(1+z^2)，"
                "0<=z<=1 m，K=(r=1,z=0)，A=(r=sqrt(2),z=1)；壳厚0.01 m，"
                "E=210 GPa、nu=0.3。壳内表面承受1 MPa均匀压力。"
                "Abaqus官方网页文字写B点uz=0，但官方SAX1/SAX2/SAXA11/SAXA21可执行输入"
                "均在K侧物理节点约束U2；本公开合同以可复现的官方可执行样例为准，在K约束轴向U2。"
            ),
            adaptation=(
                "必须使用常规轴对称壳SAX1或SAX2，完整表示上述双曲中面和0.01 m均匀壳厚；"
                "所有壳节点都须满足r^2-z^2=1，网格必须包含精确K=(1,0)节点。"
                "仅K点U2=0，不额外约束径向位移或转角；对全部壳单元施加+1 MPa压力。"
                "k_meridional_s11/k_hoop_s22 定义为K点ELEMENT_NODAL S11/S22的壳中面值；"
                "若无显式中面section point，则分别平均负/正表面贡献后取二者中值。"
                "reaction_z 为K点RF2。"
            ),
            metrics={
                "k_meridional_s11": _m(-50.0e6, "K点壳中面子午向S11/Pa", 0.03),
                "k_hoop_s22": _m(50.0e6, "K点壳中面环向S22/Pa", 0.04),
                "reaction_z": _m(math.pi * 1.0e6, "K点轴向RF2/N", 0.002),
            },
        ),
        MechanicsCase(
            case_id="nafems-le7-axisymmetric-cylinder-sphere",
            family="axisymmetric_shell",
            axisymmetric_shell_profile="sphere_cylinder",
            length=2.5,
            width=1.5,
            height=0.025,
            radius=1.0,
            sample_axial_position=1.4034,
            young=210.0e9,
            poisson=0.3,
            load=1.0e6,
            metadata=CaseMetadata(
                description="检查球冠圆柱连接附近外表面应力及轴向反力。",
                tags=(
                    "pressure",
                    "shell-section-output",
                    "local-stress",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="nafems",
                    reference="NAFEMS P18.LE7 / TNSB Rev. 3 (1990), reproduced by the Abaqus 2026 "
                    "Benchmarks Guide: axisymmetric cylinder/sphere under uniform internal "
                    "pressure; reference outer-surface axial stress at D is 25.9 MPa.",
                    url=HttpUrl(
                        "https://www.nafems.org/publications/resource_center/p18/"
                    ),
                    document_version="TNSB Rev. 3 (1990)",
                ),
            ),
            description=(
                "m-N-Pa 单位。NAFEMS P18.LE7 轴对称球冠-圆柱壳：圆柱中面半径1 m，"
                "0<=z<=1.5 m；上部为半径1 m、球心位于(r=0,z=1.5 m)的球冠，"
                "延伸至极点(r=0,z=2.5 m)。壳厚0.025 m，E=210 GPa、nu=0.3；"
                "整个壳内表面承受1 MPa均匀压力。极点约束U1和UR3，底部(r=1,z=0)约束U2。"
            ),
            adaptation=(
                "必须使用常规轴对称壳SAX1或SAX2表示球冠与圆柱连续中面；网格必须精确包含"
                "D=(r=1,z=1.4034 m)节点。D点外表面SPOS的子午/轴向S11按相邻单元"
                "ELEMENT_NODAL贡献取均值，记为d_outer_s11。reaction_z为底部(r=1,z=0)"
                "节点RF2。"
            ),
            metrics={
                "d_outer_s11": _m(25.9e6, "D点外表面SPOS轴向/子午向S11/Pa", 0.02),
                "reaction_z": _m(-math.pi * 1.0e6, "底部节点轴向RF2/N", 0.002),
            },
        ),
        MechanicsCase(
            case_id="nafems-le8-axisymmetric-crown-knuckle-shell",
            family="axisymmetric_shell",
            axisymmetric_shell_profile="crown_knuckle_cylinder",
            length=0.5625,
            width=0.5,
            height=0.01,
            radius=0.25,
            crown_radius=0.25,
            knuckle_radius=0.0625,
            sample_radius=0.150763,
            sample_axial_position=0.5505748,
            young=210.0e9,
            poisson=0.3,
            load=1.0e6,
            metadata=CaseMetadata(
                description="检查过渡壳几何、指定外表面环向应力与轴向反力。",
                tags=(
                    "pressure",
                    "shell-section-output",
                    "coordinate-components",
                    "local-stress",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="nafems",
                    reference="NAFEMS P18.LE8 / TNSB Rev. 3 (1990), reproduced by the Abaqus 2026 "
                    "Benchmarks Guide: axisymmetric crown/knuckle/cylinder shell under uniform "
                    "internal pressure; reference outer-surface hoop stress at D is 94.5 MPa.",
                    url=HttpUrl(
                        "https://www.nafems.org/publications/resource_center/p18/"
                    ),
                    document_version="TNSB Rev. 3 (1990)",
                ),
            ),
            description=(
                "m-N-Pa 单位。NAFEMS P18.LE8 轴对称压力壳：圆柱中面半径0.25 m，"
                "从z=0延伸至约z=0.5 m；上部由半径0.0625 m的圆环过渡段连接"
                "半径0.25 m的球冠，轴线上球冠极点为(r=0,z=0.5 m)。壳厚0.01 m，"
                "E=210 GPa、nu=0.3，整个壳内表面承受1 MPa均匀压力。极点约束U1和UR3，"
                "底部(r=0.25,z=0)约束U2。"
            ),
            adaptation=(
                "必须使用常规轴对称壳SAX1或SAX2表示球冠、圆环过渡段和圆柱连续中面；"
                "球冠圆心为(r=0,z=0.75 m)、半径0.25 m，圆环段圆心为"
                "(r=0.1875,z=0.5 m)、半径0.0625 m。网格必须精确包含"
                "D=(r=0.150763,z=0.5505748 m)节点。D点外表面SPOS环向S22按相邻单元"
                "ELEMENT_NODAL贡献取均值，记为d_outer_s22。reaction_z为底部节点RF2。"
            ),
            metrics={
                "d_outer_s22": _m(94.5e6, "D点外表面SPOS环向S22/Pa", 0.06),
                "reaction_z": _m(
                    -math.pi * 1.0e6 * 0.25**2,
                    "底部节点轴向RF2/N",
                    0.002,
                ),
            },
        ),
        MechanicsCase(
            case_id="nafems-le9-branched-axisymmetric-shell",
            family="axisymmetric_shell",
            axisymmetric_shell_profile="branched_sphere_cylinder",
            length=2.0,
            width=1.0,
            height=0.01,
            radius=0.707107,
            sample_radius=0.707107,
            sample_axial_position=1.0,
            branch_sphere_radius=1.0,
            branch_center_axial_position=1.707107,
            young=210.0e9,
            poisson=0.3,
            load=1.0e6,
            metadata=CaseMetadata(
                description="检查分支连接、分区压力及从Gauss厚度积分点外推壳面应力。",
                tags=(
                    "pressure",
                    "junction-constraint",
                    "shell-section-output",
                    "local-stress",
                    "reaction-balance",
                ),
                source=SourceRef(
                    kind="nafems",
                    reference="NAFEMS P18.LE9 / TNSB Rev. 3 (1990), reproduced by the Abaqus 2026 "
                    "Benchmarks Guide: branched axisymmetric cylinder/spherical shell under "
                    "pressure; reference outer-surface axial stress at C on the upper cylinder "
                    "is -319.9 MPa.",
                    url=HttpUrl(
                        "https://www.nafems.org/publications/resource_center/p18/"
                    ),
                    document_version="TNSB Rev. 3 (1990)",
                ),
            ),
            description=(
                "m-N-Pa 单位。NAFEMS P18.LE9 分支轴对称壳：圆柱中面半径0.707107 m，"
                "0<=z<=2 m；半径1 m的球壳分支圆心位于(r=0,z=1.707107 m)，"
                "从轴线极点(r=0,z=0.707107 m)延伸至圆柱交点C=(r=0.707107,z=1 m)。"
                "圆柱与球壳在C使用两个重合节点并由MPC TIE连接。壳厚0.01 m，"
                "E=210 GPa、nu=0.3。仅z>=1 m的上圆柱与球壳分支承受1 MPa内压；"
                "z<1 m下圆柱不承压。底部(r=0.707107,z=0)约束U1、U2和UR3。"
            ),
            adaptation=(
                "必须使用SAX1或SAX2；C处保留圆柱节点与球壳节点两个独立重合节点，并用"
                "一个MPC TIE连接。Shell Section必须使用SECTION INTEGRATION=GAUSS和3个"
                "厚度积分点。压力只覆盖上圆柱与球壳分支，DLOAD符号为-1 MPa。"
                "c_outer_s11取C处上圆柱单元的轴向S11；3点Gauss的section-point fraction"
                "约为±0.774597和0，它们是厚度积分点而不是壳面，因此宿主按section-point"
                "fraction线性外推到负侧fraction=-1的外表面。reaction_z为底部RF2。"
            ),
            metrics={
                "c_outer_s11": _m(-319.9e6, "C点上圆柱外表面轴向S11/Pa", 0.03),
                "reaction_z": _m(
                    math.pi * 1.0e6 * 0.707107**2,
                    "底部节点轴向RF2/N",
                    0.002,
                ),
            },
        ),
        MechanicsCase(
            case_id="nafems-le11-thermal-solid",
            family="thermal_solid",
            length=1.4,
            width=1.4,
            height=1.79,
            inner_radius=0.7071,
            radius=1.4,
            young=210.0e9,
            poisson=0.3,
            load=0.0,
            thermal_expansion=2.3e-4,
            metadata=CaseMetadata(
                description="检查给定温度场、热膨胀及指定点轴向应力。",
                tags=("thermal-strain", "symmetry", "local-stress"),
                source=SourceRef(
                    kind="nafems",
                    reference="NAFEMS P18.LE11 / TNSB Rev. 3 (1990), reproduced by the Abaqus 2026 "
                    "Benchmarks Guide: solid cylinder/taper/sphere under prescribed temperature; "
                    "reference direct stress sigma_zz at A is -105 MPa.",
                    url=HttpUrl(
                        "https://www.nafems.org/publications/resource_center/p18/"
                    ),
                    document_version="TNSB Rev. 3 (1990)",
                ),
            ),
            description=(
                "m-N-Pa-°C 单位。NAFEMS P18.LE11 四分之一三维热弹性实体。"
                "全局Z为轴向，0<=theta<=90°，0<=z<=1.79 m；内表面在z=0..0.7071 m"
                "满足r²+z²=1，随后r=0.7071至顶部。外表面在z=0..0.7 m满足"
                "r²+z²=1.4²，随后经(r,z)=(1.2124,0.7)、(1.1062,1.045)、"
                "(1,1.39)线性收缩，再以r=1延伸到z=1.79。E=210 GPa、nu=0.3，"
                "线膨胀系数alpha=2.3e-4 /°C。温升场为ΔT=sqrt(x²+y²)+z。"
                "y=0面约束U2，x=0面约束U1，z=0底面和z=1.79顶面约束U3；无机械载荷。"
            ),
            adaptation=(
                "必须使用C3D20或C3D20R并覆盖完整实体；公开测评使用显式节点TEMPERATURE，"
                "不依赖UTEMP/用户子程序。每个节点温度必须等于sqrt(x^2+y^2)+z，"
                "不得叠加CLOAD/DLOAD/DSLOAD。A点为(1,0,0)内表面底部交点，"
                "a_sigma_zz读取最终ODB中该点的全局S33。"
            ),
            metrics={
                "a_sigma_zz": _m(-105.0e6, "A点全局轴向S33/Pa", 0.06),
            },
        ),
    )


def get_case(case_id: str) -> MechanicsCase:
    return next(c for c in mechanics_cases() if c.case_id == case_id)


def selected_case_ids(case_ids: Sequence[str] | None = None) -> tuple[str, ...]:
    names = BASIC_LINEAR_CASE_IDS if case_ids is None else tuple(case_ids)
    if not names or len(names) != len(set(names)):
        raise ValueError("case selection must be nonempty and unique")
    known = {case.case_id for case in mechanics_cases()}
    if not set(names) <= known:
        raise ValueError("unknown mechanics case in selection")
    return names


_CASE_LIST_ADAPTER = TypeAdapter(list[MechanicsCase])


def load_frozen_cases(root: Path, case_ids: Sequence[str]) -> tuple[MechanicsCase, ...]:
    """读取并验证该批次的Case快照，不从当前注册表补写元数据。

    Raises:
        ValueError: 快照摘要、Case顺序或唯一性不符合冻结选择。
        ValidationError: 元数据或物理合同不符合当前schema。
    """
    raw = (root / "cases.json").read_bytes()
    seal = json.loads((root / "seal.json").read_bytes())
    manifest_raw = (root / "manifest.json").read_bytes()
    if (
        seal.get("files", {}).get("manifest.json")
        != hashlib.sha256(manifest_raw).hexdigest()
    ):
        raise ValueError("frozen manifest digest mismatch")
    expected = seal.get("files", {}).get("cases.json")
    if expected != hashlib.sha256(raw).hexdigest():
        raise ValueError("frozen cases digest mismatch")
    cases = _CASE_LIST_ADAPTER.validate_json(raw)
    names = tuple(case.case_id for case in cases)
    if not names or len(set(names)) != len(names) or names != tuple(case_ids):
        raise ValueError("frozen cases do not match manifest case_ids")
    return tuple(cases)
