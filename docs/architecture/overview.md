# 项目架构

## 先建立一个印象

Abaqus Sim Agent 是一个独立维护的、面向 Abaqus 长任务的多 Agent 系统。项目服务 AI Agent / LLM 应用、Python AI 后端和 AI for Science 方向的秋招准备，但不以寻找创新点或堆叠技术栈为目标；它把专业角色、可恢复状态、受控外部副作用和分层验证做扎实，并要求能准确解释代码路径、工程取舍和已知局限。

本项目可以有来源地吸收并重构 `references/AbaqusAgent` 已实现的业务设计，但不继承其 Git 历史，也不照搬其 Graph State、同步执行和固定文件工作方式。上游设计与当前契约的映射见[源码设计分析](../scratch/abaqusagent-source-design-analysis.md)。

当前代码已经建立领域契约、Graph State、Agent 协作协议和 Mock 执行器。LangGraph 父图/子图、实际模型适配、持久化、知识检索、Abaqus 执行器与 API 尚未实现；它们不能被当作当前能力宣传。

完整的系统分层、运行拓扑、组件职责、数据所有权、API、部署和测试边界见
[完整系统框架](system-framework.md)；六个 Agent 与第一个受支持有限元 envelope 见
[Agent 与首个完整实现范围](agent-implementation-scope.md)；父图和外部副作用的细节分别见
[一次仿真 Run 的工作流](flows/simulation-run.md)与[Abaqus Job 生命周期](flows/job-lifecycle.md)。运行技术的
选型、落地状态、升级触发条件和对应的工程讲解点见[技术栈与工程讲解点](technology-stack.md)。

## 核心能力与公开接口

| 核心能力或公开入口 | 对外含义与适用场景 | 关键状态或副作用 | 主要实现位置 |
|---|---|---|---|
| 仿真领域契约 | 约束 Agent 之间传递的需求、建模计划、产物、诊断和验证结果 | Pydantic 严格校验，禁止未知字段 | `src/abaqus_sim_agent/domain/models.py` |
| 多 Agent 协作协议 | 定义 Supervisor 与专业 Agent 的结构化职责边界 | 当前是 Protocol，尚无 LLM 实现 | `src/abaqus_sim_agent/agents/contracts.py` |
| 面向 checkpoint 的工作流状态 | 为后续 LangGraph checkpoint 提供业务状态载体 | 目标是不保存运行时连接；当前 `Any` 字段尚未从类型和测试上强制可序列化 | `src/abaqus_sim_agent/application/state.py` |
| 仿真执行器协议 | 统一 Mock 与真实 Abaqus 的提交、查询、取消、日志和结果接口 | Job 提交是外部副作用，必须携带幂等键 | `src/abaqus_sim_agent/execution/base.py` |
| Mock 执行器 | 在没有 Abaqus 的 CI 中验证状态和故障路径 | 内存实现；进程重启后不保留状态 | `src/abaqus_sim_agent/execution/mock.py` |

## 逻辑组件与实现映射

| 逻辑组件 | 职责与边界 | 主要协作 | 拥有的数据或状态 | 主要实现位置 |
|---|---|---|---|---|
| 领域契约 | 表达业务事实，不调用模型和 Abaqus | 被 Agent、工作流、执行器共同依赖 | 不拥有运行时状态 | `domain/models.py` |
| Agent 层 | 做需求理解、建模决策、输入生成、诊断和验证 | 通过结构化契约协作，工具由运行时注入 | 各角色局部上下文；不拥有全局状态 | `agents/contracts.py` |
| 应用用例与 Coordinator | 后续负责 create/clarify/approve/cancel/query，用持久状态启动和恢复父图 | API/CLI、Graph、repository | 命令语义与 run 调度投影；不拥有图游标 | 尚未实现 |
| 工作流层 | 以确定性父图负责 phase、路由、预算、暂停恢复和终止；专业 Agent 用 per-invocation 子图 | 通过 Runtime Context 调用 Agent、Job Service 与 Validator | 可序列化 Graph State | `application/state.py` 只有骨架 |
| Job Service | 唯一负责 reserve、submit、poll、cancel、timeout 和 reconcile | 接受经过审批的 immutable artifact；调用 Executor | 持久 Job/attempt ledger | 尚未实现 |
| Executor adapter | 把确定性命令映射到 Mock 或 Abaqus CLI/ODB | 不决定是否提交、重试或验收 | 外部 Job handle 和原始观察 | `execution/` |
| 应用数据层 | 后续持久化 Job、attempt、approval、artifact metadata 与 event/audit；不接管 LangGraph checkpoint schema | Job Service、API 与 reconciler | 关系模型、唯一约束、事务和索引 | 尚未实现；目标为 SQLAlchemy 2 + Alembic |
| Artifact Store | 后续保存 `.inp`、`.odb`、日志、图片和摘要 | Graph State 只保存路径、哈希和元数据 | 大型仿真产物 | 尚未实现 |
| API 与演示层 | 后续以 REST/OpenAPI + SSE 投影资源和事件，React 控制台不另建工作流状态 | 工作流、应用数据层与 Artifact Store | 不拥有任务真相 | 尚未实现 |

