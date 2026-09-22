# 公开材料力学 v9：NAFEMS LE6 斜板压力案例扩展

2026-09-17。目标是在已有杆、梁、圆轴、平面应力和轴对称案例之外，加入板/壳表示、法向压力、壳截面上下表面应力和网格精度要求，并继续分别记录 Foundation capability、官方只读知识资源、API Catalog、Abaqus runtime 与独立物理验收证据。旧批次、旧 seal 和旧成绩不回填。

## 案例与参考资格

新增 `nafems-le6-skew-plate`，对应 NAFEMS P18.LE6 / TNSB Rev. 3 (1990) 的 skew plate under normal pressure。平面内为边长均为 1 m、锐角 30° 的平行四边形，厚度 0.01 m，`E=210 GPa`、`nu=0.3`。取 `A=(0,0,0)`、`B=(1,0,0)`、`D=(cos30°,sin30°,0)`、`C=B+D`，全板承受 700 Pa、沿 `-Z` 的均布法向压力。四边约束 `U3=0`；A 点额外约束 `U1=U2=0`，B 点额外约束 `U2=0` 去除面内刚体运动。

公开目标为板中心 E 点下表面最大主应力 0.802 MPa；载荷面积为 0.5 m²，因此边界总竖向反力目标为 +350 N。正式合同允许常规 shell/plate 表示和 `S4/S4R/S8R/S8R5/S9R5`，并要求网格含有精确中心节点，以便宿主独立读取中心壳截面结果。

来源：

- Abaqus 2024 Benchmarks Guide：`https://docs.software.vt.edu/abaqusv2024/English/SIMACAEBMKRefMap/simabmk-c-le6.htm`
- NAFEMS code-verification catalog：`https://www.nafems.org/publications/code-verification/nastran-code-verification/`
- NAFEMS R0006 背景：`https://www.nafems.org/publications/resource_center/r0006/`

Abaqus 2024 页面正文写有 `-7.0 kPa`，但同页给出的细网格应力约 0.862 MPa，与 0.802 MPa NAFEMS 参考只相差约 7.5%；若压力真为 7 kPa，线性结果应整体放大约 10 倍。因此本批次把 700 Pa 作为冻结题设，并把 Abaqus 页面中的 `7.0 kPa` 记录为文档异常，而不据此改写参考题目。

正式 Agent run 前完成了独立资格验证：参考模型使用 +Z 壳法向，CAE `Pressure(..., region=side1Faces, magnitude=700)` 写出 `SPOS` + `*Dsload ... P,700`，Abaqus 2024 中对应实际向 `-Z` 的压力。S8R5 中中心节点 section point 1 描述为 `SNEG, (fraction = -1.0)`，section point 5 为 `SPOS, (fraction = 1.0)`。参考 ODB 得到中心下表面最大主应力 0.820377 MPa、总反力 350.000003 N；有界 `model.inp` 合同检查返回 `[]`。这建立了正式 run 之前独立的求解与读取口径。

另有一个重要建模陷阱在参考资格验证时被排除：板中心分割后，实例 `edges` 同时包含外边界和内部 partition 线；若直接对所有实例边施加 `U3=0`，会错误约束内部线并把中心应力压低到约 0.2 MPa。正式合同因此只认可真实四条外边界。

## 冻结运行配置

批次目录：`D:/abaqus-runs/public-mechanics-v9-nafems-le6-skew-plate-20260917`，seal SHA-256 为 `3db60fa559bf06e02c9e90b2b62846d45e3b476f549479cfb53c89c3119ec928`。

- 单一 `abaqus_2024_coder`，Foundation profile 开启。
- API Catalog 使用 `full_with_discovery`，API lookup 预算 8 次。
- 绑定官方知识快照 `var/knowledge/official-structural-topics-20260916-v3`。
- Provider/model 为 `opencode-go / deepseek-v4-flash`。
- 模型请求上限 40，Concept lookup 上限 2，Abaqus 调用上限 18，Solver 调用上限 6，active wall 上限 2700 秒。
- HTTP timeout 600 秒、retry 0，任务与 runtime 并发均为 1。
- `preflight` 与真实 Provider `doctor` 通过后启动正式 run。

## 正式运行结果

第一次尝试在首个模型请求之后遇到 Provider HTTP 600 秒超时，run 进入 `interrupted`；当时约消耗 603.99 秒 active wall、1 次模型请求、2 次工具调用，尚未调用 API、Abaqus 或 Solver。该事件归类为 Provider 基础设施中断，不作为模型或物理失败。冻结协议允许一次显式 resume，因此重新执行 Provider doctor 后，从同一 history、累计预算和原 freshness 基线继续。

