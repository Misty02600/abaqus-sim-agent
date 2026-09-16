# 系统框架：部署与状态所有权

## 两个解释器和一个外部运行边界

宿主 Python 3.12 运行 CLI、Pydantic AI/Harness、CodingRunService 和评测；Abaqus 2024 的 Python 3.10
运行 CAE/odbAccess 脚本。宿主依赖不能直接当 vendor Python 依赖，宿主也不直接导入 Abaqus API。
两边通过工作区文件、固定 launcher 参数和 execution result 协作。

产品入口是本地 Windows CLI。普通 CLI 装配通用执行 verifier，固定 evaluator 装配对应物理 verifier。
宿主调用一个 Coder，Coder 使用文件工具和三个 typed runtime tools；组件图见 [总览](overview.md)。

## 谁拥有哪些状态

| 状态或资源 | 初始化 / 持续维护者 | 其他组件如何使用 |
|---|---|---|
| workspace 与 initial snapshot | workspace resolver、宿主首次启动；新 task revision 建立新的验收基线 | Coder 读写文件，runner/verifier 核对输入和变化 |
| immutable request / base launch | CLI 或 evaluator composition | 恢复核对原合同，amendment 形成 effective overlay，不改写原实验请求 |
| turn、revision、usage、active wall | CodingRunService | 模型只接收允许的上下文，不能重置预算或直接写 outcome |
| 模型 history、settled snapshot、tool effect | Pydantic AI/Harness 与宿主持久化适配 | resume 选择可继续边界，未决 vendor effect 触发 `needs_reconcile` |
| 问题、history 关联、用户答复 | 宿主问答组件 | Console 在执行锁外取答；锁内按 ID 持久化和消费 |
| 子进程与原始执行观察 | Abaqus runner | 工具保存 execution journal，模型收到诊断，verifier 独立判断 scope |
| 查询、计数、实际已交付证据 | run-scoped dependencies 与查询 journal | resume 合并记录；候选、读卡和参数分别恢复 |
| outcome 与 verification | Service 聚合宿主 verifier 结果 | 当前 outcome 是投影，历史 revision 结果保留；Agent 不能宣告成功 |
| 固定 Case 的物理真值 | evaluator / trusted oracle | 不注册为模型工具；只反馈当前 scope 允许的验证失败证据 |

## 数据放在哪里

**Workspace** 保存模型可见的脚本和 CAE/INP/ODB/JSON 等交付物。交互 CLI 默认使用启动时的绝对工作目录，
也可显式指定。from-scratch Gold 要求空目录；verified-seed change 先验证 source 的合同、完成状态和 hash，
缓存脚本 bytes 后再建立新工作区，避免校验后源文件改变。

**State directory** 必须位于 workspace 外；普通交互默认在 `%LOCALAPPDATA%/abaqus-sim-agent/runs/<run_id>`。
其记录按职责分为：

- 基础合同和修订：`request.json`、`launch.json`、`amendments.json`、`initial_snapshot.json`。
- 会话恢复：`resume_state.json`、`history.json`、`step-persistence/`、`coding_run.lock`。
- 人工交接：`user_questions.json` 与按问题 ID 关联的 history 快照。
- 执行与知识证据：`execution_results.jsonl`、raw stdout/stderr、`api_lookups.jsonl`、
  `api_discoveries.jsonl`、`concept_lookups.jsonl`，以及显式策略的额外 journal。
- 验收：当前 `outcome.json`、verification 和历史 `outcomes/task-*/`；评测另保存 oracle 和 diff/provenance。

Catalog 与文档索引是显式选择的外部知识数据，和 run state 分开；可恢复产品入口核对 request/launch 中的
内容绑定。显式工厂的只读参考资料尚无同等 launch 快照保证，见 [知识接入](knowledge-access.md)。

## 并发、持久化与崩溃边界

- 一个 state 目录只允许一个持有 OS 非阻塞文件锁的活动工作段。锁 handle 不传给子进程；锁文件存在不表示持锁。
- 待答释放执行锁，答复写入和继续执行重新取锁。锁按 state 隔离，不保证不同 run 共用 workspace 的事务隔离。
- atomic JSON、append journal 与 Harness snapshot 分别保存宿主状态和步骤证据；它们不是一个数据库事务。
- 恢复要求当前 workspace 指纹匹配持久边界。损坏 tool-effect ledger 失败关闭，不通过 event log 猜造副作用状态。
- 未决 Abaqus effect 先进入 `needs_reconcile`。当前不会查询原 PID、重新附着、自动重提或通过人工对账解锁。
- 宿主还活着时，timeout/cancellation 会终止准确 PID 的进程树并保存能取得的日志；宿主/机器崩溃后的
  外部进程管理没有同等保证，见 [Job 生命周期](flows/job-lifecycle.md)。

## 依赖和装配

`coding` 是产品宿主，`execution` 是 vendor 副作用边界，`verification` 是可信验收，`api_catalog` 和
`retrieval` 提供知识，`evaluation` 定义外层证据系统。这是活动组件图，不额外套统一技术分层。

`coding.verification_port` 由消费方拥有，具体 verifier 从外侧实现。`coding.service` 不直接依赖
物理 verifier；CLI/evaluator 分别装配对应 scope。部分可选检索服务仍复用 evaluation 内实现，
清理或拆分必须先核对调用关系，不能把期望的分层当现状。

实现入口：[workspace](../../src/abaqus_sim_agent/coding/workspace.py)、
[service](../../src/abaqus_sim_agent/coding/service.py)、[verifier port](../../src/abaqus_sim_agent/coding/verification_port.py)、
[run lock](../../src/abaqus_sim_agent/coding/run_lock.py)、[runner](../../src/abaqus_sim_agent/execution/abaqus2024.py)。

## 已决定但尚未成为产品的拓展

[ADR-0025](../adr/0025-local-web-workbench-over-coding-run.md)已选择 FastAPI + assistant-ui 的本地工作台。
三路 UI 接线比较不等于产品 API/UI 完成，具体实施仍在 [计划索引](../plans/todo/README.md)。
数据库 Job ledger、durable scheduler、多 worker 和远程执行没有因该 Web 决定自动获选。

架构依据：[ADR-0017](../adr/0017-package-by-component-coding-run.md)、
[ADR-0019](../adr/0019-separate-product-execution-evidence-from-evaluation-oracles.md)、
[ADR-0020](../adr/0020-resume-coding-runs-with-harness-step-persistence.md)、
[ADR-0021](../adr/0021-use-amendments-and-task-revisions-for-continuation.md)。
