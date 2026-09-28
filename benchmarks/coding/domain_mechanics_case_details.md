# 领域仿真案例：合同与证据详表

先看[简短案例清单](domain_mechanics_inventory.md)；本页供需要核对具体合同、证据和运行入口时查阅。

当前案例为20个公开力学仿真任务，按物理能力分组。旧Gold已删除；输入变体不增加本套件的题数或权重。
选题已确认5题入选（均布梁、圆轴扭转、厚壁圆筒、孔板、三向实体梁VM184）、4题历史（均匀杆、分段杆、均匀平面应力板、端载梁），NAFEMS LE1–LE10共10题暂留评分，LE11待单独处理；目前评分选择规模为15题。VM184三个工况全部合格才记整题通过。入选不等于当前配置已经验收通过。下文保留全部20题合同与既有证据，v25运行器已接入15题默认选择和显式评分范围。
源码定义见[public_mechanics_cases.py](../../src/abaqus_sim_agent/evaluation/public_mechanics_cases.py)，
验收见[run verifier](../../src/abaqus_sim_agent/verification/public_mechanics_run.py)、
[INP checker](../../src/abaqus_sim_agent/verification/public_mechanics_input.py)和
[ODB reader](../../src/abaqus_sim_agent/verification/assets/public_mechanics_oracle.py)。
下列运行证据为删除前在`aa7116f`基线的只读盘点，不是当前版本的新成绩。
逐题来源链接见[分类与来源清单](domain_mechanics_inventory.md)，原始来源登记见[前六题](foundation_public_mechanics_sources_v1.json)及各扩展报告。
新增Abaqus 2025公开查阅链接不替换冻结来源；2026文档与2024运行证据分别解释。

## Agent 的共同工作与交付

所有从零仿真题都要求 Agent 编写普通动态 Python、处理 API/运行错误，按 CAE 建模 → 独立 Solver →
独立 odbAccess 提取分阶段执行，再申请宿主验收。宿主 oracle 不注册给模型。
全部为 Abaqus/Standard、线弹性、小变形静力；LE11 增加给定温度场的热膨胀，不是传热求解。

| 合同 | 输入与交付 | 实现自由与限制 |
|---|---|---|
| 公开力学 | 空 workspace；全部物理输入与数值参考、容差给模型；`build_model.py`、`extract_results.py`、`model.cae/inp/odb`、`results.json`、`report.md` | 允许的表示/单元内自主网格与 API 写法；坐标和取数定义固定，reader 要求一个实例；只允许指定建模/提取脚本和 Solver INP |
| 公开孔板、VM184 | 上述交付加 `model_coarse.inp/odb` | 粗细两次真实求解，目标量相对细网格变化 ≤1%；自动结果策略把节点数增加降为建议，两点一致性不等于渐近收敛 |

公开 Case `results.json` 的字段见源码 `metrics`，必须来自 ODB 或由 ODB 截面合力恢复；参考值不能硬编码为答案。
公开 JSON 与独立读数的相对一致性阈值为 `1e-5`，物理目标按逐量容差，绝对容差默认 `1e-8`。

## 按物理能力组织的公开案例合同

经典解析题、厂商验证题与NAFEMS统一组织；来源作为标签，不承担套件划分。
经典轴向杆和悬臂梁的来源为登记的LibreTexts解析关系，圆轴、平面应力和厚圆筒采用源码列明的解析关系。
Ansys来源为VM-WB-MECH-002（2024 R2）和VM184（2025 R2）。NAFEMS来源为P18/TNSB与Abaqus官方复述。
NAFEMS各题单位均为m–N–Pa（LE11另含°C），除LE3外E=210GPa、ν=.3；其余单位见逐题合同。
所有点应力按本项目指定的ODB离散取数定义；百分比为目标相对容差，符号属于合同。

### 轴向与面内拉伸

