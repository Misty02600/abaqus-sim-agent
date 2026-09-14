# ADR-0020：使用 Harness StepPersistence 续跑中断的 CodingRun

## 状态

已采纳

## 日期

2026-09-02

## 后续关系

[ADR-0024](0024-host-owned-user-question-waiting.md)为新默认协议增加宿主waiting_for_user及答复恢复；原runtime reconcile与文件锁边界继续有效。

[ADR-0021](0021-use-amendments-and-task-revisions-for-continuation.md) 部分替代本文“不接受新goal/运行配置”与
“terminal一律不重开”的限制；本文的StepPersistence、累计状态、工作区一致性、文件锁和
`needs_reconcile`决定继续有效。

## 当时遇到了什么

`CodingRunService` 已在每个完成节点后写出 `history.json`，但新进程仍从空 history、空 usage 和当前工作区
快照开始。模型网关在 Agent 已经读写文件或完成工具调用后异常时，用户只能重新启动完整任务；保存下来的
消息、执行日志和 artifact 只能用于审查，不能用于继续。

Codex、Claude Code 和 Pi 的共同产品体验并不要求先实现外部进程 exactly-once：它们首先持久化会话及已
完成步骤，在同一会话上启动新的 turn。当前锁定的 `pydantic-ai-harness==0.24.0` 已提供
`StepPersistence`、`ContinuableSnapshot`、`ToolEffectRecord` 和文件/SQLite store；重新引入 LangGraph
只为获得这层能力会同时引入第二套运行时和状态语义。

## 最后决定

- 保留 ADR-0008 的单 Pydantic AI Coder 与原生 tool loop，不引入 LangGraph。
- 每个 CodingRun 使用 `request.run_id` 作为 Pydantic AI `conversation_id`；每次模型 turn 使用唯一
  `<run_id>-turn-<n>`。Harness `FileStepStore` 位于模型不可写的 state 目录，记录模型请求、工具生命周期、
  settled message snapshot 和未决 tool effect。
- `CodingRunService` 另行原子保存 `resume_state.json`，拥有累计 usage、active wall time、turn、澄清、调用
  预算、lookup 去重状态、静态预检缓存和完整执行引用；`initial_snapshot.json` 保留首次启动前的 freshness
  基线，不能在恢复时用当前工作区重建。
- 三个模型可见 Abaqus 工具完成后立即把完整 `AbaqusExecutionResult` 追加到
  `execution_results.jsonl`，并在副作用开始前为 Harness tool effect 标注输入 hash、Job 名和幂等标识。
- 普通 CLI 新增 `--resume <state_dir>`。`launch.json` 保存不含凭据的模型、Skill、Catalog、Concept 和静态
  源 composition；续跑重新加载并核对原 request/binding，不要求用户复述原参数，也不允许用新 goal、
  workspace、run ID 或 allowlist 覆盖原合同。
- API/模型基础设施异常和 task cancellation 把状态写为 `interrupted`；续跑优先使用 Harness 的最新
  complete snapshot，只有尚未形成 snapshot 时才使用宿主 history 或原始 goal。新 turn 继承累计预算，
  进程停止到用户继续之间的等待时间不计入 active wall time。
- 若未决 tool effect 属于 `run_cae_script`、`run_solver_job` 或 `run_abaqus_python`，状态改为
  `needs_reconcile`，在任何新模型请求和重复 runtime 前失败关闭。当前只报告确定的 tool call、幂等键和
  effect summary，不声称已重新附着或自动判定外部 Abaqus 状态。
- 已完成或业务失败的 terminal CodingRun 不通过该入口重新打开；旧 state 缺少 initial snapshot、resume
  state 或 launch manifest 时明确拒绝，不增加迁移兼容层。显式 typed-probe 的进程内状态当前也不恢复，
  避免伪造 probe 结论或 adoption。

## 为什么这样选

Pydantic AI 原生 `message_history` 和 Harness StepPersistence 已覆盖会话续接、完成工具结果、失败事件与
未决副作用识别。本项目只需补它明确不拥有的宿主预算、workspace freshness、verifier provenance 和外部
runtime 边界。这样可以直接解决已复现的网关中断，同时保持一套 Agent tool loop。

文件 store 与当前单机 CLI、工作区外 JSON/JSONL 证据一致，也便于审查。若以后出现并发 worker、多用户或
服务端租约需求，再以实测需求选择 SQLite/PostgreSQL 或完整 durable runtime；本决定不把本地文件持久化
宣传成数据库事务。

