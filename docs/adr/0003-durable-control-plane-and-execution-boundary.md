# ADR-0003：分离可恢复控制平面与 Abaqus 执行边界

## 状态

已采纳

## 日期

2026-08-19

## 当时遇到了什么

Abaqus Job 是长时间运行的外部副作用。LangGraph checkpoint 可以恢复业务状态，但不能为外部求解器提供 exactly-once 事务；节点在故障、重试或 interrupt 恢复时可能重新执行。如果把 Job 提交直接放进普通 Agent 节点，进程在“提交成功、checkpoint 尚未落盘”之间崩溃后，恢复可能产生重复 Job。

当前仓库已经定义面向可序列化设计的 Graph State 骨架、执行器协议和幂等键，但尚未用类型与
round-trip 测试证明 State 的任意写入都可持久化，也尚未建立 durable checkpointer、持久 Job ledger、
Artifact Store 或真实 Abaqus adapter，需要先固定状态与副作用的所有权。

## 最后决定

LangGraph 作为唯一控制平面，负责阶段、checkpoint、interrupt/resume、循环预算和多 Agent 编排；Abaqus 的 submit、poll、cancel、timeout 和 reconcile 由无 LLM 的确定性 Job service 独占。

采用以下边界：

- Graph State 只保存可序列化业务状态，以及 artifact、Job 和评测结果的引用。
- 模型客户端、Agent 实现、执行器、数据库、Artifact Store 和检索器通过 Runtime Context 注入。
- 审批 interrupt 与提交分成两个节点，任何真实提交都发生在审批之后。
- 持久 Job ledger 对稳定的 `idempotency_key` 建唯一约束。键由逻辑 run、规范 revision、输入 artifact digest、executor profile 和 solver 配置共同产生；外部 job name 只是可对账标识，不充当唯一幂等机制。
- 恢复时先依据 ledger、外部 job ID、隔离运行目录和 Abaqus 状态文件进行 reconcile，再决定继续轮询、标记未知或重试；不盲目重新提交。
- 每次输入 revision 使用独立 workspace。大型输入、ODB、日志、图片和报告进入 Artifact Store，State 只保存 URI、hash 和元数据。
- 本地开发首先使用 SQLite durable checkpointer、SQLite Job ledger 和本地 content-addressed Artifact Store。进入多进程 API、并发 worker 或多用户部署时迁移到 PostgreSQL；只有出现独立队列或对象存储需求时再评估 Redis/Celery 与 S3-compatible 存储。
- 首个真实 executor 使用 headless Abaqus CLI 与 ODB 提取 adapter。CAE GUI 或 MCP 连接只作为以后可选的交互 adapter，不作为核心执行路径。

## 为什么这样选

这个设计把 LLM 擅长的解释、规划和诊断，与外部系统要求的幂等、权限、恢复和对账分开。它允许 CI 使用 Mock Executor 验证完整生命周期，也允许有许可证的环境替换为真实 Abaqus adapter，而不改变父图和 Agent 合同。

SQLite 和本地 Artifact Store 足以证明单机纵向闭环，又保留明确的 PostgreSQL 和 S3 迁移触发条件，避免在尚无并发和容量证据时引入多套基础设施。

[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 明确区分 checkpoint 和应用持久数据；[interrupt 文档](https://docs.langchain.com/oss/python/langgraph/interrupts) 说明恢复会从当前节点开头重新运行，副作用必须幂等。这也是审批节点与提交节点分离的直接依据。

## 没有采用的方案

- 在 LLM Agent 工具中直接同步运行 Abaqus：难以取消、恢复、对账和限制权限。
- 把 checkpoint 当作 exactly-once 保证：无法覆盖外部进程已启动但状态尚未持久化的窗口。
- 首版直接采用 PostgreSQL、Redis、Celery、MinIO 和 Neo4j：增加部署与故障面，但当前没有并发、容量或图关系消融证据。
- 使用 Temporal、DBOS 或第二套 Agent runtime 包裹 LangGraph：引入两套恢复语义，当前需求可由 LangGraph、Job ledger 和 reconciler 完成。
- 只通过 Abaqus GUI/MCP 执行：会把核心可靠性依赖在交互进程、全局队列和过宽脚本权限上。

## 带来的影响

- 有利：控制流恢复与外部副作用的责任清晰；Mock 和真实执行共享合同；重复提交、取消、超时和崩溃恢复可以独立测试。
- 代价：需要实现 Job FSM、唯一约束、运行目录隔离、reconciler 和 artifact manifest。
- 风险：数据库状态与 Abaqus 外部状态仍可能短暂不一致，必须把 `UNKNOWN` 和人工接管作为显式状态，不能伪造 exactly-once。

## 落实与确认

- 实施情况：已建立执行器协议、幂等键字段、Mock Executor 和面向可序列化设计的 Graph State 骨架；
  JSON round-trip 约束、durable graph、Job ledger、reconciler、Artifact Store 与真实 Abaqus adapter 尚未实现。
- 代码或测试：`src/abaqus_sim_agent/execution/`、`src/abaqus_sim_agent/application/state.py`、`tests/`。

## 相关文档

- [项目架构](../architecture/overview.md)
- [完整系统框架](../architecture/system-framework.md)
- [一次仿真 Run 的工作流](../architecture/flows/simulation-run.md)
- [Abaqus Job 生命周期](../architecture/flows/job-lifecycle.md)
- [生态、框架与求职项目调研](../scratch/ecosystem-research-20260819.md)
