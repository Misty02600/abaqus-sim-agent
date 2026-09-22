# 公开材料力学 v6：圆轴纯扭转案例扩展

2026-09-17。目标是扩展现有六题公开材料力学端到端 benchmark，并观察当前 Foundation capability、API Catalog 与只读知识资源在新物理族中的实际采用情况。旧批次、旧 seal 和旧成绩不回填。

## 新增案例与参考资格

新增 `circular-shaft-pure-torsion`：沿 X 的 1000 mm 实心圆截面 wire 轴，直径 20 mm，`E=200000 MPa`、`nu=0.3`，左端固支，右端施加绕 +X 的 `100000 Nmm` 集中扭矩。允许 B31/B32，截面必须为实心圆。

独立参考采用 Saint-Venant 圆轴扭转关系：`phi=T*L/(G*J)`、`tau_max=T*R/J`。在进入 Agent 正式运行前完成以下资格验证：

- `uv run pytest tests/test_public_mechanics.py -q`：22 passed；相关 Ruff 检查通过。
- Abaqus 2024 宿主参考模型使用 B31 + 半径 10 mm 圆截面，Solver 正常完成。
- 宿主参考 ODB 测得 `tip_ur1=0.08276057243347168 rad`、`reaction_mx=-100000 Nmm`、`surface_shear_nominal=63.66197723675813 MPa`，与解析目标一致。
- Abaqus 生成的参考 `model.inp` 通过新增有界输入合同检查。

因此该题在正式 Agent 运行前已经具备独立问题定义、解析目标、Abaqus 2024 参考运行和宿主输入检查，不依赖 Agent 自报结果建立 oracle。

## 冻结运行配置

批次目录：`D:/abaqus-runs/public-mechanics-v6-torsion-20260917`，seal SHA-256 为 `36f8e55849ab765b63c972b9307240e19c6c2ca13d41373e93a29e5d30239f48`。

- 单一 `abaqus_2024_coder`，Foundation profile 开启。
- API Catalog 模式为 `full_with_discovery`；每题 API lookup 预算 8 次。
- 绑定官方 beam 知识资源快照 `var/knowledge/official-beam-20260915-final`。
- 模型请求上限 40，Abaqus 调用上限 18，Solver 调用上限 6，active wall 上限 2700 秒。
- HTTP timeout 600 秒、retry 1；任务与 runtime 并发均为 1。
- `preflight` 与真实 Provider `doctor` 均通过后启动唯一正式 run。

## 正式运行结果

正式分类为 `task_failed`，failure stage 为 `model_budget`。Agent 在准备结束前耗尽 40 次模型请求上限，因此该批次不能记为 formal pass。

| 指标 | 结果 |
|---|---:|
| model requests | 40 |
| tool calls | 50 |
| API lookup calls | 8 |
| Abaqus calls | 13 |
| Solver calls | 1 |
| active wall | 609.71 s |
| received input tokens | 2,361,308 |
| cache-read tokens | 2,269,952 |
| received output tokens | 54,245 |

运行期间 Agent 进入了真实修复闭环，包括 CAE region/API 写法错误、ODB component 读取错误和 NumPy `float32` JSON 序列化错误。最终已经生成 fresh `build_model.py`、`extract_results.py`、`model.cae`、`model.inp`、`model.odb`、`results.json` 和 `report.md`，Solver 也实际完成；失败发生在模型预算耗尽，而不是 Solver 或物理 oracle 拒绝。

## 知识与 API 的实际采用

`preflight` 证明运行时同时暴露了 `abaqus-2024-coding`、五个 `linear-structural-fea-*` capability、`discover_abaqus_api`、`lookup_abaqus_api`、`knowledge_list_directory`、`knowledge_read_file` 和三个 Abaqus runtime 工具。

实际 history 中只有一次成功的 capability load，目标是 `abaqus-2024-coding`。没有加载五个 `linear-structural-fea-*` capability；正式报告中的 `api_discovery_queries=[]`、`concept_lookup_queries=[]`，也没有知识目录/知识文件工具调用。Exact API lookup 则实际消耗满 8 次预算，并进入 CAE/后处理修复过程。

因此本题支持“Exact API Card + runtime feedback 能参与完成新扭转案例”的观察，不支持“API discovery 或新增线性 Foundation 知识包已经带来任务收益”的结论。Profile/资源可见、模型实际加载、检索命中、运行成功和物理正确继续分开记录。

## Post-hoc 独立物理验收

正式 run 结束后，没有修改该冻结批次的任务、预算、workspace 或结果文件；另用生产宿主 `public_mechanics_oracle.py` 只读打开 Agent 最终 `model.odb`，再把 measurement 送入现有有界 INP 合同检查。

| 指标 | 独立 ODB measurement | 参考值 | 相对误差 |
|---|---:|---:|---:|
| `tip_ur1` | 0.08276057243347168 | 0.08276057040778557 | 2.45e-8 |
| `reaction_mx` | -100000.0 | -100000.0 | 0 |
| `surface_shear_nominal` | 63.66197723675813 | 63.66197723675813 | 0 |

最终 `model.inp` 的合同检查返回空错误列表。也就是说，Agent 留下的最终模型和 ODB 在 post-hoc 物理上已经合格；但由于它没有在 40 次请求预算内完成正式验收协议，正式分数仍保持 `task_failed/model_budget`，不能用事后 oracle 回填为 pass。

## 对下一轮案例扩展的含义

圆轴纯扭转已经把公开 suite 从轴向、弯曲、孔洞和 3D solid 扩展到 torsion，同时暴露了一个比物理建模更直接的工程问题：Agent 已经得到正确结果后仍继续消耗请求，当前结束策略/修复节奏需要单独测量。

下一批材料力学案例应继续补新的表示与载荷族，优先选择二维 plane-stress/membrane，以及 plate/shell bending 或厚壁圆筒 pressure/stress-gradient；这些题才能进一步观察新增 Foundation capability 是否会被主动加载，而不是继续堆同类 beam/truss 题。
