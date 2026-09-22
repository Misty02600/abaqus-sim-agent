# 公开材料力学 v8：轴对称厚壁圆筒内压案例扩展

2026-09-17。目标是在 v7 平面应力之后扩展公开材料力学端到端 benchmark，并分别记录 Foundation capability、官方只读知识资源、API Catalog、Abaqus runtime 与独立物理验收的证据。旧批次、旧 seal 和旧成绩不回填。

## 新增案例与参考资格

新增 `thick-cylinder-internal-pressure`：二维轴对称开口端厚壁圆筒截面，内半径 20 mm、外半径 40 mm、轴向长度 20 mm，`E=200000 MPa`、`nu=0.3`，内壁均布压力 100 MPa，外壁和两端自由，仅在 `(r=40,z=0)` 约束轴向位移去除刚体平移。允许 CAX4/CAX4R/CAX8/CAX8R。

独立参考采用 Lamé 厚壁圆筒解。目标为 `inner_u1=0.0196666666667 mm`、`outer_u1=0.0133333333333 mm`、r-z 截面积加权 `area_mean_s11=-33.3333333333 MPa`、`area_mean_s33=100 MPa`。轴对称应力平均按 r-z 截面积计算，不用 `EVOL`；后者包含旋转体的 `2πr` 权重，会得到不同的体积平均。

正式 Agent 运行前已完成资格验证：

- `uv run pytest tests/test_public_mechanics.py -q`：24 passed；相关 Ruff 检查通过。
- Abaqus 2024 宿主参考模型完成 CAE、Solver 和 trusted ODB oracle。
- 参考 ODB 得到 `inner_u1=0.01966666616499424`、`outer_u1=0.013333333656191826`、`area_mean_s11=-33.333333352208136`、`area_mean_s33=100.00000114440918`。
- 参考 `model.inp` 的有界输入合同检查返回 `[]`。

因此本题的物理目标和验收口径在正式 Agent run 前已经独立建立。

## 冻结运行配置

批次目录：`D:/abaqus-runs/public-mechanics-v8-axisymmetric-pressure-20260917`，seal SHA-256 为 `11a003c0aa4a3512a6e1a0cb6a0699e33c684fe712d41fb7f0c3b8233faf260d`。

- 单一 `abaqus_2024_coder`，Foundation profile 开启。
- API Catalog 使用 `full_with_discovery`，API lookup 预算 8 次。
- 绑定官方知识快照 `var/knowledge/official-structural-topics-20260916-v3`。
- Provider/model 为 `opencode-go / deepseek-v4-flash`。
- 模型请求上限 40，Concept lookup 上限 2，Abaqus 调用上限 18，Solver 调用上限 6，active wall 上限 2700 秒。
- HTTP timeout 600 秒、retry 0，任务与 runtime 并发均为 1。
- `preflight` 与真实 Provider `doctor` 均通过后启动唯一正式 run。

## 正式运行结果

正式分类为 `task_failed`，failure stage 为 `model_budget`。40 次模型请求预算耗尽，因此不能记为 formal pass。

| 指标 | 结果 |
|---|---:|
| model requests | 40 |
| tool calls | 51 |
| API lookup calls | 8 |
| Concept lookup calls | 0 |
| Abaqus calls | 9 |
| Solver calls | 1 |
| active wall | 854.46 s |

Solver 第一次且唯一一次提交即完成。后处理第一次因未导入 `INTEGRATION_POINT` 失败，随后修复并成功写出结果。最终 Agent `results.json` 为 `inner_u1=0.01966666616499424`、`outer_u1=0.013333333656191826`、`area_mean_s11=-33.33333365023136`、`area_mean_s33=100.0`。

## 知识与 API 的实际采用

结构化 history 显示：

- 成功加载的 capability 只有 `abaqus-2024-coding`；没有加载任何 `linear-structural-fea-*` capability。因此本题不能用于声称 Foundation skill 带来收益。
- 实际调用一次 `knowledge_list_directory({"path":"."})`，并读取 `README.md` 与 `scripts/gsi_mount_caemodel.py`。后者在 axisymmetric `BaseShell` 建模失败附近进入修复过程，属于真实官方知识资源采用。
- 模型主动 API 查询包含 `Assembly.Set.vertices`、`regionToolset.Region`，以及 receiver=`Assembly` 上对 `Set`/`SetFrom` 的 discovery。
- 宿主自动 API 提示还记录了 `Assembly.Set`、`Model.DisplacementBC`、`Model.Pressure`、`Part.setElementType`、`Part.BaseShell` 等卡片。自动提示与模型主动调用分开计证；API lookup 预算最终达到 8/8。

因此 v8 支持“API 系统实际进入建模/修复链路”和“官方只读脚本被实际读取”的结论；不支持“新增 Foundation skill 已被采用或已产生因果收益”的结论。

## Post-hoc 独立物理验收

正式 run 结束后没有修改冻结批次任务、预算、workspace 或结果。宿主另以 trusted oracle 只读最终 `model.odb`，得到：

| 指标 | 独立 ODB measurement | 参考值 |
|---|---:|---:|
| `inner_u1` | 0.01966666616499424 | 0.01966666666666667 |
| `outer_u1` | 0.013333333656191826 | 0.013333333333333334 |
| `area_mean_s11` | -33.33333365023136 | -33.333333333333336 |
| `area_mean_s33` | 100.0 | 100.0 |

最终 `model.inp` 合同检查返回 `[]`。最终模型和 ODB 在 post-hoc 物理上合格，但正式结果仍保持 `task_failed/model_budget`。

## 对后续案例扩展的含义

v6 圆轴扭转、v7 平面应力和 v8 轴对称内压连续出现“最终物理结果正确，但在正式验收收尾前耗尽 40 次模型请求”的模式。后续应继续扩展新的表示与载荷族，同时单独记录从“首次得到正确结果”到“正式终止”的额外请求成本。

下一题优先板/壳弯曲类。它能覆盖 shell 表示、法向压力、截面上下表面结果和网格敏感性，并更直接观察新增线性建模/离散化 skill 是否会被实际加载。