| Case ID | 来源 | 物理输入与表示 | 目标、取数与实现限定 |
|---|---|---|---|
| `uniform-axial-bar` | 项目自设；[理论依据](https://eng.libretexts.org/Bookshelves/Mechanical_Engineering/Mechanics_of_Materials_%28Roylance%29/02%3A_Simple_Tensile_and_Shear_Structures/2.01%3A_Trusses) | mm–N–MPa；L1000、A100、E200000、ν.3；左U1=0、全节点U2/U3=0，右+10000N；T3D2/3 | 端U1=.5、IP平均S11=100（各0.1%），左RF1=−10000（0.01%） |
| `stepped-axial-bar` | 项目自设；[理论依据](https://eng.libretexts.org/Bookshelves/Mechanical_Engineering/Mechanics_of_Materials_%28Roylance%29/02%3A_Simple_Tensile_and_Shear_Structures/2.01%3A_Trusses) | 同材；0–400段A100、400–1000段A200，共节点连续；同载荷约束；T3D2/3不能跨分段界面 | 端U1=.35、界面U1=.2、两段IP应力100/50（0.1%）；RF1=−10000（0.01%） |
| `plane-stress-uniform-tension` | 项目自设；解析关系见源码 | 100×20、厚2，E200000、ν.3；左仅U1=0、左下U2=0，右U1=.05；CPS4/4R/8/8R | 右平均U1=.05（0.01%）；左RF1=−4000、IP平均S11=100（0.1%）；禁止左边全锁U2 |
| `plate-with-hole-tension` | [Ansys VM-WB-MECH-002](https://ansyshelp.ansys.com/public/Views/Secured/corp/v242/en/wb_vm/wbvt_VM-WB-MECH-002.html) | m–N–Pa；15×5×1，中心孔r.5，E1000、ν0；左面全固定、右+X面力100Pa；完整3D，C3D4/10/8/8R/20/20R | 孔壁**未平均 ELEMENT_NODAL S11 贡献最大值**312.5Pa（3%），RF1=−500N（0.1%）；粗细应力变化≤1%，EVOL体积误差≤0.5% |
| `nafems-le1-elliptic-membrane` | [NAFEMS LE1](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le1.htm) | 四分之一椭圆环，内半轴2/1、外3.25/2.75、厚.1；x=0/y=0分别对称，外边向外10MPa | D=(2,0)处ELEMENT_NODAL S22贡献均值92.7MPa（5%）；两边RF1/RF2=−2.75/−3.25MN（0.2%）；CPS6/6M/8/8R，精确D节点 |

### 梁、板、壳弯曲

| Case ID | 来源 | 物理输入与表示 | 目标、取数与实现限定 |
|---|---|---|---|
| `cantilever-tip-load` | 项目自设；[理论依据](https://eng.libretexts.org/Courses/Illinois_Institute_of_Technology/Building_Envelope_Inspection_and_Rehabilitation_Engineering/08%3A_Non-Destructive_Evaluation_and_Facade_Monitoring_for_Distress_Conditions/8.06%3A_Vibration_Analysis/8.6.01%3A_Computing_fundamental_natural_frequency_of_vibration_of_beams) | X向L1000，Y高50/Z宽20，E200000、ν.3；左全固支，右−Y 1000N；B31/32，自主截面方向 | 端U2=−8、UR3=−.012（1%）；RF2=1000、RM3=1e6（0.01%）；由RM3恢复根部名义弯曲应力120（0.1%），不用实体角点峰值 |
| `cantilever-uniform-load` | 项目自设；[理论依据](https://eng.libretexts.org/Courses/Illinois_Institute_of_Technology/Building_Envelope_Inspection_and_Rehabilitation_Engineering/08%3A_Non-Destructive_Evaluation_and_Facade_Monitoring_for_Distress_Conditions/8.06%3A_Vibration_Analysis/8.6.01%3A_Computing_fundamental_natural_frequency_of_vibration_of_beams) | 同梁，全长−Y 1N/mm真正均布线载荷，不能用端集中力替代 | 端U2=−3、UR3=−.004（1%）；RF2=1000、RM3=5e5（0.01%）；恢复名义应力60（0.1%） |
| `solid-cantilever-directional-bending` | [Ansys VM184](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_vm/Hlp_V_VM184.html) | in–lbf–psi；X长6/Y宽.2/Z高.1，E1e7、ν.3；左全固定；X/Y/Z三个独立+1lbf端面工况；完整3D，C3D4/10/8/8R/20/20R | 各步自由端面对应方向位移**最大值**.00003/.108/.432in（3%），各向左反力−1（0.1%）；三步不累加载荷，粗细位移变化≤1%，体积误差≤0.5% |
| `nafems-le2-cylindrical-shell-patch` | [NAFEMS LE2](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le2.htm) | 圆柱中面R1、轴长.5、弧10°、厚.01；θ0边固支、两轴端对称；θ10边正向轴矩1000Nm/m，总500Nm | E=(r1,θ6.6667°,z.3)外表面SPOS切向S11贡献均值60MPa（3%），固支RM3=−500（0.2%）；S4/4R/8R/8R5/9R5，局部1方向为周向 |
| `nafems-le6-skew-plate` | [NAFEMS LE6](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le6.htm) | 边长1/夹角30°平行四边形、厚.01，−Z700Pa；四边仅U3=0，A U1/U2、B U2去刚体 | 中心下表面**先平均应力分量再求面内最大主应力**.802MPa（10%）；边界RF3=350N（0.2%）；S4/4R/8R/8R5/9R5、精确中心节点 |
| `nafems-le10-thick-plate-pressure` | [NAFEMS LE10](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le10.htm) | 同LE1平面范围，厚.6三维实体；x0 U1=0、y0 U2=0；外椭圆面U1/U2均为0，外边中面z.3 U3=0；顶面−Z1MPa | D=(2,0,.6) S22贡献均值−5.38MPa（10%），中面支承RF3=5.4486997586MN（0.2%）；C3D20/20R/10/10HS/10M，精确D节点 |

### 扭转

| Case ID | 来源 | 物理输入与表示 | 目标、取数与实现限定 |
|---|---|---|---|
| `circular-shaft-pure-torsion` | 项目自设；解析关系见源码 | X向L1000、实心直径20，E200000、ν.3；左固支，右绕+X 1e5Nmm；B31/32 | 端UR1=.0827605704（1%）；RM1=−1e5（0.01%）；从RM1恢复表面名义剪应力63.6619772（0.1%） |
| `nafems-le5-z-section-cantilever` | [NAFEMS LE5](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le5.htm) | X长10、Z形中面三段、厚.1；x0截面固支，x10两翼缘Z向反力偶形成+X 1.2MNm，总力0 | A=(2.5,−1,−1)中面S11=−108MPa（3%）；S8R/8R5/9R5；显式中面贡献均值，或上下表面分别均值后取中值；端部CLOAD限定 |

### 曲面壳点载荷

| Case ID | 来源 | 物理输入与表示 | 目标、取数与实现限定 |
|---|---|---|---|
| `nafems-le3-hemisphere-point-loads` | [NAFEMS LE3](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le3.htm) | 四分之一球壳R10、厚.04、E68.25GPa；A=(10,0,0) +X2kN、C=(0,10,0) −Y2kN；两边壳对称含转角，极点U3=0 | A点U1=.185m（2%）；S8R/8R5/9R5，精确A/C/极点，不能只约束平移或全固支 |

### 压力与轴对称

| Case ID | 来源 | 物理输入与表示 | 目标、取数与实现限定 |
|---|---|---|---|
| `thick-cylinder-internal-pressure` | 项目自设；解析关系见源码 | r20–40、轴长20，E200000、ν.3，开口端内压100MPa；仅(40,0) U2=0；X=r/Y=z，CAX4/4R/8/8R | 内/外壁U1=.0196666667/.0133333333；r-z截面积加权S11=−33.3333333、S33=100，均1%；不能换成带2πr权重的EVOL平均 |
| `nafems-le4-axisymmetric-hyperbolic-shell` | [NAFEMS LE4](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le4.htm) | r²−z²=1，z0–1、厚.01、内压1MPa；仅K=(1,0) U2=0 | K中面S11=−50MPa（3%）、S22=50MPa（4%）；RF2=+π·1e6N（0.2%）；SAX1/2；约束位置明确采用官方可执行输入，和官方网页B点文字有差异 |
| `nafems-le7-axisymmetric-cylinder-sphere` | [NAFEMS LE7](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le7.htm) | R1圆柱z0–1.5接球冠至2.5，厚.025、内压1MPa；极点U1/UR3=0、底部U2=0 | D=(1,1.4034) SPOS子午/轴向S11均值25.9MPa（2%）；底RF2=−π·1e6（0.2%）；SAX1/2 |
| `nafems-le8-axisymmetric-crown-knuckle-shell` | [NAFEMS LE8](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le8.htm) | R.25圆柱+R.0625圆环+R.25球冠，厚.01、内压1MPa；极点U1/UR3=0、底U2=0 | D=(.150763,.5505748) SPOS环向S22均值94.5MPa（6%）；底RF2=−π·1e6·.25²（0.2%）；SAX1/2；官方细SAX2约90.41MPa，容差包含已记录离散偏差 |
| `nafems-le9-branched-axisymmetric-shell` | [NAFEMS LE9](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le9.htm) | R.707107圆柱z0–2接球壳分支；C重合双节点MPC TIE；仅上圆柱及球壳承压，DLOAD=−1MPa；厚.01，底U1/U2/UR3=0 | C处**上圆柱**S11=−319.9MPa（3%），底RF2=+π·1e6·.707107²（0.2%）；SAX1/2，3点Gauss；按fraction外推到−1外表面，不能直接取最外积分点 |

### 热弹性

| Case ID | 来源 | 物理输入与表示 | 目标、取数与实现限定 |
|---|---|---|---|
| `nafems-le11-thermal-solid` | [NAFEMS LE11](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le11.htm) | 四分之一圆柱/锥段/球面厚实体，z0–1.79；α=2.3e−4/°C，逐节点ΔT=r+z；两对称面约束，顶底U3=0；无机械载荷 | A=(1,0,0)全局S33=−105MPa（6%）；C3D20/20R、显式TEMPERATURE，不用UTEMP；不是纯机械载荷题 |

## 已有证据，不合并为当前成功率

20题均有实现；离线合同测试不能证明当前真实模型或持证运行通过。
下表的“正式通过”限冻结批次的自动验收。公开 results-first 报告默认没有独立脚本审查签署，不能扩大为完整规格保真。

| Case | 持证参考层 | 真实 Agent 与后续证据 |
|---|---|---|
| uniform axial bar | [六题参考](public_mechanics_reference_v1_20260915.json)通过 | v1正式完成；本机v22-rerun1也正式通过（14请求），不是相同profile重复估计 |
| stepped axial bar | 同上 | v1正式完成（23请求）；见[测量修正记录](public_mechanics_e2e_measurement_correction_20260915.md) |
| cantilever tip load | 同上 | v1/v13预算失败；本机v21在允许的一次resume内正式通过（32请求） |
| cantilever uniform load | 同上 | v1受LineLoad零分量/同字节重写误判污染，测量无效；v2只有中断记录，未找到正式完成outcome |
| circular shaft | [v6参考资格](public_mechanics_v6_case_extension_20260917.md)通过 | v6预算失败但事后物理合格；本机v13新批正式通过（16请求） |
| plane stress uniform | [v7参考资格](public_mechanics_v7_plane_stress_extension_20260917.md)通过 | v7预算失败但事后物理合格；本机v13新批正式通过（26请求） |
| thick cylinder | [v8参考资格](public_mechanics_v8_axisymmetric_pressure_extension_20260917.md)通过 | v8预算失败但事后物理合格；本机v21仍预算失败 |
| hole plate | 六题参考通过，包括粗细检查 | [v5正式批次](public_mechanics_knowledge_api_v5_20260916.md)Provider中断；后来8次attempt且amendment后自动通过，只是post-hoc。本机v22-rerun1另有Provider中断 |
| VM184 solid | 六题参考通过，包括三独立工况及粗细检查 | v5正式批次Provider中断；15次attempt、amendment及timeout诊断后自动通过，仍为post-hoc；[更早Sol回放](public_mechanics_results_first_v4_20260915.md)也不是正式通过 |
| LE1 | [v10官方输入与适配输入](public_mechanics_v10_nafems_le1_elliptic_membrane_extension_20260917.md)参考资格通过 | v10预算失败、0 Solver、无可作物理回放的ODB |
| LE2 | 本机参考Solver完成，独立读数59.705437MPa、RM3≈−500 | 本机v15 Provider中断，无完成响应，不计模型物理失败 |
| LE3 | 本机参考独立U1=.184898987m | 本机v12预算失败、1次Solver；有post-hoc measurement文件，未据文件存在宣告全合同通过 |
| LE4 | 本机参考STA完成；未在资格目录找到结构化完整oracle判定 | 本机v16正式自动通过（28请求、3 Solver）；不是据参考脚本推断 |
| LE5 | 本机参考独立中面S11=−109.847982MPa | 本机v14正式自动通过（36请求、1次允许resume） |
| LE6 | [v9参考资格](public_mechanics_v9_nafems_le6_skew_plate_extension_20260917.md)通过 | v9预算失败，提取修完未执行；事后独立物理合格。本机v22-rerun1预算失败、0 Solver，不能被v9事后结果覆盖 |
| LE7 | 本机参考STA完成；未在资格目录找到结构化完整oracle判定 | 本机v17正式自动通过（29请求、1次允许resume） |
| LE8 | 本机参考STA完成；未找到结构化完整oracle判定 | 本机v18 `interrupted`，2个完整响应；不擅自细分为物理失败或Provider超时 |
| LE9 | 本机参考独立S11=−314.810597MPa、RF2=1570797.25N | 本机v19 Provider中断，未正式完成 |
| LE10 | [v11参考资格](public_mechanics_v11_nafems_le10_thick_plate_extension_20260917.md)通过 | v11预算失败且局部S22物理失败；人工[只改SYM_Y](le10_sym_y_isolation_20260922.md)持证通过。随后[指导/自然续跑至60次](le10_continuation60_20260922.md)均未正式完成，指导组曾得到合格解后又留下未求解加密输入 |
| LE11 | 本机官方适配输入独立S33=−103.261344MPa；不是完整参考交付验收记录 | 在本次检查的public-mechanics目录及仓库报告中未找到正式Agent trial，不宣称已测或已通过 |

表中“本机”项目的路径、原始分类与文件hash见[证据摘录](domain_mechanics_evidence_audit_20260927.json)。
尤其 v5 的最新 `report.json` 将事后终态标为 `formal_pass`，但冻结报告明确 `formal_batch=false`；
**原批次仍为Provider中断**。不能只扫描该字段统计成绩。
本次未逐份重验全部旧seal，也未读取所有原始ODB；未找到记录不等于证明记录从未存在。

## 重复、互补与评分问题

| 比较或发现 | 本次核对结论与具体依据 |
|---|---|
| 容差不能统一为2% | 均匀场解析题可以较紧；LE10参考细C3D20约−5.643MPa，本就偏离−5.38约4.9%；LE8、LE11也记载允许单元的离散偏差。各容差是冻结任务误差预算，不是普适FEA标准 |
| 取数不能统一成“最大应力” | 孔板取未平均贡献最大值；LE1/10取指定点贡献均值；LE6先平均分量再求主应力；LE5取中面；LE7/8取SPOS；LE9由Gauss积分点外推壳面；梁名义应力从RM恢复。换口径会改变题目 |
| 曾把合法实现当错答案 | [公开v2](public_mechanics_e2e_measurement_correction_20260915.md)：零载荷分量、同字节重写误拒。[v4](public_mechanics_results_first_v4_20260915.md)：OP=NEW多块处理修正。旧成绩未回填 |
| results-first不是完整规格验证 | `assess_measurements`把**所有** `check_input` findings降为advisories，里面既有语法/写法差异，也有真正的错边界/载荷/允许单元检查。数值、identity、provenance仍强制，但不能把advisory一概理解为无关写法；脚本审查是必要的独立证据 |
| 自动identity也有范围 | `model_identity_errors`强制0.5% EVOL体积仅对hole/solid_beam；不能由goal要求3D输出EVOL就声称LE10/LE11同样强制体积gate。少量数值指标也不证明全部材料与几何忠实 |
| LE10给出的反例 | 原错误支承模型反力和体积均合理，D点却为+0.003773MPa；只改SYM_Y后为−5.562129MPa。整体平衡不能代替局部边界和应力；这是参考正确、Agent实现错误的证据，不是题设与参考冲突 |

## 当前入口、预算与复现条件

下列模块路径均以 `python -m abaqus_sim_agent.evaluation.<模块>` 调用；当前领域仿真使用公共suite入口。
“可装配”只指当前源码/离线检查，未开展新的付费或持证验收。

| 入口 | 当前配置/可用性 | 历史边界 |
|---|---|---|
| `public_mechanics_suite` | v25；`prepare/preflight/doctor/run/continue/resume/pause/report/launch`；`--case`可指定多个不同Case，不选则15题；历史/未入选Case只作非计分诊断。必传新ASCII `--root` 与 `--catalog`；默认Foundation on、API full、Planning off、docs off、legacy_decision；每题80模型/30 Abaqus/10 Solver/5400秒，澄清0，lookup/preflight计数不限；HTTP300秒+4次retry；任务/runtime并发各1（可选2） | `prepare`复制源码/Skill/Catalog，可选knowledge；seal绑定依赖与launcher。`preflight`是假模型，`doctor`会真实访问Provider。旧批次必须用其worker/snapshot与匹配依赖，当前report拒绝覆盖旧version |
| `foundation_public_mechanics_benchmark` | 更早的力学行为/回答题集装配与评分 | 与当前20题仿真suite不同，不能因同名Case把行为分数计入仿真完成 |

公开 v1及多项v6–v22历史配置通常为40/18/6/2700秒、API8、Planning on，HTTP又分60/retry10、600/retry0、180/retry1等。
v5事后恢复还修改limits/timeout；完整值按冻结manifest与amendment分别读，不能仅以目录v号推断当前执行条件。
v25 metadata、15题选择及原生结果规则已完成离线接线验证，尚未运行新的真实模型/持证批次。元数据schema、批次冻结和报告语义见[Coding入口](README.md)。

旧state不能交给当前CLI猜测升级；依赖、旧Planning适配、prompt/Skill、case/oracle和运行资源一起决定可复现性。
[LE10续跑记录](le10_continuation60_20260922.md)已记载Harness/Pydantic AI升级导致93个绑定文件不符，恢复匹配runtime后才继续。
在本工作树中，本机原始批次和主工作区的参考资料可以只读访问，但 `var/api-catalog`、`var/knowledge`、ODB、
原始reference脚本/运行树并非这份Git checkout提供的可移植资源；换机器需要原证据存储或重新资格验证。

## 修改、修复与历史证据的边界

旧Gold的从零评测入口已删除。修改/修复任务仍验证既有种子的原始请求、产物hash与物理结果，
`evaluation/seed_contracts.py`只保存它们需要匹配的来源文本；`plate_reference.py`、`beam_reference.py`和取数脚本被修改/修复任务复用。
独立交互试点复用部分需求与测量schema，但不并入本套件或增加同一物理题权重。
保留这些实际依赖不提供创建新Gold批次的入口；旧源码与专属报告可在Git历史查阅。
本机冻结运行树未重跑或修改；LE10修复观察继续与从零正式trial分列。
