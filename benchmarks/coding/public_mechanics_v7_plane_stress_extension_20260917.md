# 公开材料力学 v7：平面应力均匀拉伸案例扩展

2026-09-17。目标是在 v6 圆轴纯扭转之后继续扩展公开材料力学端到端 benchmark，并观察当前 Foundation capability、API Catalog discovery/exact lookup 与只读知识资源的实际采用情况。旧批次、旧 seal 和旧成绩不回填。

## 新增案例与参考资格

新增 `plane-stress-uniform-tension`：二维矩形板 `x=0..100 mm`、`y=0..20 mm`、厚度 `2 mm`，`E=200000 MPa`、`nu=0.3`。左边界约束 `U1=0`，左下角额外约束 `U2=0` 去除刚体平移；右边界规定 `U1=+0.05 mm`，其余方向自由。允许 CPS4/CPS4R/CPS8/CPS8R，不允许用 3D、壳、梁或平面应变替代。

独立解析参考为 `epsilon_x=delta/L`、`sigma_11=E*epsilon_x`、`F=sigma_11*t*w`，因此目标为 `tip_u1=0.05 mm`、`reaction_x=-4000 N`、`mean_s11=100 MPa`。

在 Agent 正式运行前完成了以下资格验证：

- `uv run pytest tests/test_public_mechanics.py -q`：23 passed；相关 Ruff 检查通过。
- Abaqus 2024 宿主参考模型求解成功。
- 宿主参考 ODB 测得 `tip_u1=0.05000000074505806 mm`、`reaction_x=-4000.0 N`、`mean_s11=100.0 MPa`。
- 参考 `model.inp` 通过现有有界输入合同检查，返回空错误列表。

因此该题在正式 Agent 运行前已经有独立问题定义、解析目标、Abaqus 2024 参考运行和宿主输入检查，不依赖 Agent 自报结果建立 oracle。

## 冻结运行配置

批次目录：`D:/abaqus-runs/public-mechanics-v7-plane-stress-20260917`。

- 单一 `abaqus_2024_coder`，Foundation profile 开启。
- API Catalog 使用 `full_with_discovery`。
- 绑定知识资源快照 `var/knowledge/official-structural-topics-20260916-v3`。
- 模型请求上限 40，API lookup 上限 8，Concept lookup 上限 2，Abaqus 调用上限 18，Solver 调用上限 6，active wall 上限 2700 秒。
- Provider/model 为 `opencode-go / deepseek-v4-flash`；HTTP timeout 600 秒、retry 0；任务和 runtime 并发均为 1。
- 冻结 worker 的 `preflight` 与真实 Provider `doctor` 均通过后启动唯一正式 run。

## 正式运行结果

正式分类为 `task_failed`，failure stage 为 `model_budget`。下一次模型请求会超过 40 次 request limit，因此该批次不能记为 formal pass。

| 指标 | 结果 |
|---|---:|
| model requests | 40 |
| tool calls | 51 |
| API lookup calls | 8 |
| Concept lookup calls | 0 |
| diagnostic retrieval calls | 0 |
| Abaqus calls | 12 |
| Solver calls | 2 |
| active wall | 664.05 s |
| received input tokens | 2,218,358 |
| cache-read tokens | 2,135,808 |
| received output tokens | 55,088 |

运行中第一次 Solver 因输入问题失败，随后修复并成功完成 Abaqus/Standard；提取脚本还经历了节点索引和 `EVOL` 标量读取错误。最终留下 fresh `build_model.py`、`extract_results.py`、`model.cae`、`model.inp`、`model.odb`、`results.json` 等产物，最终 Solver 状态为完成。正式失败发生在模型预算耗尽，而不是最终 Solver 失败。

## 知识与 API 的实际采用

运行时暴露了 `abaqus-2024-coding`、五个线性 Foundation capability、API discovery/exact lookup、知识目录/文件读取以及三个 Abaqus runtime 工具。

实际结构化 history 中：

- 成功 `load_capability` 只有 `abaqus-2024-coding` 一次；五个 `linear-structural-fea-*` capability 均未实际加载。
- 未观察到 `knowledge_list_directory` 或 `knowledge_read_file` 调用。
- API discovery 实际发生，至少包括 `Part/cells` 和 `Part/faces` 两次查询。
- Exact lookup 实际用于 `Model.HomogeneousSolidSection`、`Part.setElementType`、`FieldOutputRequest.setValues`、`Part.seedPart`、`VertexArray.findAt`、`MeshNodeArray.getByBoundingBox` 等 API；也出现了 `Part.HomogeneousSolidSection`、`EdgeArray.findAt`、`Assembly.Set` 等未命中/不可用尝试。

因此 v7 首次给出了“API discovery + exact lookup 被真实采用”的证据，但仍不能说明新增 Foundation skill 或知识文件改善了任务，因为它们没有被加载/读取。Profile 暴露、实际采用、检索命中、运行完成和物理正确继续作为不同证据层记录。

## Post-hoc 独立物理验收

正式 run 结束后，没有修改冻结批次的任务、预算或 workspace；宿主独立只读最终 ODB，并对最终 INP 执行有界合同检查。

| 指标 | 独立 ODB measurement | 参考值 | 相对误差 |
|---|---:|---:|---:|
| `tip_u1` | 0.05000000074505806 | 0.05 | 1.49e-8 |
| `reaction_x` | -3999.9998474121094 | -4000.0 | 3.81e-8 |
| `mean_s11` | 100.0 | 100.0 | 0 |

独立读取还得到 1621 个节点、500 个单元、单一 `Step-1`，材料为 `E=200000 MPa, nu=0.3`；最终 `model.inp` 合同检查返回 `[]`。

也就是说，Agent 留下的最终模型和 ODB 在 post-hoc 物理上合格；正式分数仍保持 `task_failed/model_budget`，不能以事后验收回填为 pass。

## 对后续案例的含义

v6 圆轴扭转与 v7 平面应力都出现了相同模式：Agent 最终可以得到物理正确的模型，但在正式结束协议之前消耗完 40 次模型请求。下一轮一方面应继续补新的表示与载荷族，另一方面要继续记录正确结果出现后到正式终止之间的额外修复/请求成本。

优先扩展轴对称厚壁圆筒内压。它会新增 CAX 连续体、压力边界、径向/环向应力梯度和 Lamé 解析解，能够更有效地区分“基础线性题会做”与“真正覆盖不同建模表示和载荷”的能力。
