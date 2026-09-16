# 历史架构决定

以下记录在 2026-09-16 文档治理中从当前入口归档。保留原编号、原状态、当时背景、决定和后果；
只调整路径与相对链接。它们解释演进，不定义当前产品能力。

| 决策家族 | 原 ADR | 原状态 | 归档原因 | 后继决定 |
|---|---|---|---|---|
| 上游复用 | [0001：保持独立实现](0001-independent-from-upstream.md) | 已替代 | superseded：允许可追溯的实质复用，保留独立仓库 | [0005](../0005-traceable-upstream-reuse.md) |
| Agent 运行时 | [0002：LangGraph 分层多 Agent](0002-langgraph-hierarchical-multi-agent.md) | 已替代 | superseded：首轮转为单 Coder | [0008](../0008-pydantic-ai-harness-abaqus-coding-run.md) |
| 外部副作用与控制面 | [0003：可恢复控制平面](0003-durable-control-plane-and-execution-boundary.md) | 已替代 | superseded：不再作为首轮前置条件 | [0008](../0008-pydantic-ai-harness-abaqus-coding-run.md)；当前窄会话恢复见 [0020](../0020-resume-coding-runs-with-harness-step-persistence.md) |
| 应用结构 | [0006：模块化单体控制平面](0006-modular-monolith-control-plane.md) | 已替代 | superseded：旧控制面首轮路线撤出 | [0008](../0008-pydantic-ai-harness-abaqus-coding-run.md)；当前组件边界见 [0017](../0017-package-by-component-coding-run.md) |
| 模型适配 | [0007：StructuredModelPort](0007-structured-model-port.md) | 已替代 | superseded：产品改用 Pydantic AI 原生 provider/loop | [0008](../0008-pydantic-ai-harness-abaqus-coding-run.md) |
| 静态预检 | [0016：可回退的 abqpy 静态预检](0016-advisory-abqpy-static-preflight.md) | 已替代 | superseded：改为有分级证据的 ACI 决定；旧 advisory 兼容不等于旧 ADR 重新生效 | [0018](../0018-enforce-verified-abaqus-aci.md) |

当前事实由 [架构总览](../../architecture/overview.md)维护；返回 [当前决定](../README.md)。
当前根目录的其余记录仍保留有效架构约束，不因含有旧实现或实验段落而整篇归档，也不为减少数量合并重写。
