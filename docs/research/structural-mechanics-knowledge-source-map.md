# 结构力学知识包来源图谱

更新时间：2026-09-17。

本文记录结构力学知识包应长期复用的来源、用途和证据边界，避免每次扩展专题时重新搜索同一批资料。目标不是把手册或教材原文搬进模型上下文，而是把不同来源编译成短小、可追溯、可按需加载的原创知识笔记。

## 来源分层

| 层级 | 主要来源 | 最适合回答什么 | 不应承担什么 |
|---|---|---|---|
| 理论与建模原则 | Bathe/MIT OCW、Bower、Bonet & Wood、Belytschko 等教材 | 为什么这样建模；运动学、本构、离散化和算法的物理/数值含义 | Abaqus 对象名、参数签名、版本兼容 |
| Abaqus 实现语义 | Abaqus 2026 FD02 官方分析、材料、单元、接触手册 | Abaqus 支持什么 formulation/procedure、适用条件、默认行为、输出和诊断 | 自动证明 Abaqus 2024 运行兼容 |
| Abaqus 官方案例 | 官方 Getting Started、Example Problems、Benchmarks、Verification 脚本 | 把概念映射成完整建模顺序；观察对象关系、步骤组合和结果检查 | 作为用户材料参数、边界条件或通用模板直接照搬 |
| 外部验证案例 | NAFEMS benchmark、ANSYS Verification Manual | 独立/跨代码检查、解析目标值、发现遗漏的典型结构题型 | Abaqus API、Abaqus solver 特有行为 |

官方页面和官方脚本保留来源 release、页面、资源名和 hash。商业教材只记录书目信息和知识用途，知识包内写原创总结，不复制章节正文。开放课程也优先整理成自己的高密度笔记，而不是把整份 PDF 长期塞入默认上下文。

## Abaqus 官方文本

本机 Abaqus 2026 FD02 文档已建立页面级来源清单，当前精选 33 个页面，覆盖：

- linear structural foundation：static procedure，以及 beam/shell/solid element selection；
- hyperelasticity：rubberlike hyperelasticity、compressibility、test data、hybrid formulation；
- plasticity：classical metal plasticity，以及名义/工程应力应变到 true stress + logarithmic plastic strain 的材料数据预处理语义；
- geometric nonlinearity：`nlgeom` 语义和 CAE step 开关，有限应变下 Cauchy stress 与 `E`/`NE`/`LE` 输出语义、Standard ODB 默认应变度量，以及 eigenvalue buckling、initial imperfection、unstable collapse/postbuckling 与 modified Riks；
- structural dynamics：dynamic procedures、frequency extraction、modal dynamics、direct implicit dynamics；
- Explicit dynamics：explicit procedure、mass scaling，以及 section controls 中的 hourglass/distortion control 和 Explicit total-energy output 语义；
- contact：contact overview、mechanical properties、Standard/Explicit troubleshooting；
- interface-cohesive：cohesive element overview/behavior 和 surface-based cohesive behavior。

来源索引由 `src/abaqus_sim_agent/skills/import_official_structural_texts.py` 生成。本地快照只保存元数据、章节标题和 hash，不复制厂商文档整页正文。该索引解决“以后从哪里重新读”的问题；各 Pack 再把稳定建模决策编译成小型 topic reference。`references/abaqus-official-notes.md` 通常只做入口索引，Agent 按未解决的问题继续读取对应 topic，而不是一次加载整组官方笔记。若某专题只有一个很短、不可再自然拆分的稳定主题，可以保留单文件，不为目录形式强行拆分。

## NAFEMS benchmarks

NAFEMS 的 P18 *The Standard NAFEMS Benchmarks* 值得作为长期验证来源保留。NAFEMS 对这套测试的原始定位是独立于有限元软件厂商、能够在不同有限元系统上运行的标准测试；目标值优先来自解析解，必要时也采用多套数值系统相互核对。P18 Rev. 3 的公开目录仍列出了经典线性、热学和自由振动题。

入口：<https://www.nafems.org/publications/resource_center/p18/>

对当前线性基础包最有价值的结构题包括：

| ID | 题目 | 主要检查点 |
|---|---|---|
| P18.LE1 | Elliptic Membrane | plane stress、曲边几何、压力载荷与应力提取 |
| P18.LE2 | Cylindrical Shell Patch Test | shell patch、曲面压力、壳单元基本一致性 |
| P18.LE3 | Hemisphere Point Loads | shell、点载荷、弯曲/膜耦合与网格敏感性 |
| P18.LE5 | Z-Section Cantilever | 用 plate/shell 表示开口薄壁截面，载荷诱发扭转 |
| P18.LE6 | Skew Plate Normal Pressure | 斜板弯曲、非正交几何、压力载荷 |
| P18.LE10 | Thick Plate Pressure | 厚板/实体弯曲、三维应力与单元性能 |
| P18.LE11 | Solid Cylinder/Taper/Sphere with Temperature | 三维实体、温度梯度、热弹性 |

