# ADR-0002：使用 LangGraph 构建分层多 Agent 系统

## 状态

已替代

## 日期

2026-08-19

## 当时遇到了什么

项目的首要目标是形成面向 Agent/大模型应用开发秋招的深度作品。上游项目已经以 Interpreter、Architect、Input Writer、Runner、Reviewer 和 Visualization 等角色组织流程；我们的项目如果退化成单 Agent 或普通脚本流水线，既不满足作品定位，也无法沉淀多 Agent 协作、状态所有权和故障路由方面的实践。

同时，Abaqus 仿真包含长时间 Job、人工审批、失败修复循环和恢复需求，纯粹的自由对话式 Agent 不适合承担全部控制流。

## 最后决定

使用 LangGraph 构建分层多 Agent 系统：父图负责 Supervisor、全局阶段、人工暂停、恢复和终止条件；专业 Agent 或子图负责需求解释、建模设计、输入生成、执行协调、错误诊断和结果验证。确定性的校验、文件操作和 Job 控制作为受权限约束的工具或执行器，不伪装成 LLM 推理。

一个角色只有在具备独立目标、结构化输入输出、允许的工具集合和成功/失败条件时才称为 Agent。普通状态查询、路由函数和文件读写保持为普通代码。

## 为什么这样选

LangGraph 可以显式表达父图、子图、条件循环、checkpoint 和 interrupt/resume；它也允许把确定性节点与 LLM Agent 混合在一个可观测状态机中。LangChain 保留为模型、消息和工具适配层，不承担第二套编排运行时。

## 没有采用的方案

- 单 Agent + 大工具箱：实现快，但角色边界、上下文隔离和协作评测不足。
- 只有固定 DAG 并给节点添加 Agent 名称：缺少真正的角色策略和局部闭环。
- 同时引入 PydanticAI 或 DeepAgents 作为第二套 runtime：增加状态、恢复和调试语义的重叠。

## 带来的影响

- 有利：覆盖 LangGraph、多 Agent、HITL、持久化、评测和可观测性等高价值面试点。
- 代价：需要明确状态 schema、角色协议、路由守卫和循环预算。
- 风险：角色拆得过细会增加 token 成本和失败面；每个 Agent 都必须能说明独立存在的必要性。

## 落实与确认

- 实施情况：已建立角色 Protocol、领域契约和 Graph State；LangGraph 父图与各角色实现尚未开始。
- 代码或测试：`src/abaqus_sim_agent/agents/contracts.py`、`src/abaqus_sim_agent/application/state.py`。

## 替代关系

- 被替代：[ADR-0008：首轮采用 Pydantic AI Harness 单 Coder 的原生 Abaqus CodingRun](../0008-pydantic-ai-harness-abaqus-coding-run.md)。
- 本 ADR 保留为 LangGraph-first 与分层多 Agent 首轮路线的历史记录；未来重新采用多 Agent 或 LangGraph
  需要以 CodingRun 评测证据形成新决定。

## 相关文档

- [项目架构](../../architecture/overview.md)
- [Agent 与首个完整实现范围](../../architecture/agent-implementation-scope.md)
- [一次仿真 Run 的工作流](../../architecture/flows/simulation-run.md)
