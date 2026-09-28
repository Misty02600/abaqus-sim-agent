# 技术栈与工程讲解点

## 当前采用的技术

版本以 [pyproject.toml](../../pyproject.toml)和 [uv.lock](../../uv.lock)为准；运行参数以当前
effective launch 为准。这里解释实际职责，不维护另一份依赖版本或实验成绩清单。

| 技术 | 实际职责 | 当前边界 |
|---|---|---|
| Python 3.12 / uv / uv_build | 宿主、依赖、项目内 `.venv`、包构建 | 不等于 Abaqus vendor Python 3.10 |
| Pydantic | strict 请求、配置、决策、证据和 outcome | 类型校验不能证明物理意图正确 |
| Pydantic AI 2.51.0 | 模型/provider、原生 tool loop、typed output、deferred tools、usage | 当前只有一个产品 Coder；pydantic-evals 同步锁定 2.51.0，历史批次仍使用各自冻结环境 |
| Harness 0.36.0 | workspace coding、Shell、Skills、StepPersistence；Planning 仅保留历史适配 | 产品用 AbaqusTextFileSystem 保留 UTF-8/CP936 编码和 raw-byte hash，组合 run-scoped Shell 与可回读的 ToolOutputLimits；显式设置 sub_agents=False。0.36 的 Windows JSONL 追加与进程树清理仍需版本补丁 |
| CodingRunService / asyncio | 问答、累计预算、修订、恢复、verifier 反馈 | 本地文件后端宿主，不是 durable scheduler |
| FastAPI / Uvicorn / REST + SSE | 本地 Web 命令、只读快照和变更通知 | 只监听回环地址；SSE 不传递运行真值或逐 token 恢复日志 |
| React / TypeScript / assistant-ui | Thread、消息、工具、产物与验证展示 | ExternalStore 消费宿主投影，不运行 Agent 或拥有产品状态 |
| Online 3D Viewer | 展示当前 CAE 工具卡声明的 fresh OBJ | 不读取 CAE/ODB，不等于结果云图或任意 Abaqus 对象支持 |
| JSON/JSONL、文件指纹、OS lock | 会话与执行证据、freshness、同 state 独占 | 无跨文件数据库事务、跨 run workspace 锁或外部 Job 对账 |
| Abaqus 2024 launcher | CAE noGUI、Standard、standalone Python/odbAccess | 外部进程与许可证边界；进程 0 不等于仿真正确 |
| 产品 verifier / 固定 oracle | 分别验证执行证据与指定任务物理合同 | 普通 CLI 不运行隐藏物理 oracle |
| pydantic-evals / 自有 evaluator | 重复 Case、gate、usage、对照与冻结报告 | 参考脚本通过、模型通过、任务收益分别统计 |
| SQLite / NumPy Flat / ONNX MiniLM | 官方文档、metadata exact、Dense 与本地持久索引 | 3.9万条低QPS语料使用精确 cosine，不实现ANN；索引不是业务数据库 |
| 自研 SQLite BM25 / RRF | 固定 tokenizer 的词法对照与独立融合评测 | 保留ADR-0022算法合同；不自动替换产品 Concept 路径 |
| 官方 XHTML / abqpy AST / runtime notes | 可追溯 Catalog、精确查证与 receiver 方法目录 | 静态/跨版本来源不能替代实际运行 |
| BasedPyright / 可选 ACI | 宿主类型检查，或显式 profile 下的 vendor 脚本预检 | API blocker 需要冻结证据，未知类型诊断不自动升级 |

## 模型配置与兼容

模型依赖使用 `pydantic-ai-slim` 的模型 extras，预装除 xAI 外的非实时模型 SDK。提供商目录不按 SDK 或凭据过滤；选择后缺包直接提示包名。
原生路径使用 `infer_model` / `infer_provider_class`；OpenCode Go/Zen 继续限定 DeepSeek 并复用原有 reasoning/header 适配。
模型候选复用 `known_model_names()`，允许手动输入；候选不是账号实时可用名单，也不决定默认推荐顺序。
新普通会话保存有效窗口、可选 API 地址和非凭据的接口/区域环境绑定，恢复不会重新读取偏好或静默重绑环境。
Web API Key 与 OAuth 令牌只在宿主内存；重启后重新提供凭据或复用本机已有登录。OpenAI Codex 用框架 PKCE/回调，
GitHub Copilot 用框架设备流且必须提供自己的 OAuth Client ID。登录完成不等于推理 API 权限已经验收。

模型工厂使用所选原生 provider 或 OpenCode 适配，不隐藏切换 provider。当前目标 Go/V4-Flash 新 run 使用
typed `ModelExecutionConfig` 保存 thinking enabled/high、Planning 开关、HTTP 重试与超时等有效设置；普通产品
默认关闭 Planning，冻结 profile 保留原设置。当前 launch 按保存的有效配置恢复；不兼容的开发期 state
显式拒绝，不迁移。历史批次复现使用其冻结源码和依赖，不能套新默认后声称复现旧请求。

