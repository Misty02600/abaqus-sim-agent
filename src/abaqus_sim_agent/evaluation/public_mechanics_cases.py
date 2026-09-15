"""公开力学问题的 Abaqus 端到端合同；不是产品建模 IR。"""

from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from abaqus_sim_agent.coding.contracts import CodingRunBudget


class Metric(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    target: float
    relative_tolerance: float = Field(gt=0)
    absolute_tolerance: float = Field(default=1e-8, gt=0)
    meaning: str


class MechanicsCase(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    case_id: str
    family: Literal["truss", "beam", "hole", "solid_beam"]
    description: str
    source: str
    adaptation: str
    length: float
    width: float
    height: float
    young: float
    poisson: float
    load: float
    segments: tuple[tuple[float, float], ...] = ()
    distributed: bool = False
    refinement: bool = False
    metrics: dict[str, Metric]

    def physical_description(self) -> str:
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
            max_model_requests=40,
            max_api_lookup_calls=8,
            max_concept_lookup_calls=2,
            max_abaqus_calls=18,
            max_solver_calls=6,
            wall_time_seconds=2700.0,
        )

    def goal(self) -> str:
        metric_text = "\n".join(
            f"- {name}: {m.meaning}；公开参考 {m.target:g}；相对容差 {m.relative_tolerance:.2%}，绝对容差 {m.absolute_tolerance:g}。"
            for name, m in self.metrics.items()
        )
        extra = (
            "必须保存粗、细两套实际网格及结果：model_coarse.inp/model_coarse.odb 与 model.inp/model.odb；"
            "说明网格如何加密，节点数仅作辅助检查。两者物理问题不变，目标位移或孔表面应力相对细网格的变化不超过1%。"
            "这只是本题的加密一致性检查，不是通用收敛阈值。"
            if self.refinement
            else "本题不强制第二次求解，是否需要加密由实际误差决定。"
        )
        return f"""使用 Abaqus 2024 在空工作区自主完成以下完整仿真，而非仅写理论分析。
{self.physical_description()}
题设的线弹性、小变形、静力假设固定，使用 Abaqus/Standard Static General、NLGEOM=OFF。
{self.adaptation}
单元和网格在本题允许的表示范围内自行选择，不得改变题设参数、载荷、约束或目标量来拟合参考。
所有必需物理输入已给全；接口/网格等实现选择可在预算内查证，不用请求用户选择 API。
建模脚本 build_model.py 只能生成 model.cae 和 INP，不能提交 Job；求解必须用独立 solver 工具。
使用独立 extract_results.py 从本次 ODB 读取结果，写 results.json；不得硬编码参考结果。
保留所需 U/RF/S 场；梁还需能读取 UR/RM（来自 U/RF 输出），实体须请求 EVOL 供宿主核对体积。
{extra}
当前独立ODB读取器要求单个实例；集合名称、INP分块及API写法自行选择。装配、约束和载荷的实现说明供脚本审查。
按题给全局坐标建立几何以便独立取数，使用常数各向同性Elastic；梁截面的物理尺寸和方向保持题设。
results.json 的顶层数值字段（含方向；每项必须来自 ODB 或由其截面合力恢复）为：
{metric_text}
同时交付简短 report.md，说明模型选择、读取定义、误差与仍存在的限制。
只有脚本、CAE/INP/ODB、JSON 和实际三阶段证据齐全后才请求宿主验收；失败时依据反馈自行修复。
公开参考用于对照；宿主以独立ODB数值、反力、几何材料尺度及收敛结果为主要验收依据。
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
            source=axial,
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
            source=axial,
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
            source=beam,
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
            source=beam,
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
            case_id="plate-with-hole-tension",
            family="hole",
            length=15.0,
            width=5.0,
            height=1.0,
            young=1000.0,
            poisson=0.0,
            load=100.0,
            refinement=True,
            source="https://ansyshelp.ansys.com/public/Views/Secured/corp/v242/en/wb_vm/wbvt_VM-WB-MECH-002.html",
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
            source="https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_vm/Hlp_V_VM184.html",
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
    )


def get_case(case_id: str) -> MechanicsCase:
    return next(c for c in mechanics_cases() if c.case_id == case_id)
