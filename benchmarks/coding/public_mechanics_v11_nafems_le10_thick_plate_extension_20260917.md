# 公开材料力学 v11：NAFEMS LE10 厚板案例扩展

2026-09-17。目标是在既有线性 Foundation 案例之外加入三维厚板弯曲、二次实体单元、曲面压力、厚度方向离散化和局部应力读取，并分别记录 Foundation capability、官方只读知识资源、API Catalog、Abaqus runtime 与独立物理验收证据。旧批次、旧 seal 和旧成绩不回填。

## 案例与参考资格

新增 `nafems-le10-thick-plate-pressure`，对应 NAFEMS P18.LE10 / TNSB Rev. 3 (1990) 的 thick plate benchmark。模型为四分之一椭圆环形厚板：内椭圆半轴 2.0 m × 1.0 m，外椭圆半轴 3.25 m × 2.75 m，厚度 0.6 m，`E=210 GPa`、`nu=0.3`。`x=0` 侧面约束 `U1`，`y=0` 侧面约束 `U2`；外椭圆柱面约束 `U1=U2`，外椭圆边厚度中面 `z=0.3 m` 约束 `U3`；上表面 `z=0.6 m` 施加 1 MPa 均匀压力。

公开目标为 `D=(2,0,0.6)` 的全局 `S22=-5.38 MPa`。正式合同要求完整 3D 实体，允许 `C3D20/C3D20R/C3D10/C3D10HS/C3D10M`，网格必须含精确 D 节点；宿主以 D 点未平均 `ELEMENT_NODAL S22` 贡献均值作为读取口径，并以外椭圆边厚度中面约束节点的 `RF3` 总和检查竖向平衡。理论压力合力目标为 `5.448699758569798 MN`。

正式 Agent run 前使用 Abaqus 官方 LE10 输入完成独立资格验证。冻结参考源 `tmp/nafems_le10_source/nle10fkf.inp` 的 SHA-256 为 `D73139382D5BC745837BE72320E01DBC82BF15CE3842C6041D7E5D622E63A783`。资格验证副本只位于 `tmp/nafems_le10_qualification/`，未修改可信源。Abaqus 2024 细网格 `C3D20` 的独立读取结果为 `d_sigma_yy=-5.6431825 MPa`、`reaction_z=5.44834515625 MN`、`volume=3.269007157534361 m^3`；应力相对 NAFEMS 目标约 4.9%，反力相对理论压力合力约 0.0065%。有界 INP 合同对资格验证输入返回 `[]`。

相关回归在正式 run 前通过：`tests/test_public_mechanics.py` 为 27 passed，涉及 LE10 文件的 Ruff 检查通过。

## 冻结运行配置

批次目录：`D:/abaqus-runs/public-mechanics-v11-nafems-le10-thick-plate-20260917`，seal SHA-256 为 `0e2146889016b4cbc39028bbf3434914f18f9dc1154db70e1236c1bba7ee4055`。

- 单一 `abaqus_2024_coder`，Foundation profile 开启。
- API Catalog 使用 `full_with_discovery`，绑定 `var/api-catalog/abaqus-2024-reference-context-v2`。
- 绑定官方知识快照 `var/knowledge/official-structural-topics-20260916-v3`。
- Provider/model 为 `opencode-go / deepseek-v4-flash`。
- 模型请求上限 40，API lookup 上限 8，Concept lookup 上限 2，Abaqus 调用上限 18，Solver 调用上限 6，active wall 上限 2700 秒。
- HTTP timeout 600 秒、retry 0，任务与 runtime 并发均为 1。
- `preflight` 通过；正式配置暴露 18 个 tools 与 11 个 capabilities。暴露只说明可用，不等于模型实际加载或采用。

## 正式运行结果

run id 为 `pme2e-v5-6b00ba3e12-nafems-le10-thick-plate-pressure`。最终正式分类为 `task_failed`，持久化状态为 `failed`，failure stage 为 `model_budget`：模型请求达到 40/40，再发下一次请求会超过冻结预算。40 个 HTTP attempt 全部 completed，0 failed，因此本轮不是 Provider 中断。

| 指标 | 最终累计结果 |
|---|---:|
| model requests | 40 |
| tool calls | 50 |
| API lookup calls | 5 |
| Concept lookup calls | 0 |
| Abaqus calls | 10 |
| Solver calls | 1 |
| active wall | 约 1280.97 s |
| input tokens | 3,314,915 |
| cache-read tokens | 3,180,032 |
| output tokens | 79,565 |

