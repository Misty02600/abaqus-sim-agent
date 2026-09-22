# 验证证据与允许声明

## 如何读结果

当前实现以源码和合同测试为依据；真实模型观察、持证运行和物理验收以对应冻结报告为依据。
本页提供阅读路线，不重新计分，也不把旧批次结果升级为当前配置的验收。详细请求、hash、预算、每次失败
和统计只在原报告维护；本机 raw 文件不可用时，仍可从仓库报告了解方法和局限，但不能声称已重放原运行。

| 证据层级 | 能说明什么 | 不能代替什么 |
|---|---|---|
| 源码 / 离线合同测试 | 工具、状态、预算与错误分支接线 | 真实模型行为、许可证环境 |
| Provider smoke / FunctionModel | 分别证明实际模型交互或确定性协议路径 | Abaqus 运行与物理正确 |
| licensed reference integration | 参考脚本、runner、oracle 在本机运行 | 模型从零独立完成 |
| 产品 `execution_evidence` | declared deliverables、freshness、语法、runtime stage | 全部自然语言语义、物理验收 |
| 固定 Case `task_oracle` | 指定任务的 INP/ODB、结果与回归合同 | 任意 CAE 支持范围、稳定成功率 |
| 配对 / held-out 评测 | 冻结范围内的比较与不确定性 | 未控制变化的因果归因、跨任务外推 |

## 产品与会话

- [开放立方体产品 smoke](../../benchmarks/coding/product_open_cube_execution_evidence_20260831.json)：
  任意交付文件名、一次 CAE、零 Solver，通过产品执行验收；未做物理 oracle。
- [Provider 恢复 smoke](../../benchmarks/coding/coding_run_resume_provider_smoke_20260902.json)和
  [amendment / revision E2E](../../benchmarks/coding/coding_run_amendment_revision_provider_e2e_20260903.json)：
  支持本地会话与修订边界；不证明在途 Solver 恢复或完整自然语言交付。
- [问答迁移记录](../plans/done/0007-user-question-tool.md)：持久待答、答复幂等、混发拒绝和旧协议兼容已做离线验证；
  不能由此推断模型不再漏问。
- [运行配置记录](../plans/done/0005-align-target-agent-runtime.md)：保存有效配置和旧 run 兼容；
  离线 HTTP/schema 一致不证明服务端实际 thinking 强度或任务收益。

## 固定 CAE 案例

plate CPS4R、3D bar C3D8R、beam B31 的参考与 oracle 都有持证记录，Agent 在部分冻结配置下曾通过
各自 smoke gate。历史 plate 成绩早于后续 strict clarification 合同，不能作为新协议稳定性证据。
模型变更的共享 seed/provenance 已实现，但 plate suite 没有同一配置下的全套通过记录。

- [Coding 评测入口](../../benchmarks/coding/README.md)：完整历史、profile 和冻结报告。
- [strict 澄清评测](../../benchmarks/coding/plate_clarification_policy_evaluation_20260830.json)：
  决策层澄清与持证闭环分别统计，真实漏问/超时不能被旧通过结果掩盖。
- [B31 runtime allowlist](../../benchmarks/coding/beam_tension_b31_runtime_allowlist_evaluation_20260829.json)：
  固定 profile 的 gate 与正常路径兼容；不能单因果归于 allowlist 或 Catalog。
- [plate 变更 suite](../../benchmarks/coding/plate_model_change_suite_evaluation_20260825.json)、
  [B31 截面变更](../../benchmarks/coding/beam_section_change_evaluation_20260827.json)：
  每个 Case 独立验收新需求和原合同，不能用总通过数抵消失败 Case。
- [公开力学 results-first v4](../../benchmarks/coding/public_mechanics_results_first_v4_20260915.md)：
  新评测将关键数值/identity/provenance 与人工脚本审查分开；离线回放不回填旧正式成绩。

## API、文档与专业知识

- [Catalog candidate v3](../../benchmarks/api_catalog/modeling_candidate_v3_20260914.md)和
  [reference context v2](../../benchmarks/api_catalog/reference_context_discovery_v2_20260914.md)：
  候选内容覆盖与原题开发回放，不是默认替换或跨版本运行兼容保证。
- [一题一会话 API 意图评测](../../benchmarks/api_catalog/intent_suite_expansion_20260914.md)、
  [API 方案小闭环](../../benchmarks/api_catalog/api_discovery_loop_v1_20260915.md)、
  [历史失败反馈](../../benchmarks/api_catalog/historical_api_feedback_v1_20260915.md)：
  任务/数据/接线各有不同边界。历史反馈属于 development，零 Abaqus 的主 API 方案不能称物理闭环。
  当前产品的 receiver 目录 discovery 与这些实验的语义发现不同，见 [知识接入](knowledge-access.md)。
- [检索评测入口](../../benchmarks/rag/README.md)：Dense、BM25、RRF、证据交付与重排分别留证；
  当前证据不支持宣称 Hybrid 普遍更好，或把检索命中直接算成 Agent 收益。
- [bounded document policy 批次](../../benchmarks/coding/document_policy_phase3_batch2_20260912.json)：
  有部分分支可运行，尚未获得行为资格；工程完成与默认 off 并不矛盾。
- [官方脚本与只读资料](../../benchmarks/coding/knowledge_reference_resources_20260915.md)：
  只读工厂接线和一份适配脚本的 licensed 最小验证；无新真实 Agent 采用收益。

## 已采纳、尚待产品实施

Web 路线采用 FastAPI + assistant-ui，见 [ADR-0025](../adr/0025-local-web-workbench-over-coding-run.md)。
[三路 UI 接线比较](../research/ui-three-way-comparison-20260913.md)使用真宿主/假模型，
不能当已完成 Web 产品或仿真功能。产品工作由已有 PLAN-0009/0010 验收。CAE 脚本内常见 Solver 生命周期
调用的最小 AST 阻断已有离线合同测试，见 [PLAN-0011 完成记录](../plans/done/0011-block-solver-submit-inside-cae-script.md)；
该证据不证明动态 Python 无法绕行，也不等于持证仿真验证。

## 维护规则

新增证据时只更新对应支持结论和入口，详细结果放原本类型的报告。未通过、无效批次、人工干预、消费后的
held-out 和事后分析都保留身份；不把失败重跑到通过，也不为了文档统一改写冻结样本、seal 或源码绑定。