依赖方向为 API/CLI → Application Facade → workflow/application contracts → domain/ports，具体的数据库、
Artifact Store、模型 provider 和 Abaqus adapter 从外侧实现端口，并只在 Composition Root 组装。领域契约
不反向依赖 LangGraph、LangChain、FastAPI、SQLAlchemy 或 Abaqus。

## 最重要的质量目标和约束

- 多 Agent 角色必须具有不同目标、结构化输入输出和工具权限边界。
- checkpoint 重放不能导致同一个 Abaqus Job 被重复提交。
- 关键参数缺失和真实 Job 提交必须能够暂停，等待人工补充或批准后继续。
- Mock 与真实执行器遵循同一协议，核心可靠性测试不依赖 Abaqus 许可证。
- 知识检索需要来源、集合边界和离线指标，不能只展示一次相似度搜索。
- solver 正常结束只代表执行层完成；需求忠实度和物理正确性必须独立验证。
- 来自上游的代码、提示词或资产必须保留可追溯来源；第三方案例数据在确认再分发权之前不得迁入本仓库。

## 数据和状态放在哪里

- Graph State：目标只保存可序列化的任务阶段、结构化结果、Job handle、循环计数和摘要；当前 schema 尚未证明任意写入都可序列化。
- Runtime Context：模型客户端、专业 Agent、执行器、检索器、Artifact Store 和观测组件等单次运行依赖。
- 控制平面基础设施：checkpointer 在图编译时配置，不作为 Graph State 或普通业务上下文的一部分。
- Artifact Store：输入文件、ODB、完整日志、截图及其他大文件；State 只保存引用与校验信息。
- 应用数据库：持久化 run 投影、human action、Job/attempt、artifact metadata 与 append-only event；不替代 Graph checkpoint。
- Job ledger：外部 Job 当前状态的业务真相；Graph State 和 UI 只保存或显示投影，恢复时必须重新 reconcile。

## 已采纳但尚未实现的目标边界

| 目标组件 | 已确定的边界 | 首个实现 | 升级触发条件 |
|---|---|---|---|
| LangGraph 控制平面 | 负责阶段、checkpoint、interrupt/resume、预算和多 Agent 编排 | SQLite durable checkpointer | 多进程、并发 worker 或多用户后迁移 PostgreSQL |
| Job service | 无 LLM，独占 submit、poll、cancel、timeout、reconcile；幂等键唯一 | SQLAlchemy 2 + Alembic 管理的 SQLite Job ledger，加 Mock/CLI adapter | 服务化后迁移 PostgreSQL；只有真实队列压力才评估 broker |
| Artifact Store | 每个 run 隔离；内容 hash、manifest 和来源可追踪 | 本地 content-addressed 文件存储 | 多 worker 或远程部署后评估 S3-compatible 存储 |
| Verifier / Evaluator | 分开报告 executable、spec fidelity 和 physics validity | 人工 Gold cases + 确定性检查 | 固定 benchmark 建立后再接入 RAG、LLM judge 或训练 |
| API 与观测 | 不拥有工作流真相，只通过 Application Facade 提交命令并投影状态、事件和 artifact；SSE 从持久事件续读 | SQLite 阶段 FastAPI 与 Coordinator 同进程；首个 durable Mock vertical slice 后加入 REST/OpenAPI/SSE | PostgreSQL 后可拆为两个进程；接口稳定后增加窄范围 React/TypeScript UI，跨进程后评估 OpenTelemetry |

首版不引入第二套 Agent runtime、Redis/Celery、Neo4j、MinIO、LoRA、多模态或多 solver。它们只有在固定评测、并发容量或真实外部消费者给出证据后再评估。

## 关键决定

- [ADR-0001：与上游 AbaqusAgent 保持独立实现](../adr/0001-independent-from-upstream.md)（已由 ADR-0005 替代）
- [ADR-0002：使用 LangGraph 构建分层多 Agent 系统](../adr/0002-langgraph-hierarchical-multi-agent.md)
- [ADR-0003：分离可恢复控制平面与 Abaqus 执行边界](../adr/0003-durable-control-plane-and-execution-boundary.md)
- [ADR-0004：以证据和物理验证定义任务成功](../adr/0004-evidence-first-physics-verification.md)
- [ADR-0005：有来源地吸收与重构 AbaqusAgent](../adr/0005-traceable-upstream-reuse.md)
- [ADR-0006：使用模块化单体和端口适配器组织控制平面](../adr/0006-modular-monolith-control-plane.md)
- [架构文档入口](README.md)
- [完整系统框架](system-framework.md)
- [Agent 与首个完整实现范围](agent-implementation-scope.md)
- [一次仿真 Run 的工作流](flows/simulation-run.md)
- [Abaqus Job 生命周期](flows/job-lifecycle.md)
- [生态、框架与求职项目调研](../scratch/ecosystem-research-20260819.md)
- [AbaqusAgent 源码设计分析](../scratch/abaqusagent-source-design-analysis.md)
- [本地参考仓库目录](../scratch/reference-repositories.md)
- [技术栈与工程讲解点](technology-stack.md)