恢复后的最终正式分类为 `task_failed`，failure stage 为 `model_budget`：模型请求达到 40/40，再发下一次请求会超过冻结预算。正式成绩保持失败，不因后续 post-hoc 验收改写。

| 指标 | 最终累计结果 |
|---|---:|
| model requests | 40 |
| tool calls | 55 |
| API lookup calls | 8 |
| Concept lookup calls | 0 |
| Abaqus calls | 11 |
| Solver calls | 2 |
| active wall | 约 1813.05 s |
| input tokens | 3,419,850 |
| cache-read tokens | 3,083,008 |
| output tokens | 85,082 |

运行过程中先后遇到 `SectionAssignment`、`setMeshControls`、`setElementType` 的 receiver/region 容器错误，以及一次 shell element type 选择问题；之后成功生成 CAE/INP。中心节点修复阶段又出现过 `min()` 空序列错误。第一次 Solver 提交因生成的节点集文本错误而在 input processor 失败；修复后第二次 Solver 成功完成，最终 `model.odb` 为 fresh artifact。

Solver 成功后，Agent 的 `extract_results.py` 首次执行因未导入 `ELEMENT_NODAL` 而失败。模型随后正确补上 `from abaqusConstants import ELEMENT_NODAL`，但这次编辑耗尽第 40 次模型请求，未能再运行一次提取脚本并进入正式 verifier，因此正式结果停在 `model_budget`。

## 知识与 API 的实际采用

结构化 history 显示，本题实际加载的 capability 只有 `abaqus-2024-coding`。没有加载 `linear-structural-fea-formulation`、`linear-structural-fea-modeling`、`linear-structural-fea-discretization`、`linear-structural-fea-postprocess` 或 `linear-structural-fea-verification`；也没有 `knowledge_list_directory`、`knowledge_read_file` 或其他 `knowledge_*` 调用。因此本题不能用于声称新增 Foundation skill 或官方只读知识资源产生了收益。

API 索引则明确进入了真实建模/修复路径。实际查询涉及 `Model.Pressure`、`Part.SectionAssignment`、`Part.setMeshControls`、`Part.setElementType`、`MeshNodeArray.sequenceFromLabels`、`FieldOutput.getSubset`、`FieldOutputRequest.setValues`、`FieldValue.sectionPoint`、`SectionPoint.number`、`HomogeneousShellSection` 等，返回了 Pressure、sequenceFromLabels、setMeshControls、getSubset、SectionAssignment、FieldOutputRequest.setValues 等卡片/参数证据。API lookup 最终达到 8/8。

因此 v9 支持“API 索引被模型实际采用并进入 shell 建模与修复链路”的结论；不支持“Foundation skill 已被采用”或“Foundation skill 已带来因果收益”的结论。

## Post-hoc 独立物理验收

正式 run 结束后没有恢复或重启 Agent，也没有修改冻结批次状态、预算、workspace、INP 或 ODB。宿主以生产 `public_mechanics_oracle.py` 只读最终 ODB，并用同一冻结 Case 合同检查最终 `model.inp`。

| 指标 | 独立 ODB measurement | 参考值 | 相对误差 | 容差 |
|---|---:|---:|---:|---:|
| `center_lower_max_principal` | 779742.414 Pa = 0.779742 MPa | 802000 Pa | 2.7753% | 10% |
| `reaction_z` | 349.9999807 N | 350 N | 5.52e-8 | 0.2% |

最终 ODB 有 1281 个节点、400 个单元，材料为 `E=210 GPa, nu=0.3`，单一 `Step-1`。最终 `model.inp` 的有界合同检查返回 `[]`。

因此最终模型和 Solver 产物在 post-hoc 物理上合格，但正式 benchmark 结果仍保持 `task_failed/model_budget`。这里严格区分了 runtime success、physics correctness 和 formal benchmark outcome。

## 对后续案例扩展的含义

LE6 已把公开力学 benchmark 扩展到 shell/plate、法向压力、section point 与边界几何识别，证明 API 索引在更复杂表示中确实会被主动使用。同时 v6-v9 连续出现“最终或近最终物理结果正确，但在正式收尾前耗尽模型请求预算”的模式，说明当前更突出的限制是修复效率与终止效率，而不是单纯能否得到正确物理解。

下一批仍应优先增加新的表示/载荷族，例如 NAFEMS LE1 椭圆膜、LE2 圆柱壳 patch、LE3 半球点载荷、LE5 Z 型截面悬臂、LE10 厚板和 LE11 热弹性实体。它们应继续作为线性 Foundation benchmark；塑性、超弹性、几何非线性、动力学、Explicit 和接触保持独立 specialty suite，避免把不同知识包的效果混在同一总分里。
