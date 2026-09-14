# 架构决定

## 当前有效

- [ADR-0025：在 CodingRun 宿主之上增加本地 Web 工作台](0025-local-web-workbench-over-coding-run.md) — 已采纳，产品未实施；FastAPI + assistant-ui、REST + 轻量 SSE，由PLAN-0008/0009/0010实施与验收。

- [ADR-0024：ask_user与宿主待答](0024-host-owned-user-question-waiting.md) — 已采纳；局部替代ADR-0008/0020的新默认交接，旧profile保持原协议。

- [ADR-0004：以证据和物理验证定义任务成功](0004-evidence-first-physics-verification.md) — 已采纳
- [ADR-0005：有来源地吸收与重构 AbaqusAgent](0005-traceable-upstream-reuse.md) — 已采纳
- [ADR-0008：首轮采用 Pydantic AI Harness 单 Coder 的原生 Abaqus CodingRun](0008-pydantic-ai-harness-abaqus-coding-run.md) — 已采纳
- [ADR-0009：使用官方文档、abqpy AST 与运行证据构建精确 Abaqus API Catalog](0009-exact-abaqus-api-catalog.md) — 已采纳
- [ADR-0010：以显式受预算工具接入官方建模概念检索](0010-budgeted-official-concept-retrieval.md) — 已采纳
- [ADR-0011：以已验证 CodingRun 作为现有模型变更的种子](0011-verified-seed-model-change.md) — 已采纳
- [ADR-0012：用逐 Case gate 的正交变更套件扩展 L1](0012-per-case-model-change-suite.md) — 已采纳
- [ADR-0013：模型变更新指标由宿主 oracle 验收](0013-host-owned-model-change-acceptance.md) — 已采纳
- [ADR-0014：用 typed probe 协议约束诊断结论和 production 收敛](0014-typed-probe-convergence-protocol.md) — 已采纳
- [ADR-0015：产品 Coder 不暴露通用 Shell](0015-disable-general-shell-for-product-coder.md) — 已采纳
- [ADR-0017：以扁平组件图组织 CodingRun 产品包](0017-package-by-component-coding-run.md) — 已采纳
- [ADR-0018：以分级 ACI 强制生产脚本语法和已验证 API 合同](0018-enforce-verified-abaqus-aci.md) — 已采纳为显式 profile
- [ADR-0019：分离产品执行证据与测评物理 oracle](0019-separate-product-execution-evidence-from-evaluation-oracles.md) — 已采纳
- [ADR-0020：使用 Harness StepPersistence 续跑中断的 CodingRun](0020-resume-coding-runs-with-harness-step-persistence.md) — 已采纳
- [ADR-0021：用 Amendment 和 Task Revision 延续 CodingRun](0021-use-amendments-and-task-revisions-for-continuation.md) — 已采纳；部分替代 ADR-0020 的不可修改/terminal 限制
- [ADR-0022：先独立评测 BM25、Dense 与 RRF，再决定是否接入 Agent](0022-evaluate-bm25-dense-rrf-before-agent-adoption.md) — 已采纳；不进入默认Concept路径，仅有默认关闭的Solver diagnostic advisory实验profile
- [ADR-0023：在纯编辑模式写入前执行调用方声明的范围合同](0023-constrain-explicit-reference-edits-before-writing.md) — 已采纳为默认关闭的单表达式引用编辑 profile

## 已替代

- [ADR-0001：与上游 AbaqusAgent 保持独立实现](0001-independent-from-upstream.md) — 由 ADR-0005 替代
- [ADR-0002：使用 LangGraph 构建分层多 Agent 系统](0002-langgraph-hierarchical-multi-agent.md) — 由 ADR-0008 替代
- [ADR-0003：分离可恢复控制平面与 Abaqus 执行边界](0003-durable-control-plane-and-execution-boundary.md) — 由 ADR-0008 替代
- [ADR-0006：使用模块化单体和端口适配器组织控制平面](0006-modular-monolith-control-plane.md) — 由 ADR-0008 替代
- [ADR-0007：通过 StructuredModelPort 隔离模型提供方](0007-structured-model-port.md) — 由 ADR-0008 替代
- [ADR-0016：以固定 abqpy 与 BasedPyright 提供可回退的静态预检](0016-advisory-abqpy-static-preflight.md) — 由 ADR-0018 替代

被替代 ADR 保留当时问题、取舍和未完成路线，不能用来描述当前首轮产品调用链。当前首轮边界以
ADR-0008 和架构文档为准；ADR-0020/0021 提供文件后端的中断续跑和显式会话修订，不恢复旧 LangGraph、
多 Agent 或持久 Job Service 路线。其余方向仍需要新的任务证据和新的 ADR。