P18 本身不是非线性知识包的主来源；NAFEMS 还有几何非线性、塑性、接触、复合材料等后续专题出版物，可以按实际测评缺口再引入。它最适合成为 verifier/evaluation 的“外部题库与目标值来源”，而不是 Agent 的 Abaqus 操作手册。

## ANSYS Verification Manual

ANSYS Verification Manual 也值得长期记录。它是厂商维护、随 release 更新的验证案例库，当前公开 2026 R1 页面仍包含大量完整输入 listing。案例经常把计算结果与解析解、经典教材或 NAFEMS benchmark 对比；例如公开的 plasticity R0049 系列直接标注 NAFEMS 来源，动态厚板 VM203 也直接引用 NAFEMS forced-vibration benchmark。

入口：<https://ansyshelp.ansys.com/>

使用时把它定位为：

- 跨软件验证案例来源：同一个物理问题可用 Abaqus 重建并比较目标量；
- 案例发现器：能快速看到塑性、接触、动力学、热应力等典型 verification problem；
- 第二实现证据：当 Abaqus 官方案例只展示一种建模方式时，可用另一商业求解器的验证题检查物理问题和目标量是否具有普适性。

它不是 Abaqus API 或 Abaqus 默认行为的证据。ANSYS 元素、命令、收敛控制和求解器特性不能翻译成 Abaqus 规则。若某个 ANSYS case 本身引用 NAFEMS 或经典解析解，优先追溯原始 benchmark/解析来源，把 ANSYS 输入 listing 作为附加实现参考。

## 理论和开放教学来源

### 可直接长期复用的开放资料

- K. J. Bathe, MIT OpenCourseWare, *Finite Element Procedures for Solids and Structures*: <https://ocw.mit.edu/courses/res-2-002-finite-element-procedures-for-solids-and-structures-spring-2010/>。线性课程覆盖单元、数值积分、静力方程、动力学、模态与频率；非线性课程覆盖增量分析、Lagrangian formulation 和非线性求解。适合作为 linear foundation、geometric nonlinearity、structural dynamics 的共同理论骨架。
- MIT OpenCourseWare 2.094, *Finite Element Analysis of Solids and Fluids II*: <https://ocw.mit.edu/courses/2-094-finite-element-analysis-of-solids-and-fluids-ii-spring-2011/>。提供整套可下载 lecture notes，直接覆盖 large-displacement analysis、有限变形、stress/strain tensors、Total Lagrangian、`u/p` formulation、非线性求解以及 beam/plate/shell；特别适合补 geometric nonlinearity、hyperelasticity 和近不可压缩 formulation 的理论层。
- A. F. Bower, *Applied Mechanics of Solids*: <https://solidmechanics.org/>。除了基本连续体力学、材料、边界条件和有限元建模，还直接覆盖 hyperelasticity、rate-independent plasticity、large-strain constitutive behavior、contact、cohesive-zone/interface fracture 和 failure mechanics，并提供部分教学代码。它可以作为多个 Pack 共用的第一开放参考，而不只服务 linear foundation。
- H. Luş and S. Keten, *Structural Dynamics*（2024，Northwestern University Libraries，CC BY-NC-SA）：<https://open.umn.edu/opentextbooks/textbooks/1724>。可直接获取 PDF，覆盖 SDOF/MDOF 自由与受迫振动、数值积分和线性多自由度模型；适合补 structural-dynamics Pack 的物理基础，但不替代 Abaqus modal/direct-dynamics 实现语义。
- R. Pfefferkorn, *EAS Elements for Solid Mechanics: Mesh Distortion Insensitive and Hourglassing-Free Formulations with Increased Robustness*（2023，KIT Scientific Publishing，CC BY-SA 4.0）：<https://doi.org/10.5445/KSP/1000154549>。这是较窄的专题书，不作为基础必读；当 discretization/Explicit 任务暴露 mesh distortion、hourglassing、enhanced-assumed-strain 或 mixed-element 相关缺口时，可作为开放的深入来源。

这些开放来源优先进入“可重复获取”的理论来源池。进入运行时 Skill 前仍应先编译成短小的决策笔记，不把整本教材或整套讲义作为默认上下文。

### 商业教材：只引用和总结

