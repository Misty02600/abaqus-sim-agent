# Public mechanics end-to-end v1

2026-09-15。用户要求从公开材料力学/Workbench 案例转入实际自主仿真。首轮为六题各一次的 **Foundation-on 任务资格试验**，不是 off/on 因果消融或 2/3 稳定性 gate。失败不替换，不手工修补计分中的 Agent 文件。

## 实际合同与自由度

合同由 `evaluation/public_mechanics_cases.py` 的 strict `MechanicsCase` 产生，封存时另保存 JSON 和实际 prompt。六题为均匀轴向杆、变截面杆、端载悬臂梁、均布载荷悬臂梁、Workbench VM-WB-MECH-002 孔板、APDL VM184 三向实体悬臂梁。前四题为仓库参数的经典力学题；后两题明确保留来源与移植定义，不称全部为 Workbench 原题。

- 两杆按一维轴向问题定义，允许 T3D2/T3D3，补充 nu=.3（不改变轴向参考），不从面积猜三维形状。
- 两梁按矩形梁定义，允许 B31/B32，截面方向和网格自主；根部名义弯曲应力由独立 ODB 反力矩恢复，避免把实体约束角点峰值当梁解析端部应力。
- 孔板与 VM184 要求完整实体，允许常用一/二阶四面体或六面体。孔板采用全局 S11 的孔壁未平均 ELEMENT_NODAL 最大值，是显式 Abaqus 移植口径。
- VM184 使用一个 ODB 中有序 X/Y/Z 三个独立静力步，旧载荷必须清除；端面居中、同号的等效节点力合力各1 lbf。不以梁或手算代替实体。
- 首版有界 INP 校验支持一个 Part/实例、题给坐标、命名集合、常数各向同性 Elastic、RECT 材料积分梁截面；不支持实例变换、RP/coupling/MPC、general section、子程序、非线性或任意 INP 语法。限制已写入 Agent 题设，不能声称任意建模实现都能通过。
- 物理参数与边界固定；Agent 自主实现、选择允许的单元/网格、查证 API、修复失败、取数并交付。模型与脚本从空 workspace 创建，不提供参考脚本作为 seed。

## 独立验收

要求 build_model.py、extract_results.py、model.cae、model.inp、model.odb、results.json、report.md；孔板和实体梁额外保存 model_coarse.inp/odb。

宿主核对当前脚本/INP/ODB/JSON 与实际三阶段执行哈希及顺序、STA完成状态；拒绝 CAE 产出 solver artifacts。再用私有 Abaqus Python 重新读取 ODB，校验输入中的物理区域、材料/截面、约束、载荷与工况独立性、网格连通/几何边界/实体体积，比较参考和 Agent JSON。JSON 不能替代 ODB；动态脚本逐语句保真仍不构成 OS 级不可信租户保证。

容差在 Case 中逐量预先声明：杆位移/应力0.1%、杆梁反力/反力矩0.01%、梁位移/转角1%、梁恢复应力0.1%、孔板峰值3%、实体三向位移3%、实体反力0.1%。这是本次任务误差预算，不是普适 FEA 标准。输入节点力合力另容许 CAE 写入舍入量级的 1e-5 相对误差。

孔板和实体梁粗/细模型的目标量变化须≤1%，且细网格节点数更多，两套物理输入独立核对相同合同。两点一致性不等于严格渐近收敛证明，更不等于物理 validation。实体 EVOL 体积与解析几何体积的差异不得超过0.5%，用于几何覆盖检查。

## 参考运行与实现准备

六个宿主参考已生成实际 CAE/INP/ODB/JSON，并通过本次独立验收。参考结果：杆伸长0.5 mm；分段杆总伸长约0.35 mm；端载梁挠度约-8.015 mm（参考-8）；均布梁约-3.008125 mm（参考-3）；孔板细网格S11约316.2991 Pa（参考312.5）；三维梁X/Y/Z位移约3.06236e-5/0.1079166/0.4310384 in。后两题粗细变化也通过预定1%要求。

保留参考开发中的失败：最初两个 beam orientation 传参变体未被 consistency check 接受，CAE退出0但未生成INP；按2024声明的 sequence-of-sequences 改为嵌套region后完成。输入校验还按实际证据处理了ODB中nu=0末尾零省略，以及CAE节点力小数舍入。没有通过放宽数值参考容差使失败过关。参考开发不是Agent成绩。

参考产物位于本机 `D:/abaqus-runs/public-mechanics-reference-20260915-v1`（杆）、`...-v3`（梁）、`...-v2`（实体），对应 `state/public_verification_final.json`。早期错误及校验记录保留。

## 首轮运行配置

单 `abaqus_2024_coder`，Foundation-on，full API Catalog 显式使用 `abaqus-2024-modeling-candidate-v3`。原文RAG保持off，不声称测试了文档RAG；接口查证并未关闭。未接入未迁移的语义 API discovery。

Go/deepseek-v4-flash，thinking enabled/high 配置，Planning开启；HTTP60秒、SDK额外重试10；旧 legacy_decision，所有题设完整、max_clarifications=0。每题40模型请求、8 API lookup、18 Agent Abaqus调用、6 Solver调用、2700秒wall。独立宿主oracle调用不算Agent工具调用，但耗时在任务wall内。三个运行工具只执行build_model.py、extract_results.py及对应INP；按六题串行，不宣称跨进程许可证协调。

运行前独立复制包内源码、Skill/引用、Catalog、案例和有效配置，绑定相关Python依赖文件、安装版本、Python与Abaqus launcher哈希。FunctionModel预检不访问Provider。运行后复核绑定；既有结果/默认配置/Skill正文不改变。

## 来源与解释边界

- [Workbench VM-WB-MECH-002 2024 R2](https://ansyshelp.ansys.com/public/Views/Secured/corp/v242/en/wb_vm/wbvt_VM-WB-MECH-002.html)：公开题设与Target，不复制vendor工程文件或自适应模板。
- [APDL VM184 2025 R2](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_vm/Hlp_V_VM184.html)：实体悬臂与参考位移；有序独立工况和端面节点力分配为本次显式移植选择。
- 经典题来源记录沿用 `foundation_public_mechanics_sources_v1.json`，新运行合同不覆盖旧离线题。

结果按完整任务通过、失败阶段、实际修复与成本报告；允许试验失败，不以“输出了分析”替代完整交付。不因单次完成宣称通用自主选型或 Foundation 稳定收益。
