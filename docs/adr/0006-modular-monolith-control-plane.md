# ADR-0006：使用模块化单体和端口适配器组织控制平面

## 状态

已替代

## 日期

2026-08-19

## 当时遇到了什么

项目已经确定 LangGraph 多 Agent、持久 Job ledger、Artifact Store、FastAPI/SSE 和后续 React 控制台。
如果只列技术栈而不固定系统框架，API、Graph、Agent、数据库和 Executor 很容易互相直连；如果首版直接
拆微服务、独立 worker、Redis/Celery 和远程 runner，又会在没有并发与部署证据时引入分布式一致性、
运维和调试成本。

现有 ADR-0003 还规定：本地开发先使用 SQLite，进入多进程 API、并发 worker 或多用户部署时再迁移
PostgreSQL。因此 SQLite 首版若让 API 与独立 worker 并发写，会与既有持久化边界冲突。

## 最后决定

使用模块化单体和端口适配器组织控制平面：

- 一个仓库、一个 Python distribution 和一套领域契约；不把每个 Agent 拆成服务。
- 交互、应用用例、工作流、Agent、验证、Job Service、持久化和 adapter 保持明确模块边界。
- FastAPI/CLI 只调用 Application Facade；不直接调用 Graph、SQLAlchemy model 或 Executor。
- LangGraph 父图负责全局业务状态机，具体运行依赖通过 Runtime Context 注入。
- Job Service 是 Abaqus submit、poll、cancel、timeout 和 reconcile 的唯一入口；Executor 只是 adapter。
- 具体依赖只在 Composition Root 组装，领域与应用层依赖 Protocol，不依赖具体基础设施。
- SQLite 首版让 FastAPI 与 Coordinator 在同一 Python 进程运行；Coordinator 从持久数据恢复非终态 run，
  不把内存 task 或 FastAPI `BackgroundTasks` 当作 durable queue。
- 迁移 PostgreSQL 或等价并发持久化后，同一代码包可以用不同启动命令运行 API process 与 Coordinator
  process；这仍是模块化单体的两个入口，不是两套微服务。
- 真实 Abaqus 先作为持证宿主机上的受控外部进程。跨主机成为真实需求后，才设计 remote-runner 协议。

## 为什么这样选

模块化单体足以把 SQL 事务、REST/SSE、持久状态机、Agent 权限、Abaqus 外部副作用和评测放进一条真实
调用链，又保留清晰的拆分缝。首版可以在一个进程和两份 SQLite 文件中验证恢复行为，避免过早处理服务
发现、消息交付和跨服务事务；将来 API 与 Coordinator 沿现有端口拆成两个进程时，不需要改写领域合同。

## 没有采用的方案

- 每个 Agent 一个微服务：角色通信主要是同一 run 内的结构化业务状态，拆服务只会增加网络与状态同步。
- 首版 API + 独立 worker + SQLite：并发写和进程协调边界不清，也与 ADR-0003 的 PostgreSQL 触发条件冲突。
- 首版 Redis/Celery/MQ：当前没有队列容量、背压或跨主机调度证据；持久 run 扫描与 machine interrupt 足够。
- 在 HTTP 请求或 FastAPI `BackgroundTasks` 中完成整个 run：连接生命周期和内存任务不能保证长任务恢复。
- 把 API、Graph、ORM 和 Executor 直接放在同一模块：虽然也是单进程，但不是可维护的模块化单体。

## 带来的影响

- 有利：依赖方向、状态所有权和测试替身清晰，Mock 与真实 Abaqus 可以共享同一纵向链路。
- 有利：首版部署简单，仍能覆盖应用事务、长任务恢复和外部进程对账。
- 代价：需要 Application Facade、Coordinator、repository/unit of work、Composition Root 和明确端口。
- 代价：SQLite 阶段只有一个控制面进程；需要多 worker 时必须先迁移 PostgreSQL 或重新评估并发存储。
- 风险：模块化边界若只写在文档里而 API/Agent 越层调用，最终仍会退化成耦合单体；测试和代码审查必须
  检查依赖规则。

## 落实与确认

- 实施情况：系统框架、运行流程、数据所有权和演进触发条件已经确定；Application Facade、Coordinator、
  API、数据库和 Composition Root 尚未实现。
- 当前代码：`domain/`、`agents/`、`application/state.py` 和 `execution/` 是可继续演进的骨架，不为目录外观
  预先创建空模块。

## 替代关系

- 被替代：[ADR-0008：首轮采用 Pydantic AI Harness 单 Coder 的原生 Abaqus CodingRun](0008-pydantic-ai-harness-abaqus-coding-run.md)。
- 模块化单体、FastAPI 和 SQL 控制面的设计保留为历史候选，不再构成首轮或后续默认承诺；引入时需由
  真实消费者、并发、查询或恢复需求重新决策。

## 相关文档

- [完整系统框架](../architecture/system-framework.md)
- [一次仿真 Run 的工作流](../architecture/flows/simulation-run.md)
- [Abaqus Job 生命周期](../architecture/flows/job-lifecycle.md)
- [ADR-0002：使用 LangGraph 构建分层多 Agent 系统](0002-langgraph-hierarchical-multi-agent.md)
- [ADR-0003：分离可恢复控制平面与 Abaqus 执行边界](0003-durable-control-plane-and-execution-boundary.md)