| 来源 | 最适合支持的专题 |
|---|---|
| Hughes, *The Finite Element Method: Linear Static and Dynamic Finite Element Analysis* | linear FE 的 variational/discrete formulation、time integration、beam/plate/shell 与线性动力学；适合补五个 linear foundation Skill 背后的统一 FE 理论，但不直接进入默认上下文 |
| Belytschko, Liu, Moran, Elkhodary, *Nonlinear Finite Elements for Continua and Structures* | 非线性 FE 总体、large deformation、material nonlinearity、contact/impact、Explicit 思维 |
| Bonet & Wood, *Nonlinear Continuum Mechanics for Finite Element Analysis* | kinematics、stress/equilibrium、hyperelasticity、large elastoplastic deformation、linearization 与 solution |
| Holzapfel, *Nonlinear Solid Mechanics* | finite deformation、hyperelasticity、nearly incompressible solids |
| Ogden, *Non-Linear Elastic Deformations* | finite elasticity、principal stretches、strain-energy functions、incompressible/isotropic hyperelastic solids；适合作为 hyperelasticity 的经典理论补充 |
| Simo & Hughes, *Computational Inelasticity* | plasticity、internal variables、return mapping、consistent tangent |
| de Souza Neto, Perić & Owen, *Computational Methods for Plasticity: Theory and Applications* | 从小应变到大应变 plasticity、return mapping、consistent tangent、quasi-static nonlinear FE、arc-length；适合作为 metal-plasticity 的工程实现型主参考 |
| Dunne & Petrinic, *Introduction to Computational Plasticity* | 更教学化的 continuum plasticity、有限变形运动学、implicit/explicit integration，以及把本构实现进 FE code 的思路；适合作为 Simo/Hughes 的入门补充 |
| de Borst, Crisfield, Remmers & Verhoosel, *Non-Linear Finite Element Analysis of Solids and Structures* | nonlinear solution strategies、plasticity、damage、hyperelasticity、large-strain elastoplasticity；适合补 geometric nonlinearity 与多类材料非线性的共同数值骨架 |
| Wriggers, *Computational Contact Mechanics* | contact kinematics、constraint enforcement、contact discretization 与算法 |
| Laursen, *Computational Contact and Impact Mechanics* | contact/impact、large deformation interface algorithms |
| Nam-Ho Kim, *Introduction to Nonlinear Finite Element Analysis* | 教学型 nonlinear elasticity/plasticity/contact 与 FE 求解过程 |
| Donaldson, *Introduction to Structural Dynamics* | FEM equations of motion、damping、natural frequencies、modal transformation、time integration；适合 structural-dynamics 的 FE 视角补充 |
| Chopra, *Dynamics of Structures: Theory and Applications to Earthquake Engineering* | SDOF/MDOF、free/forced response、response spectra、numerical response 与 modal analysis；适合作为 structural-dynamics 的物理直觉和解析算例来源 |
| Anderson, *Fracture Mechanics: Fundamentals and Applications* | LEFM、energy release rate、fracture modes、testing 与 structural fracture；适合 interface/cohesive Pack 理解“裂纹驱动力/断裂能”背景，但不能替代 cohesive-zone 本构与 Abaqus 实现语义 |

这些书适合生成 `modeling-principles.md` 或专题 theory notes。笔记应写成“Agent 做决定时需要知道的条件、分支和失败模式”，不要写成教材摘要。

## 专题来源优先级

| 专题 | 第一理论来源 | 第一 Abaqus 实现来源 | 外部验证来源 |
|---|---|---|---|
| linear foundation | Bathe/MIT OCW、Bower、Hughes | Static procedure + beam/shell/solid selection | NAFEMS P18 LE/FV；ANSYS VM |
| hyperelasticity | Bonet & Wood、Holzapfel、Ogden、MIT 2.094、Bower | Hyperelastic Behavior of Rubberlike Materials | 压缩/拉伸解析与 vendor verification case |
| plasticity | Simo & Hughes、de Souza Neto、Dunne & Petrinic、Bower | Classical Metal Plasticity | NAFEMS material-nonlinear reports；ANSYS R0049/VM |
| geometric nonlinearity | Bathe/MIT 2.094、Bonet & Wood、de Borst、Belytschko | geometric nonlinearity pages + official nonlinear examples | NAFEMS geometric-nonlinear reports；cross-code benchmarks |
| structural dynamics | Bathe/MIT OCW、Luş & Keten、Chopra、Donaldson | frequency/modal/direct dynamic pages | NAFEMS FV + forced-vibration cases；ANSYS VM |
| Explicit dynamics | Belytschko + Abaqus theory | Explicit Dynamic Analysis + Mass Scaling | impact/wave/contact benchmarks |
| contact | Wriggers、Laursen、Bower | Contact overview/mechanical/troubleshooting | NAFEMS advanced contact benchmarks；ANSYS VM |
| interface/cohesive | Bower 的 surfaces/interfaces 与 cohesive-zone 内容；Anderson 补 fracture-energy、mode 与试验背景 | cohesive element + contact cohesive behavior | delamination/DCB/ENF/mixed-mode benchmarks |

