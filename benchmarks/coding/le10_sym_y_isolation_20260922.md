# LE10：只改正 SYM_Y 支承集合的独立求解

2026-09-22，用户在只读调查后明确同意执行这个最小验证。仓库基线为
`d0e5d55368d7668dbd3a00510d50fa777e790633`。

**只改正一个支承集合，D 点应力就恢复到原合同容差内。** 原结果为 +0.003773 MPa，修正后为
−5.562129 MPa，与参考 −5.38 MPa 相差 3.3853%，通过原来的 10% 容差。反力和有界输入检查也通过。
这提供了直接的单变量实验支持：错误的 Y 对称支承是该冻结模型局部应力严重异常的主要原因。

## 问题和唯一改动

原批次是 [公开力学 v11 LE10](public_mechanics_v11_nafems_le10_thick_plate_extension_20260917.md)，
正式结果为 `task_failed/model_budget`。原始脚本的注释要求 `y=0 → U2=0`，但 `SYM_Y` 用 `J==0`
选择节点。该参数网格中 I 控制角度，J 控制内外椭圆之间的径向位置，因此选中了内椭圆柱面；y=0
应对应 I=0。

本实验在新 ASCII 目录 `D:/abaqus-runs/le10-sym-y-isolation-20260922` 中复制最终 `model.inp`，
只替换 assembly `*Nset, nset=SYM_Y` 的成员列表：从错误面的535个节点改为 y=0 面的183个节点。
选取条件为 `abs(y) < 1e-7`，实际选中的183个输入节点均为 y=0。

按完整边界自由度比较，这一改动移除了528个多余的 U2 约束，补回169个缺失的 U2 约束。
X对称、外椭圆面内约束和外椭圆厚度中面 U3 支承保持不变。输入逐 block 比较只发现这个 NSET
成员列表不同；该 block 的关键字、名字和实例归属也未改。

网格节点、单元、C3D20R 类型、材料、压力、分析步、输出请求、Job 名和 Solver 命令均保留。
本实验直接改变 INP，不重新运行 CAE，不生成新的 CAE，也未修改原建模脚本生成器。

## 执行与检查

- 新运行没有调用 Provider、没有恢复 Agent/trial；只执行一次 Abaqus/Standard 2024 Solver，随后执行
  一次 trusted Abaqus Python 独立读取。
- 使用原 v11 snapshot 的宿主 runner、Case、`public_mechanics_oracle.py` 和有界 INP checker。
  执行前394项批次绑定、2629项外部 runtime 文件以及 launcher hash 全部匹配。
- Solver 命令仍为 `abq2024.bat job=model input=model.inp interactive`；没有新增CPU、内存、精度等参数。
  新工作区为空，Solver耗时52.16秒，exit code 0、未超时、stage通过，日志明确 `COMPLETED`，无诊断警告/错误。
- 独立读取耗时2.18秒，exit code 0、未超时；读取前后 ODB hash 相同。
- D 点仍按全局坐标 `(2,0,0.6)` 查找，取最后一帧未平均 `ELEMENT_NODAL S22` 的贡献均值。
  反力仍取外椭圆厚度中面节点的 RF3 总和；容差未改。
- 原组使用已有独立 measurement；本次只重新求解修正组，没有重新求解原组。

## 结果

| 指标 | 原始模型 | 仅修正 SYM_Y | 冻结参考/要求 |
|---|---:|---:|---:|
| D点全局 S22 / MPa | +0.003772912 | −5.562129 | −5.38，容差10% |
| S22 相对参考误差 | 100.0701% | 3.3853% | 通过 |
| 竖向总反力 / N | 5,448,699.726563 | 5,448,699.738281 | 5,448,699.758570，容差0.2% |
| 体积 / m³ | 3.269219850597 | 3.269219850597 | 保持相同 |
| 节点/单元 | 12231 / 2304 | 12231 / 2304 | 节点坐标及单元连接完全相同 |
| 有界 INP checker | support boundary mismatch | `[]` | 通过 |
| 独立模型身份检查 | 本次未重跑原组检查 | `[]` | 修正组通过 |

修正组反力相对参考误差为 `3.7236e-9`。两份独立 measurement 的节点、单元、材料和分析步名称
完全相同。原组反力已接近平衡，修正组反力仍接近平衡，而局部应力大幅改变，进一步说明整体反力
平衡不能单独判断局部应力或支承位置是否正确。

## 可以支持与不能支持的结论

- 对这一个冻结几何/网格，保持其他输入不变，仅纠正 SYM_Y 就足以让原来失败的应力指标通过原合同。
  因而已有比“支承错配与应力异常共同出现”更强的因果证据。
- 剩余3.3853%差异没有在本实验中进一步归因；没有做网格收敛或单元公式对照，也不宣称精确解或物理实验验证。
- 这是由调查者定位错误后执行的输入修正。没有证明 Agent 能自主发现或修复，也没有测试 Foundation
  Skill 的路由、正文采用或任务收益。
- 原正式 run 仍为 `task_failed/model_budget`。本次检查原批次全部539个文件，运行前后路径集合和
  SHA-256 均相同；没有回填成绩、替换旧模型、修改 Prompt/Skill 或调整产品协议。

## 证据

- [实验定义与预检 manifest](D:/abaqus-runs/le10-sym-y-isolation-20260922/manifest.json)
- [只改一个集合的 INP diff](D:/abaqus-runs/le10-sym-y-isolation-20260922/evidence/input.diff)
- [修正输入](D:/abaqus-runs/le10-sym-y-isolation-20260922/workspace/model.inp)
- [结果汇总](D:/abaqus-runs/le10-sym-y-isolation-20260922/result.json)
- [独立 measurement](D:/abaqus-runs/le10-sym-y-isolation-20260922/evidence/measurement.json)
- [Solver 执行记录与 raw log 路径](D:/abaqus-runs/le10-sym-y-isolation-20260922/evidence/solver_execution.json)
- [独立读取执行记录](D:/abaqus-runs/le10-sym-y-isolation-20260922/evidence/oracle_execution.json)
- [实验驱动脚本](D:/abaqus-runs/le10-sym-y-isolation-20260922/experiment.py)
- [新批次文件 seal](D:/abaqus-runs/le10-sym-y-isolation-20260922/seal.json)

关键 SHA-256：

- 原输入：`6b63c27b6129751540499d9844ec4442146dafc50a5ef07f2c9121b5a4ec06c9`
- 修正输入：`420c7efb650c0a7f882c253267a300355b8d0c5a6ed71c0896ab7efc162d448b`
- 修正 ODB：`1a9fb80ea6863152e497b12215bfa063f9ea64d3d3bcd40f1d4e791969fa974c`
- 冻结独立读取器：`fcde964ba22d80a2cca6e02ca1593f60f7e9c6fc757c3d9ccf68e00a1b59fc0b`
