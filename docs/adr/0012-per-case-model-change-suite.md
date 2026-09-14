# ADR-0012：用逐 Case gate 的正交变更套件扩展 L1

## 状态

已采纳

## 日期

2026-08-25

## 当时遇到了什么

ADR-0011 建立了 verified plate source → seeded scripts → requirement change → new/regression oracle 的首条 L1
链路，但中心孔只覆盖几何与网格拓扑变化。单个变更 Case 不能证明 Agent 能处理材料区域、section assignment
或载荷/边界语义，也容易把一个专用 evaluator 误当成通用 model-change capability。

继续复制 `plate_change.py` 会使 source validation、seed snapshot 顺序、预算、diff schema 和 gate 逐渐漂移；
反过来把三个物理 verifier 强行参数化，又会混淆孔洞边界环、双材料界面和 traction 控制模式三种不同的
成功定义。需要共享运行证据，但保持物理验收隔离，并让每个 Case 独立达到重复门槛。

## 最后决定

- L1 v1 固定为三个正交 Case：
  - `center_hole`：中心孔、边界环、孔边网格和应力集中；
  - `dual_material`：x=50 分区、两组 material/section、共节点 conforming interface；
  - `load_mode`：删除右端规定位移，改为完整右边界 +X 方向 210 MPa general surface traction。
- `evaluation/verified_plate_seed.py` 独占 source plate request/outcome/verification 与 artifact hash 校验。校验后
  缓存两份脚本的 immutable bytes，新 attempt 只从缓存写 seed，避免校验后 source 被修改的竞态；symlink
  在 resolve 前拒绝。
- `evaluation/model_change_runtime.py` 共享单 Coder、固定 Skill、Catalog/Concept/Planning off、预算、三个
  typed tools、terminal verification 和 state evidence；三个 Case 不复制 Agent/Service 装配。
- `verification/model_change_evidence.py` 共享 initial/final snapshot、byte-faithful CRLF diff、三阶段 execution
  provenance 和 `.sta` completion。`build_model.py` 必须改变；孔洞/双材料还需修改 extractor，load-mode
  必须保持 extractor hash 不变。
- 三个 Case 分别拥有 strict InputContract、OracleSpec、Measurements、Verification、RunVerifier 和 host-only
  ODB oracle。共享层不解释材料、孔洞或 traction 物理语义。
- suite gate 按 Case 统计；每个被选 Case 都必须在自己的三次中至少通过两次，suite 才通过。`6/9` 等总分
  不能掩盖某个 Case `0/3`。
- 不向 Skill、Catalog、task prompt 注入 reference 脚本、正确 API 调用或 ODB 容器配方。reference 只供
  licensed integration 和 private oracle 使用。

## 为什么这样选

三个 Case 分别对应工程变更最常见的三种轴：几何拓扑、区域属性、载荷/边界。它们共用同一 source 和
执行合同，因此能比较模型在不同变更语义下的行为；独立 verifier 又避免“替换期望数值”伪装成语义验收。
逐 Case gate 让项目诚实暴露当前能力边界：双材料已通过当前重复 gate，traction 仍受 CAE API 绑定长尾阻塞。

## 没有采用的方案

- 再增加多个孔洞、缺口或网格 Case：数量增加但仍只覆盖几何轴。
- 三份 evaluator 完整复制 source/runtime/report：会产生信任、预算和输出 schema 漂移。
- 一个包含大量 optional 字段的通用 plate verifier：会弱化 strict contract，并把不同失败语义混在一起。
- 按全部 attempt 总通过数设置 suite gate：允许简单 Case 抵消困难 Case，无法支持能力声明。
- 因 traction 长尾直接补入 Skill/API note：会重新回到无退出条件的语义填充，并污染 no-retrieval 基线。

## 带来的影响

- 有利：L1 从单个中心孔扩展为三类可区分、可独立追问的工程变更。
- 有利：verified seed、diff 和三阶段 provenance 只实现一次，case-specific oracle 保持严格。
- 有利：load-mode Case 证明数值相同不能代表控制模式改变；INP `DSLOAD/TRVEC` 与 ODB surface 几何必须同时
  成立。
- 代价：三个 verifier 都是固定 linear-static plate 合同，尚不是任意 Abaqus model change plugin API。
- 风险：OpenCode Go transport、tool retry 和 CAE API probe 长尾仍会导致完整 suite 不通过。

## 落实与确认

- 三个 deterministic reference 均在真实 Abaqus 2024 通过 licensed integration。
- `dual_material` 正式三重复为 2/3，通过自己的 gate；唯一失败在任务开始前 transport，两个 task-started
  attempt 均通过 hidden oracle。
- `load_mode` 首个 suite raw 为 0/3，其中一个完整正确解因 INP `SurfRight` 与 ODB `SURFRIGHT` 大小写
  过约束被误拒；修复 repository case matching 后，该 Agent 脚本在全新 workspace replay 通过。
- 修正后的 load-only 正式三重复仍为 0/3：两条 model-budget，一条 task-started infrastructure/tool-retry
  failure，因此不能把 posthoc replay 写成稳定 gate。
- 当前 suite 不通过；冻结证据为
  `benchmarks/coding/plate_model_change_suite_evaluation_20260825.json`。
- 后续中心孔 v2 将新指标改由宿主 oracle 独立验收并保持原 extractor；该职责变化见 ADR-0013。v2 licensed
  reference 为 1/1，正式三重复为 1/3（两次 pre-Abaqus transport timeout、一次完整 hidden-oracle pass）。
- shared runtime 后续参数化已有 Exact Catalog 模式。冻结 `load_mode` goal、source、Skill、模型、运行预算和
  verifier 后，full treatment 为 2/3，而 corrected off control 为 0/3；一个 full attempt 仍在 36 次模型
  请求和 10 次 Abaqus probe 后未进入 Solver。冻结证据为
  `benchmarks/coding/plate_model_change_treatments_20260825.json`。
- evaluator 后续将模型请求、Abaqus 和 Solver 上限暴露为显式 CLI 参数，默认保持 `36/12/3`。只把
  full-Catalog load-mode 扩为 `48/18/3` 的三重复仍是 2/3：一条通过轨迹需要 44 次模型请求，另一条在
  48 次模型请求和 16 次 Abaqus probe 后仍未进入 Solver。该结果支持保留显式高预算 profile，但不支持
  静默提高默认值或把预算当作收敛机制；证据继续冻结在同一 treatment 文件。
- 后续显式 typed-probe v2 将 diagnostic conclusion、Case-owned target 和 production adoption 作为宿主状态
  持久化；full-Catalog load-mode `48/18/3` 为 2/3，与 protocol-off control 相同。本批两条通过均无 probe，
  失败轨迹只有 7 条 inconclusive，因此只确认状态边界和正常路径不干扰，不确认任务收益。决定见 ADR-0014。

## 替代关系

- 被部分替代：[ADR-0013](0013-host-owned-model-change-acceptance.md) 将中心孔 Case 的新指标归属宿主，并要求
  保持原 extractor。

## 相关文档

- [项目架构](../architecture/overview.md)
- [一次 CodingRun 的工作流](../architecture/flows/simulation-run.md)
- [ADR-0011：以已验证 CodingRun 作为现有模型变更的种子](0011-verified-seed-model-change.md)
- [ADR-0004：以证据和物理验证定义任务成功](0004-evidence-first-physics-verification.md)
- [ADR-0013：模型变更新指标由宿主 oracle 验收](0013-host-owned-model-change-acceptance.md)
- [ADR-0014：用 typed probe 协议约束诊断结论和 production 收敛](0014-typed-probe-convergence-protocol.md)