## 当前 Pack / Skill 边界

Pack 管知识范围、来源、案例与维护责任；Skill 只在存在独立任务入口时拆分。一个 Pack 可以只有一个 Skill，
reference 也不因为章节不同而自动升级成 Skill。

| Capability Pack | 当前 Skill | Skill 边界 |
|---|---|---|
| `linear-structural-fea` | `formulation`、`modeling`、`discretization`、`postprocess`、`verification` 五个现有 Skill | 五类任务都可独立发生，因此保持五入口；solve 继续由 runtime/host 拥有，不新增 Skill |
| `hyperelasticity` | `hyperelastic-fea`、`hyperelastic-calibration` | 前者使用已确定的本构数据完成结构建模；后者处理试验数据、拟合、稳定性与可压缩性假设 |
| `metal-plasticity` | `plasticity-fea` | 当前覆盖 classical metal plasticity 的结构使用、数据语义，以及 measured tensile data → true stress/logarithmic plastic strain → hardening table 的预处理链；该预处理是同一建模入口下的 reference，循环塑性、损伤/断裂尚不足以形成独立 Skill |
| `geometric-nonlinearity` | `geometric-nonlinear-fea` | 负责有限位移/转动、构形变化、应力刚化、有限应变输出度量以及稳定性路径；`S`/`E`/`NE`/`LE` 解释和实验/DIC 应变度量匹配作为跨超弹性、塑性等专题复用的 reference，eigenvalue buckling、imperfection、postbuckling/Riks 也按需覆盖；只有后续形成稳定独立任务入口时才考虑拆 Skill |
| `structural-dynamics` | `structural-dynamics-fea`、`modal-dynamics-fea` | 前者负责直接瞬态与动力过程选择；后者负责频率提取、预应力基态/模态基和 modal response，并按需读取 primary/secondary base-motion 规则；这些仍属于同一模态任务入口，不为预应力模态或支座激励另拆 Skill |
| `explicit-dynamics` | `explicit-dynamics-fea`、`quasi-static-explicit-fea` | 前者是真实惯性主导的冲击/波传播等瞬态，并按需覆盖 hourglass/distortion control 与 `ALLAE`/`ALLDC` 诊断；后者是用 Explicit 逼近名义静力并验证惯性/质量缩放影响；这些仍属于同一两个任务入口，不为数值诊断另拆 Skill |
| `contact` | `contact-fea`、`contact-diagnostics-fea` | 前者定义物理接触界面与本构；后者针对已有模型的初始化、收敛、穿透和接触力异常 |
| `interface-cohesive` | `interface-cohesive-fea` | cohesive element 与 surface-based cohesive behavior 仍需先在同一入口内比较；当前没有足够理由按表示方法拆成两个 Skill |

这张表是当前内容组织边界，不增加 Agent、solver 或运行权限。`structural_mechanics_v1` 只是把这些 Skill
暴露给同一个 Coder 的 profile；它本身不是知识包。

## 编译成知识包的规则

1. `SKILL.md` 只放常用决策规则、必要输入、路由和“何时继续读某个 reference”。
2. Abaqus 官方语义按稳定决策主题写入短小 reference，例如 procedure selection、element selection、contact mechanics 或 diagnostics；`references/abaqus-official-notes.md` 优先只保留这些 topic 的索引与来源边界。
3. `references/modeling-principles.md` 只在某专题确实需要理论补充时建立；优先几何非线性、接触、界面/黏聚和塑性算法。
4. `references/official-examples.md` 只做案例导航。脚本 canonical 副本仍由共享官方脚本池拥有，跨专题只引用。
5. 外部 benchmark 首先服务 verification/evaluation；只有它提供了 Agent 做建模决定所需的通用规则时，才把该规则编译进运行时 Skill。
6. 任一来源缺少用户问题所需的物理参数，都不能用教程常数补造。来源能解释“参数代表什么”和“如何验证”，不能替用户决定参数值。

当前 theory layer 采用按需建立：`hyperelasticity`、`metal-plasticity`、`geometric-nonlinearity`、`structural-dynamics`、`explicit-dynamics`、`contact` 和 `interface-cohesive` 已有 `modeling-principles.md`；`linear-structural-fea` 暂不增加总的 theory reference，因为 formulation、modeling、discretization、postprocess、verification 五个 Skill 已承担基础理论拆分，再建总文件会重复默认上下文。后续只有真实任务暴露稳定缺口时再新增专题 theory note。
