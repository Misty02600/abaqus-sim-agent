# ADR-0008：首轮采用 Pydantic AI Harness 单 Coder 的原生 Abaqus CodingRun

## 状态

已采纳

后续：[ADR-0024](0024-host-owned-user-question-waiting.md)局部替代新默认提问交接形式；本文的单Coder、运行权限与宿主验收边界继续有效，旧profile仍遵守原协议。

## 日期

2026-08-22

## 当时遇到了什么

此前架构把 LangGraph 分层多 Agent、`StructuredModelPort`、`DeckDraft/ChangeSet` renderer、持久 Job
Service、数据库和 API 都设为首个完整实现的前置条件。仓库因此积累了较完整的目标设计和骨架，却没有先
证明一个模型能否在真实 Abaqus 2024 上独立完成“澄清 → 编码 → 建模 → 求解 → ODB 提取 → 物理验收”
闭环。继续同时实现父图、六个角色、持久化和 Web 控制面，会让首个可验证结果依赖过多尚无证据的抽象。

首轮需要回答的是更窄的问题：通用 Coding Agent 加 Abaqus 领域 Skill，是否能在受控工具权限和独立
oracle 下完成一条真实 CAE 任务；失败发生在澄清、代码、vendor runtime、求解、提取还是物理验证；重复
运行的成本和成功率怎样。这个问题不要求先解决多进程恢复、通用 INP AST、数据库或 API。

## 最后决定

首轮产品调用链采用一个 Pydantic AI Agent `abaqus_2024_coder`，由 Pydantic AI Harness 组装文件编码能力
和包内 `src/abaqus_sim_agent/skills/abaqus-2024-coding/SKILL.md`。它直接在隔离工作区编写普通 Abaqus Python，不先生成
`DeckDraft/ChangeSet`，也不经过首轮 renderer/publisher。

同时固定以下边界：

- 模型输出是 strict、带 discriminator 的 `CodingRunDecision`；宿主 `CodingRunService` 独占澄清循环、
  wall-clock/model/runtime 预算、模型 history、事件和最终 `CodingRunOutcome`。
- 模型只可见三个 typed Abaqus 工具：CAE `noGUI` 建模、从现有 INP 启动 solver、Abaqus standalone
  Python/`odbAccess` 提取。通用 shell 不得启动 Abaqus。
- 建模脚本、INP 和模型生成的提取脚本都位于 ASCII 工作区；原始执行日志、请求、history、事件、oracle
  输出和 outcome 位于工作区外的 state 目录。
- Agent 生成的 Abaqus Python 对宿主是普通、动态且不预先限制为自建 DSL 的代码。宿主不以文本相似度或
  自建 AST 判断正确，而是限制执行入口、路径、预算和进程树，并记录每次 workspace artifact delta。
- verifier 是宿主确定性组件，不是第二个 Agent。它通过模型不可见的 trusted oracle 重新读取 ODB，并把
  solver invocation、`.sta` 完成证据、artifact freshness、element type/count 和 U/S/RF 数值共同作为
  通过条件。Agent 的 `ready_for_verification` 只是请求验收，不能宣告成功。
- 首个 Gold 固定为二维 `CPS4R` 矩形板单轴拉伸；板厚故意缺失，Agent 必须在任何 Abaqus 调用前澄清。
  评测从空工作区开始，顺序重复三次，至少两次完整通过且无人工代码修补才满足 smoke gate。
- 本地官方文档检索继续作为独立、已评测的能力存在，但不注入首个 Gold 的 Agent capabilities。先得到
  no-RAG 基线，再用任务级消融决定是否接入。
- 首轮入口是 CLI 和本地文件证据。当前不采用或宣称 LangGraph、DBOS、FastAPI、SQLAlchemy/Alembic、
  数据库 checkpoint、持久 Job ledger、崩溃恢复或多 Agent；这些方向只有在 CodingRun 评测暴露真实需求
  后，才重新提出 ADR。

这项决定只规定首轮纵向闭环，不否定未来可能需要多 Agent、持久控制面或受限中间表示。未来采用这些能力
时必须说明它解决了哪类已复现失败，并用同一 Gold/新增 Gold 证明净收益。

## 为什么这样选

Pydantic AI Harness 已提供首轮真正需要的编码能力、Skill 加载和受控命令边界；Pydantic AI 提供 typed
output、依赖注入、工具循环、usage 与重试语义。直接使用它们可以把框架行为放进一条真实调用路径，而不再
维护一层尚未被专业 Agent 使用的 provider-neutral 模型端口和第二套父图。

