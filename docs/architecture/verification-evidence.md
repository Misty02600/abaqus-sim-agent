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

- [Phoenix / MLflow 本地 OTel 对照](../../benchmarks/observability/phoenix_mlflow_poc_20260927.md)：5 个现有
  确定性场景产生的同一批 49 spans 在两家均持久保存并通过重启读回；原始消息映射、官方适配探针、评分
  桥接和业务身份限制分开记录。该报告冻结的是替换前的隔离 PoC，后续产品接入单独验收如下。

- [Phoenix 本机接入验收](../../benchmarks/observability/phoenix_local_integration_20260927.md)：产品使用官方
  SDK，移除完整 Logfire SDK；同 5 个确定性场景经真实本机服务保存消息、业务身份、Case 和评分，重启后可
  读回。离线回归另覆盖并发重复 Case、span_tree、300K 字符输入、失败、后台停止和恢复。大输入只验证本地
  SDK 序列化，不证明接收体积上限。独立 wheel 安装通过，无真实模型、持证执行或物理收益声明；完成范围见
  [PLAN-0014](../plans/done/0014-phoenix-local-observability.md)。

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

从[简短案例清单](../../benchmarks/coding/domain_mechanics_inventory.md)按物理能力查阅领域仿真Case，经典解析、厂商与NAFEMS仅作为来源标签。
[合同与证据详表](../../benchmarks/coding/domain_mechanics_case_details.md)分列逐题判据、参考资格、正式Agent结果与事后修正，以及当前入口漂移和本机资源依赖。
这里的端到端表示领域任务Eval的运行范围，不另立一类成绩，也不与参考脚本/假模型工程测试混计。

当前领域仿真保留20个Case定义，新批次默认选15题评分；显式选择历史题只作非计分诊断。新v31沿用v26的类型化metadata、冻结名单和原生执行入口；
新数值评分比较提交结果与隐藏参考值；不再独立复测ODB或要求两套提取数值一致，保留实际建模、求解、提取和交付物的执行证据。
题设继续明确几何、材料、载荷与取值方法；新入口不解析INP判断物理建模或写法，也不要求逐项人工确认。通过表示实际执行、交付及提交数值指标通过，不宣称自动核实了全部建模细节。模型题设及反馈不主动提供参考答案，评测侧保存每次完成声明的文件副本、累计用量与独立评分，最多三次提交、两轮反馈。
首次与最终自动评分分别报告；评分器故障单列测评错误，未提交明确记未交付，普通产品验收不启用这些控制。
外部整包参考附件未经答案审查，新入口拒绝附加；不承诺公开题答案对模型绝对未知。旧批次不回填新规则。
原生Dataset调用现有单题Service，单题/整批规则对返回证据评分；CaseLifecycle投影逐题进度，完整报告在调用结束时保存，report独立只读重建。不再生成需求待审门槛，报告范围见[结果报告约定](../../benchmarks/coding/result_reporting.md)。
公开力学的`--phoenix`在真实attempt外围建立追踪，报告通过带摘要的观测记录关联已有span并复制评分；
按原生报告duration_scope区分实际执行与只读投影耗时，也不由Phoenix决定验收。恢复新建attempt追踪，开关每次显式指定。
接线和离线校验不代表15题已完成真实Agent验收。旧Gold独立运行入口已退出；冻结报告与输入保留。输入变体的交互结果单独报告，不增加仿真题权重。
模型变更的共享seed/provenance继续使用，但plate suite没有同一配置下的全套通过记录。

- [Coding评测入口](../../benchmarks/coding/README.md)：当前路由与证据边界。
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
- [runtime feedback repair 资格记录](../../benchmarks/coding/runtime_feedback_repair_qualification_20260923.md)：
  四个坏样本均由 Abaqus 2024 复现失败，静态层形成两项命中和两项未命中；后续
  [真实模型 exploratory paired pilot](../../benchmarks/coding/runtime_feedback_repair_paired_v1_20260923.md)为
  1 win / 7 tie / 0 loss，但 token 成本上升、唯一 win 未跨重复稳定，且 source seal 未覆盖完整 Agent 调用链；
  不支持默认开启、普遍收益或严格因果声明。
- [runtime feedback repair v2 协议](../../benchmarks/coding/runtime_feedback_repair_protocol_v2.md)：后续配对在两臂
  固定同一 mandatory Catalog / `automatic_verified_only`，只比较 BasedPyright off/on；尚无新的真实模型成绩。

## 已采纳、尚待产品实施

Web 路线采用 FastAPI + assistant-ui，见 [ADR-0025](../adr/0025-local-web-workbench-over-coding-run.md)。
[三路 UI 接线比较](../research/ui-three-way-comparison-20260913.md)使用真宿主/假模型，
不能当已完成 Web 产品或仿真功能。产品工作由已有 PLAN-0009/0010 验收。CAE 脚本内常见 Solver 生命周期
调用的最小 AST 阻断已有离线合同测试，见 [PLAN-0011 完成记录](../plans/done/0011-block-solver-submit-inside-cae-script.md)；
该证据不证明动态 Python 无法绕行，也不等于持证仿真验证。

## 维护规则

新增证据时只更新对应支持结论和入口，详细结果放原本类型的报告。未通过、无效批次、人工干预、消费后的
held-out 和事后分析都保留身份；不把失败重跑到通过，也不为了文档统一改写冻结样本、seal 或源码绑定。
