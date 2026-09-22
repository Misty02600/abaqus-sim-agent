# 公开材料力学 v10：NAFEMS LE1 椭圆膜案例扩展

2026-09-17。目标是在已有杆、梁、圆轴、平面应力、轴对称、实体和 LE6 板/壳案例之外，加入曲边二维连续体、对称边界、曲边压力和局部应力外推，并继续分别记录 Foundation capability、官方只读知识资源、API Catalog、Abaqus runtime 与独立物理验收证据。旧批次、旧 seal 和旧成绩不回填。

## 案例与参考资格

新增 `nafems-le1-elliptic-membrane`，对应 NAFEMS P18.LE1 / TNSB Rev. 3 (1990) 的 elliptic membrane。模型为四分之一椭圆环：内椭圆半轴 2.0 m × 1.0 m，外椭圆半轴 3.25 m × 2.75 m，厚度 0.1 m，`E=210 GPa`、`nu=0.3`。`x=0` 为 X 对称，`y=0` 为 Y 对称，外椭圆边承受 10 MPa 均匀向外压力，内椭圆边自由。

公开目标为内边点 `D=(2,0)` 的切向/全局 Y 应力 `sigma_yy=92.7 MPa`。正式合同允许 `CPS6/CPS6M/CPS8/CPS8R`，要求网格含有精确 D 节点；宿主以 D 点未平均 `ELEMENT_NODAL S22` 贡献的均值作为该离散模型的读取口径，并以两条对称边反力检查载荷平衡。

来源：

- NAFEMS P18：`https://www.nafems.org/publications/resource_center/p18/`
- Abaqus Benchmarks Guide LE1：Abaqus 官方 NAFEMS LE1 benchmark 页面及其 `nle1xf8f.inp` 参考输入。

正式 Agent run 前完成了独立资格验证。冻结参考源 `tmp/nafems_le1_source/nle1xf8f.inp` 的 SHA-256 为 `859EFAF0E20E8569027E3683DAB1FC6D6A91C6AA326E95D10DEFD6D5201E0660`。Abaqus 2024 官方细网格 CPS8 ODB 给出 `d_sigma_yy=92.807560 MPa`、`reaction_x=-2.750000008 MN`、`reaction_y=-3.250000029 MN`。将同一模型重写为当前产品合同可独立检查的输入后，宿主 oracle 得到 `d_sigma_yy=92.807584 MPa`、`reaction_x=-2.749999980 MN`、`reaction_y=-3.249999982 MN`，有界 INP 合同返回 `[]`。这建立了正式 run 之前独立的求解、读取和输入合同口径。

相关回归在正式 run 前通过：`tests/test_public_mechanics.py` 为 26 passed，涉及文件 Ruff 检查通过。

## 冻结运行配置

批次目录：`D:/abaqus-runs/public-mechanics-v10-nafems-le1-elliptic-membrane-20260917`，seal SHA-256 为 `6703dec92f969b3b36ca0c14cc9e6fe2d1026ce529e6231f5dc1c48cee30c31e`。

- 单一 `abaqus_2024_coder`，Foundation profile 开启。
- API Catalog 使用 `full_with_discovery`，API lookup 预算 8 次。
- 绑定官方知识快照 `var/knowledge/official-structural-topics-20260916-v3`。
- Provider/model 为 `opencode-go / deepseek-v4-flash`。
- 模型请求上限 40，Concept lookup 上限 2，Abaqus 调用上限 18，Solver 调用上限 6，active wall 上限 2700 秒。
- HTTP timeout 600 秒、retry 0，任务与 runtime 并发均为 1。
- `preflight` 与真实 Provider `doctor` 通过后启动正式 run。

## 正式运行结果

run id 为 `pme2e-v5-df38094f77-nafems-le1-elliptic-membrane`。本轮没有 Provider timeout，也没有显式 resume。最终正式分类为 `task_failed`，持久化状态为 `failed`，failure stage 为 `model_budget`：模型请求达到 40/40，再发下一次请求会超过冻结预算。正式成绩保持失败。