与 LE1 不同，本轮已经走通了实际建模、Solver 和 ODB 后处理。最终工作区包含 fresh `build_model.py`、`extract_results.py`、`model.cae`、`model.inp`、`model.odb` 和 `results.json`。最终模型为单实例 `PART-1-1`，12231 个节点、2304 个 `C3D20R` 单元；Solver 明确完成，最终提取得到：

- `d_sigma_yy = +3772.912353515625 Pa`
- `reaction_z = 5,448,699.7265625 N`
- `volume = 3.2692198505974375 m^3`

反力与压力合力几乎完全一致，体积也合理，但 D 点 `S22` 相对 `-5.38 MPa` 参考不仅差约三个数量级，而且符号相反，因此这是实际物理结果失败，不能由“Solver 成功”替代。

## Post-hoc 独立检查

正式 run 结束后没有修改冻结 state、workspace、INP 或 ODB。生产 `public_mechanics_oracle.py` 只读读取最终 `model.odb`，独立得到同样的 `d_sigma_yy=3772.912353515625 Pa`、`reaction_z=5448699.7265625 N` 和 `volume=3.2692198505974375 m^3`。因此错误的局部应力不是 Agent 自己的 `results.json` 幻觉或简单提取脚本伪造，而是最终求解模型本身的结果。

同样在不修改正式工作区的前提下，使用冻结 v11 snapshot 的有界 INP checker 对最终 `model.inp` 与独立 ODB measurement 检查，返回：

`['thick-plate support boundary mismatch']`

这提供了一个与错误局部应力一致的输入层证据：最终模型的支承集合没有精确满足 LE10 合同。正式分类仍保持 `task_failed/model_budget`；post-hoc 证据只用于解释失败，不能回填为正式 verifier 结果。

## 知识、Skill 与 API 的实际采用

结构化 history 中实际加载的 capability 只有 `abaqus-2024-coding`。没有实际加载任何 `linear-structural-fea-*` Foundation Skill，包括 formulation、modeling、discretization、postprocess 和 verification。因此虽然 Foundation profile 已开启，本轮不能声称 Foundation Skill 被采用，更不能据此声称 Foundation Skill 对 LE10 有收益。

官方只读知识池确实进入真实路径：模型调用了 `knowledge_list_directory(".")`、`knowledge_read_file("README.md")`，并读取 `scripts/gsi_lug_caemodel.py` 的前 80 行。这可以证明共享知识池被采用；该脚本是通用弹性 lug 示例，并不是 LE10 专用参考，所以不能把它等价成 Foundation Skill 采用或 LE10 物理正确性的证据。

API 索引也明确进入了真实实现和修复路径。结构化记录包括：

- `Part.addNodes`、`Part.addElements`：active lookup，均 `not_found`；
- `ConstrainedSketchObject / ellipse`：discovery，无候选；
- `FieldOutput.getSubset`：lookup 命中，并返回 runtime note；
- `Mdb.ModelFromInputFile`：lookup 命中；
- `Part / addNodes addElements node element mesh`：discovery，无候选；
- `openOdb`：自动 lookup 命中，并返回 node-array nesting runtime note。

因此 v11 支持“官方知识池与 API 索引都被模型实际使用”的结论；不支持“Foundation Skill 被实际加载”或“Foundation Skill 已产生收益”的结论。

## 对下一轮案例的含义

LE10 暴露了比 LE6 更深的一层问题。LE6 是正式预算失败但 post-hoc 物理通过；LE10 则是正式预算失败、Solver 成功、全局平衡正确，但局部应力和输入支承合同都失败。这说明仅靠“能建模、能求解、能查 API”还不足以覆盖局部物理语义和建模约束。

下一轮线性 Foundation 扩展优先考虑 NAFEMS LE3 Hemisphere with Point Loads、LE5 Z-section Cantilever、LE2 Cylindrical Shell Patch Test。三者分别增加曲面壳+点载荷、开口薄壁截面扭转、壳单元 patch/一致性问题，和 LE1/LE6/LE10 的重叠较小。LE11 的温度载荷更适合放入 thermo-mechanical 扩展，不并入纯线性 Foundation 评分。

同时应把“Foundation Skills 已暴露但没有被模型加载”作为单独的 routing/adoption 问题继续测量；它与题目能否求解、API 是否命中、物理 verifier 是否通过是不同证据层。