Go adapter 已恢复框架原生 `reasoning_content` 历史回传，不再沿用禁用回传的旧说明；不补造历史缺失推理。
协议与离线发送一致不证明服务端实际执行 high 强度，也不能推断所有网关错误已解决。
实现见 [model.py](../../src/abaqus_sim_agent/coding/model.py)，背景见
[配置实施记录](../plans/done/0005-align-target-agent-runtime.md)。

## Agent 扩展顺序

多 Agent 不要求引入 LangGraph。若后续评测发现独立模型上下文确有收益，按最小机制逐级增加：

1. 宿主以普通 async Python 依次调用第二个 typed Agent，明确输入、输出、依赖、权限和失败语义；全局预算、
   task revision、runtime 副作用和最终 outcome 仍由 `CodingRunService` 拥有。
2. 只有父模型需要自主选择一个自包含专家时，才使用 Harness `SubAgents`；子 Agent 使用独立 history 和窄工具，
   显式限制调用次数、时间与 usage，不默认继承 Coder 的写入或 Abaqus 工具。
3. 只有控制流本身形成需要 fork/join、循环和可视化的真实状态机时，才比较已随当前依赖安装的
   `pydantic-graph`。普通顺序、条件调用和一次 reviewer 回评继续用 Python 控制流。
4. 只有服务端长任务、崩溃恢复或多 worker 形成真实需求时，才评估 Pydantic AI 的 Temporal、DBOS、Prefect、
   Restate 等 durable integration；workflow durability 不能替代 Abaqus 外部 Job ledger 和 reconcile。

Pydantic 官方将 delegation、程序化 handoff 和 graph control 作为递增复杂度的不同模式；Harness 0.x 的
SubAgents API 仍可能随小版本变化，因此任何采用都需要锁版本和合同测试。参考：[多 Agent 模式](https://pydantic.dev/docs/ai/guides/multi-agent-applications/)、
[SubAgents](https://pydantic.dev/docs/ai/harness/subagents/)、[Pydantic Graph](https://pydantic.dev/docs/ai/graph/graph/)、
[durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)。

## 开发和验证工具

- pytest / pytest-asyncio：strict contract、工具、问答、恢复、runner、verifier 与检索的离线回归。
- opt-in `abaqus` tests：需要本机 runtime 和许可证，验证 reference/runner/oracle；并不自动调用真实模型。
- Ruff / BasedPyright：宿主源码质量和类型检查，不能替代持证仿真。
- TypeScript / Vite：前端类型检查、生产构建和 Python wheel 静态资源输入；Node 不进入运行时。
- GitHub Actions、Prek、Commitizen、git-cliff、Copier、Renovate：工程交付和仓库维护设施。

运行命令见 [项目 README](../../README.md)，支持结论见 [验证证据](verification-evidence.md)。

## 适合讲清楚的工程问题

| 问题 | 项目中的具体例子 |
|---|---|
| 怎样区分 Agent 决策与宿主权限 | ready 只申请验收，oracle 不向模型注册；Shell fail-closed 移除 |
| 如何处理人机交接 | ask_user 的持久待答、按 ID 答复幂等、锁外等待与混发拒绝 |
| 如何区分聊天与任务合同 | Web Thread message 使用 continuation；显式 amendment/task revision 保留任务合同语义 |
| 如何管理外部副作用 | 三个 vendor 工具、输入 hash、预算、raw logs、未决 effect 停止恢复 |
| 如何定义完成 | execution_evidence 与 task_oracle 分离，manifest freshness 不等于语义完整 |
| 检索如何进入真实调用链 | receiver 目录 → exact lookup，查询/已交付证据独立持久化；命中不等于采用 |
| 怎样避免评测自欺 | 固定 source/profile、逐 Case gate、保留失败、禁止回填旧 trial、区分 replay 与新实验 |
| SQL 在哪里实际使用 | 本地文档/metadata exact与BM25倒排；尚无应用 Job ledger，不把依赖写成产品事务能力 |

## 已采纳或条件触发的技术

FastAPI + assistant-ui 已被 [ADR-0025](../adr/0025-local-web-workbench-over-coding-run.md)采纳用于本地
工作台；本地 API/UI、Thread continuation、模型预览和后台 Solver submit/wait 已完成工程实现。它仍是本机单用户
文件后端，PLAN-0010 的真实 Web 主线尚未执行，不能写成远程部署或已证明稳定建模收益。

LangGraph、DBOS、业务 SQLAlchemy/Alembic、PostgreSQL、Redis/Celery、远程 worker 和 Job Service
目前未进入产品。未来即使增加专业 Agent，也优先沿上述 Pydantic AI 原生路径扩展；LangGraph 不再作为
“多 Agent 必须配套”的默认候选。当前源码与依赖不再包含 LangChain 或 Chroma；历史 Chroma
索引只由对应旧提交、冻结源码和报告解释，不在当前运行时提供兼容 reader。
只有真实调用、并发、状态或失败需求出现后再作取舍，不为了秋招覆盖技术名预先叠加。