| 指标 | 最终累计结果 |
|---|---:|
| model requests | 40 |
| tool calls | 49 |
| API lookup calls | 8 |
| Concept lookup calls | 0 |
| Abaqus calls | 13 |
| Solver calls | 0 |
| active wall | 约 1424.65 s |
| input tokens | 2,448,716 |
| cache-read tokens | 2,315,520 |
| output tokens | 71,762 |

最终 workspace 只有 `build_model.py`、`extract_results.py`、`probe_out.txt`、`probe2_out.txt` 以及 Abaqus rec/rpy 文件；没有生成 `model.inp` 或 `model.odb`，因此本轮不存在可做 post-hoc 物理验收的正式求解产物。

运行过程的主要问题是执行收敛而非题目知识缺失。模型前期花费多次请求探索 API；初版 `build_model.py` 在 `Model.Pressure(region=...)` 上连续把 Set、GeomSequence、Edge 作为 region 传入，均触发类型错误。随后在没有 Solver/ODB 的情况下提前编写并运行 `extract_results.py`，又进入 Abaqus Python/CAE import 环境探测。后期 `build_model.py` 被改写为标有 `TEMPORARY PROBE` 的网格/seed 探针，最终只写出 `probe2_out.txt`，没有回到生成 `model.cae/model.inp` 的交付路径。

## 知识与 API 的实际采用

结构化 history 中实际观察到的 capability load 只有 `abaqus-2024-coding`。没有实际加载任何 `linear-structural-fea-*` capability。因此虽然 Foundation profile 已开启，本轮不能声称 Foundation Skill 被采用，更不能据此声称 Foundation Skill 带来收益。

官方只读知识资源本轮确实进入了真实路径：观察到 `knowledge_list_directory(".")`、`knowledge_read_file("README.md")` 和 `knowledge_list_directory("scripts")`。README 提供了 Abaqus 2026 FD02 结构案例快照的来源、规范化方式以及“未验证 Abaqus 2024 兼容性”的边界；但本轮没有继续读取某个具体案例脚本正文，因此只能证明资源发现/元信息被采用，不能证明具体脚本帮助完成了 LE1。

API 索引也明确进入了真实建模与修复路径，并达到 8/8。查询/发现涉及 `ConstrainedSketch.Ellipse`、`ConstrainedSketch.ArcByCenterEnds`、`ConstrainedSketch.Spline`、`ConstrainedSketchGeometryModel.EllipseByCenterPerimeter`、`ConstrainedSketchGeometryModel.Line`、`Part.BaseShell`、`Part.seedEdgeBySize`、`Model.Pressure`、`Assembly.Set` 等；返回的精确卡片包括 BaseShell、ArcByCenterEnds、Spline、EllipseByCenterPerimeter 和 Line。

因此 v10 支持“官方知识快照的目录/README 与 API 索引被模型实际采用”的结论；不支持“Foundation Skill 被采用”或“Foundation Skill 已产生因果收益”的结论。

## 对后续案例扩展的含义

LE1 本身需要的曲边几何、平面应力、压力和局部应力读取都已有足够的权威资料与 verifier 合同；正式 run 却没有进入 Solver。这更像 routing、API 预算使用、错误修复收敛和阶段顺序的问题，而不是知识源覆盖不足。

下一题优先使用 NAFEMS LE10 Thick Plate Pressure。它与 LE1 共用椭圆平面尺度，但切换到 3D 厚实体、三维应力、实体单元与厚度方向离散化，可以在尽量少增加题意变量的同时直接检查 `linear-structural-fea-modeling/discretization/postprocess/verification` 是否真正被加载和采用。塑性、超弹性、几何非线性、动力学、Explicit 和接触仍保持独立 specialty suite。
