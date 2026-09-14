# ADR-0013：模型变更新指标由宿主 oracle 验收

## 状态

已采纳

## 日期

2026-08-25

## 当时遇到了什么

中心孔 v1 同时要求 Agent 修改 `build_model.py` 和 `extract_results.py`，并把
`hole_boundary_max_s11` 写入 `results.json`。这样虽然能形成一条闭环，却把产品代码与验收代码的职责混在
一起：Agent 既实现孔洞，又实现孔边识别和应力指标，宿主再用另一份脚本核对相同概念。项目真正要证明的
是已有模型变更及回归控制，不是让 Agent 猜测或改写测试方法。

`extract_results.py` 还是已验证 source 的一部分。只要原有 `right_mean_u1`、`mean_s11` 和
`left_sum_rf1` 对新 ODB 仍然适用，保持其 bytes 不变比增加 Case 专用输出更能证明兼容性。孔洞拓扑、孔边
离散和应力集中则属于新需求 acceptance，应由模型不可见的 host oracle 独立拥有。

## 最后决定

- 对 `center_hole` v2，Agent 可修改 `build_model.py`，必须保持 `extract_results.py` byte-for-byte 不变。
- Agent 仍必须运行原 extractor 并检查三项原有 `results.json` 数值；不能通过通用 shell 伪造结果。
- 宿主从当前 ODB 独立计算孔洞边界环、孔心/半径、网格面积、孔边单元边数量、远场 S11 和孔边最大 S11，
  并与原材料、分析步、边界、输出和反力平衡合同一起形成最终结果。
- `build_model.py` 非空 patch、`extract_results.py` 空 patch、三阶段 execution provenance 和 hidden oracle
  必须同时通过。Agent 不能声明自己的物理验收成功。
- v1 request 与评测证据保留为历史；v2 使用新的冻结 request，不回写旧结果。

## 为什么这样选

这个边界让“实现”和“判断实现是否正确”由不同主体拥有。Agent 无法通过调整孔边选择或应力统计定义来
迎合验收；同时，保持原 extractor 能直接暴露模型变更是否破坏既有结果消费方。宿主反馈只返回失败事实，
允许 Agent 做最小模型修复，不泄漏 private oracle 代码。

## 没有采用的方案

- 继续要求 Agent 增加 `hole_boundary_max_s11`：会重复实现验收语义，并让 Case 专用指标污染稳定输出合同。
- 删除 Agent 侧结果提取：会失去原有 U/S/RF 消费路径的回归证据，也破坏固定三阶段合同。
- 把完整孔洞 reference 或 oracle 规则加入 Skill：会把评测答案变成产品提示词，无法衡量模型变更能力。

## 带来的影响

- 有利：hidden oracle 真正独立于 Agent 可编辑文件，验收更难被自评或实现偏差污染。
- 有利：原 extractor 成为兼容合同；空 patch 和当前 results provenance 都有机器证据。
- 有利：通过轨迹中，oracle 发现 partition 将左右边界拆段而 Agent 只约束半边的真实回归；Agent 根据反馈
  选择完整边界、重跑三阶段后通过，形成可讲解的修复闭环。
- 代价：Case 专属测量只存在 state/oracle evidence 中，不出现在 Agent 的稳定 `results.json`。
- 风险：这个决定提高验收可信度，不解决模型网关 transport 或 Agent 重复可靠性；v2 正式批次仍只有 1/3。

## 落实与确认

- `verification/plate_change.py` 将 `extract_script_changed` 改为 `extractor_preserved`，Agent results 只核对原三项。
- `evaluation/plate_change.py` 与 suite 的 diff contract 要求 build patch 非空、extractor patch 为空。
- licensed deterministic reference 使用变更后的 build script 和原 extractor，真实 Abaqus 2024 为 1/1。
- OpenCode Go/DeepSeek V4 Flash 正式三重复为 1/3：两次在任何 Abaqus 调用前因模型 API timeout 失败；唯一
  task-complete attempt 通过全部 hidden checks，`extract_results.patch` 为零字节。
- load-mode verifier 后续把聚合 traction gate 展开为 strict `traction_contract`：逐项记录 record count、
  type、magnitude、raw/normalized direction、follower 和 surface declaration 的实际值与 pass flag。上一批
  真实失败 INP 离线 replay 后，唯一 finding 从笼统的 TRVEC 失败收敛为
  `follower_actual=true`，并明确要求 `follower=OFF` 使 INP 生成 `*DSLOAD, follower=NO`。新 Product batch
  3/3 均首次通过、没有触发 verifier 修复，因此只确认正常路径不受影响和旧 artifact 可精确诊断，不声称
  已观察到 Agent 采纳该反馈。
- 冻结证据：
  `benchmarks/coding/plate_model_change_treatments_20260825.json`。

## 替代关系

- 部分替代：[ADR-0012](0012-per-case-model-change-suite.md) 中“中心孔必须修改 extractor”的决定。

## 相关文档

- [项目架构](../architecture/overview.md)
- [ADR-0004：以证据和物理验证定义任务成功](0004-evidence-first-physics-verification.md)
- [ADR-0011：以已验证 CodingRun 作为现有模型变更的种子](0011-verified-seed-model-change.md)