[Pydantic AI 消息历史](https://pydantic.dev/docs/ai/core-concepts/message-history/) 明确支持 JSON round-trip
后通过 `message_history` 继续；[Harness Step Persistence](https://pydantic.dev/docs/ai/harness/step-persistence/)
同时明确它不恢复 capability state、retry counter、workspace 或外部副作用，这些边界由本 ADR 的宿主持久
状态和 `needs_reconcile` gate 补足。

## 没有采用的方案

- 只读取旧 `history.json`：能恢复模型上下文，但无法识别崩溃中的 Solver，也会重置预算、initial snapshot
  和 verifier execution provenance。
- 为恢复把模型、CAE、Solver、提取全部重写为 LangGraph node：会拆开当前已经通过评测的 Pydantic AI
  tool loop，并与 Harness 自带步骤持久化重复。
- 直接接入 Temporal、DBOS、Prefect 或 Restate：当前只有本地单进程续跑需求，没有服务端 worker、队列和
  durable deployment 的消费者证据。
- 未决 Abaqus 调用一律重新执行：外部进程可能已经启动或完成，会产生重复 Job 和错误预算证据。

## 带来的影响

- 有利：模型网关异常后可以在同一工作区和 conversation 上继续，已完成文件工具、消息与累计预算不会
  因新进程丢失。
- 有利：Thread/Turn/Item-like 证据由当前依赖直接形成，不需要为求职讲解引入未使用的 LangGraph 控制面。
- 代价：新增 state schema、launch manifest、执行 journal 和恢复测试；Harness 仍处于 0.x，升级必须跑
  这些合同测试。
- 边界：`needs_reconcile` 目前是安全终态，不包含 Abaqus PID/Job 重新附着、人工确认后解锁或
  exactly-once；这仍需独立的 Job ledger/reconciler 证据后再决定。

## 落实与确认

- Harness 装配与 tool effect：`src/abaqus_sim_agent/coding/agent.py`、
  `src/abaqus_sim_agent/coding/runtime_tools.py`
- 宿主持久状态与恢复选择：`src/abaqus_sim_agent/coding/resume.py`
- service/CLI：`src/abaqus_sim_agent/coding/service.py`、`src/abaqus_sim_agent/coding/cli.py`
- 恢复安全门：每次 resume state 同时保存当前 workspace fingerprint，恢复前要求逐文件一致；同一 state
  目录由 OS 非阻塞文件锁独占，锁 handle 不向 Abaqus 子进程继承；新 run 在锁内确认 state 未占用后才
  写 launch/request。
- 故障注入：fake CAE 完成后让下一模型请求失败，新进程恢复同一条 execution journal 与预算，Abaqus
  process 调用仍为一次；workspace 漂移、并发执行和旧 state 覆盖均在模型调用前拒绝。
- 真实 Provider smoke：确定性首 turn 完成 `write_file` 后注入 `ConnectionError`，再由 OpenCode Go
  `deepseek-v4-flash` 从 turn 2 恢复并通过产品 execution verifier；无人工 workspace 修改、无 Abaqus 调用。
  证据见 `benchmarks/coding/coding_run_resume_provider_smoke_20260902.json`。
- 自然故障与持证后续路径：`plate-element-derived` 的显式评测开关只对由 `ModelAPIError` 引起且已持久为
  `interrupted` 的 run 自动续一次，默认关闭且保持 evaluator `retry_task=None`。三条都复现自然超时并在
  同一 run 进入 turn 2；一条从 settled step checkpoint 恢复累计预算后完成 CAE、Solver、ODB 提取和独立
  oracle，另两条再次超时，批次1/3。成功样本的中断点在首次 Abaqus 调用前，因此证明自然 Provider 恢复后
  能完成一次持证闭环，不证明在途 Solver 恢复或外部 Job reconcile。冻结证据为
  `benchmarks/coding/plate_element_choice_allowed_resume_once_evaluation_20260903.json`。
- 用户请求的同profile时间分离复测中，两个通过样本都在turn 1直接完成，第三条在唯一一次resume后再次
  超时；因此新批次2/3是自主选型Case的独立batch gate结果，不是resume收益。前批1/3继续保留，两批合计
  仅作3/6描述性观察。证据为
  `benchmarks/coding/plate_element_choice_allowed_retry_batch_evaluation_20260903.json`。
- 该批第三条随后被直接显式resume，同一run从turn 2进入turn 3并继续累计wall-clock；Provider再次超时，
  仍未写文件或调用Abaqus。它证明底层显式resume不受评测器“一次自动恢复”策略限制，但不改变原batch
  2/3 gate。证据为`benchmarks/coding/plate_element_choice_post_batch_manual_resume_20260903.json`。
- 用户随后授权把该run的wall-clock上限从2700秒人工修订到5400秒，已消耗2465.483秒保持不变；turn 4
  完成CPS4R建模、Solver、ODB提取和独立oracle。最终累计2584.038秒低于旧上限，不能把成功归因于扩容。
  该次发生时仍是有备份和修订记录的手工评测干预，不是当时产品的通用预算扩容接口，也不重算原batch
  gate；后续产品接口由ADR-0021拥有。证据为
  `benchmarks/coding/plate_element_choice_budget_extended_resume_20260903.json`。
- 测试：`tests/test_coding_service.py`、`tests/test_coding_resume.py`、`tests/test_coding_cli.py`、
  `tests/test_coding_run_lock.py`

## 替代关系

- 补充 [ADR-0008](0008-pydantic-ai-harness-abaqus-coding-run.md) 的单 Coder 调用链，不替代其 Agent、工具
  权限、verifier 或评测决定。
- 只重新处理 [ADR-0003](0003-durable-control-plane-and-execution-boundary.md) 中“会话恢复”这一窄问题；
  ADR-0003 的 LangGraph 控制面、Job ledger 和 Artifact Store 路线仍保持被替代。

## 相关文档

- [当前系统框架](../architecture/system-framework.md)
- [一次 CodingRun](../architecture/flows/simulation-run.md)
- [当前 Solver 生命周期](../architecture/flows/job-lifecycle.md)