普通 Abaqus Python 保留 Coding Agent 修复真实脚本的能力；三个窄运行工具把 vendor runtime 副作用从
通用 shell 中分离。宿主 oracle 和 artifact freshness 又避免“模型自己生成、自己解释、自己判通过”的
同源偏差。先完成这条闭环，才能据失败分布决定 DeckDraft、Reviewer、RAG、durable Job Service 或 API
是否值得增加。

## 没有采用的方案

- 先完成 LangGraph 父图和五个专业 Agent：角色收益尚无任务基线，首个结果会被大量未验证合同阻塞。
- 首轮强制 `DeckDraft/ChangeSet` + renderer/preflight/publisher：会把可支持任务限制在预先建好的 DSL，
  也不能回答 Coding Agent 是否能修复真实 Abaqus Python。
- 继续让专业 Agent 走 `StructuredModelPort`：该端口已有离线测试，但没有进入首轮工具循环；Pydantic AI
  已经拥有本轮所需模型、tool loop 和 typed output 语义。
- 让模型通过通用 shell 任意调用 Abaqus：路径、命令、调用计数和 solver 副作用无法形成稳定权限边界。
- 先建设 FastAPI、SQL、DBOS/LangGraph persistence：当前只有单机顺序 smoke，没有消费者、并发或恢复
  数据证明这些设施是首轮阻塞项。
- 首轮接入现有 RAG：会失去 no-RAG 基线，无法区分 Skill、模型和检索的增益。

## 带来的影响

- 有利：形成短而真实的 Agent → Abaqus → oracle 闭环，澄清、工具权限、外部进程、artifact 和物理失败
  都有可观察边界。
- 有利：秋招讲解可以直接落到 Pydantic 合同、Pydantic AI tool loop、Harness 能力、Windows 子进程、
  vendor Python 边界、独立 verifier 和重复评测，而不是只讲未来架构图。
- 代价：首轮不提供 durable resume、异步 Job 管理、API、多用户、数据库事务或通用 CAE 支持。
- 代价：动态 Abaqus Python 的安全性和正确性依赖工作区隔离、工具 allowlist、预算与宿主验收；它不是可以
  接受不可信租户代码的沙箱。
- 风险：单一 Gold 容易过拟合；必须保留隐藏 oracle、空工作区、重复运行和后续分布外案例。
- 风险：旧 Graph/Agent/inference/Mock 骨架仍在仓库中，文档和讲解必须明确它们不是首轮产品调用链。

## 落实与确认

- 实施情况：首轮 CodingRun、三个模型工具、Abaqus 2024 runner、workspace/state 隔离、clarification、
  host verifier、二维拉伸 Gold evaluator 和 no-RAG Skill 装配已经进入代码与单元测试。
- 运行证据：确定性 reference model 已在本机 Abaqus 2024 完整版通过一次 opt-in 集成测试，验证了真实
  CAE/solver/ODB oracle 和数值 gate；它没有调用 DeepSeek。
- 模型证据：OpenCode Go 上的 `deepseek-v4-flash` 已真实通过 typed clarification smoke，返回
  `needs_input/plate_thickness` 且 Abaqus 调用数为 0；这只验证模型合同与工具前置边界。
- 评测证据：OpenCode Go 已运行 `http_retries=0` 与 `http_retries=2` 两组三重复实验，结果均为 0/3。
  无重试组全部因 500/503 在 Abaqus 前失败；SDK retry 组有一项推进到 30 次模型请求、9 次 Abaqus 调用后
  `model_budget`，另两项重试耗尽后仍为 500。不能声称 licensed smoke gate 已通过。
- 主要实现：`src/abaqus_sim_agent/coding/`、`src/abaqus_sim_agent/execution/abaqus2024.py`、
  `src/abaqus_sim_agent/verification/`、`src/abaqus_sim_agent/evaluation/plate_tension.py`、
  `benchmarks/coding/`。

## 替代关系

- 替代：[ADR-0002](0002-langgraph-hierarchical-multi-agent.md)、
  [ADR-0003](0003-durable-control-plane-and-execution-boundary.md)、
  [ADR-0006](0006-modular-monolith-control-plane.md)、
  [ADR-0007](0007-structured-model-port.md) 的首轮实现决定。
- 同时替代旧架构文档和仓库指令中的首轮 `DeckDraft/ChangeSet` + renderer/publisher 约束。

## 相关文档

- [项目架构](../architecture/overview.md)
- [首轮 Agent 与实现范围](../architecture/agent-implementation-scope.md)
- [当前系统框架](../architecture/system-framework.md)
- [技术栈与工程讲解点](../architecture/technology-stack.md)
- [ADR-0004：以证据和物理验证定义任务成功](0004-evidence-first-physics-verification.md)
- [ADR-0015：产品 Coder 不暴露通用 Shell](0015-disable-general-shell-for-product-coder.md)
