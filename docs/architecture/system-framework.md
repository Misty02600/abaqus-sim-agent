# 系统框架：部署与状态所有权

## 两个解释器和一个外部运行边界

宿主 Python 3.12 运行 CLI/Web、Pydantic AI/Harness、CodingRunService 和评测；Abaqus 2024 的 Python 3.10
运行 CAE/odbAccess 脚本。宿主依赖不能直接当 vendor Python 依赖，宿主也不直接导入 Abaqus API。
两边通过工作区文件、固定 launcher 参数和 execution result 协作。

产品入口是本地 Windows CLI 和只监听回环地址的 Web 工作台。二者通过共享 application/composition 装配
通用执行 verifier，固定 evaluator 装配对应物理 verifier。宿主调用一个 Coder，Coder 使用文件工具和三个
typed runtime tools；组件图见 [总览](overview.md)。

Web CLI 在服务 started 后默认打开普通 `127.0.0.1` URL。浏览器首次 GET session 时无感取得进程内 cookie
和 CSRF；用户不管理启动 token。非回环地址、远程密码登录和代理部署不在当前入口合同内。

## 谁拥有哪些状态

| 状态或资源 | 初始化 / 持续维护者 | 其他组件如何使用 |
|---|---|---|
| workspace 与 initial snapshot | workspace resolver、宿主首次启动；新 task revision 建立新的验收基线 | Coder 读写文件，runner/verifier 核对输入和变化 |
| immutable request / base launch | CLI、Web 或 evaluator composition | 恢复核对原合同，amendment 形成 effective overlay，不改写原实验请求 |
| turn、revision、usage、active wall | CodingRunService | 普通产品只观察 usage；固定评测可有限，模型不能重置或直接写 outcome |
| 模型 history、settled snapshot、tool effect | Pydantic AI/Harness 与宿主持久化适配 | resume 选择可继续边界，未决 vendor effect 触发 `needs_reconcile` |
| 问题、history 关联、用户答复 | 宿主问答组件 | Console 在执行锁外取答；锁内按 ID 持久化和消费 |
| Web Thread、invocation 与命令回执 | Web registry/controller | 记录用户消息、执行关联与传输幂等；不拥有预算、verification 或 outcome |
| Web Solver `ToolExecution` | Web controller 与独立 Solver 事件循环 | submit 记录 execution ID、进程观察、取消和终态结果；wait 读取同一结果，不使用 deferred frontier |
| 子进程与原始执行观察 | Abaqus runner | 工具保存 execution journal，模型收到诊断，verifier 独立判断 scope |
| 查询、计数、实际已交付证据 | run-scoped dependencies 与查询 journal | resume 合并记录；候选、读卡和参数分别恢复 |
| outcome 与 verification | Service 聚合宿主 verifier 结果 | 当前 outcome 是投影，历史 revision 结果保留；Agent 不能宣告成功 |
| 固定 Case 的物理真值 | evaluator / trusted oracle | 不注册为模型工具；只反馈当前 scope 允许的验证失败证据 |

## 数据放在哪里

**Workspace** 保存模型可见的脚本和 CAE/INP/ODB/JSON 等交付物。交互 CLI 默认使用启动时的绝对工作目录，
也可显式指定。公开力学从零Case要求空目录；verified-seed change 先验证 source 的合同、完成状态和 hash，
缓存脚本 bytes 后再建立新工作区，避免校验后源文件改变。

**State directory** 必须位于 workspace 外；普通交互默认在 `%LOCALAPPDATA%/abaqus-sim-agent/runs/<run_id>`。
其记录按职责分为：

- 基础合同和修订：`request.json`、`launch.json`、`amendments.json`、`initial_snapshot.json`。
- 会话恢复：`resume_state.json`、`history.json`、`step-persistence/`、`coding_run.lock`。
- 人工交接：`user_questions.json` 与按问题 ID 关联的 history 快照。
- 执行与知识证据：`execution_results.jsonl`、raw stdout/stderr、`api_lookups.jsonl`、
  `api_discoveries.jsonl`、`concept_lookups.jsonl`，以及显式策略的额外 journal。
- 验收：当前 `outcome.json`、verification 和历史 `outcomes/task-*/`；评测另保存 oracle 和 diff/provenance。

**Web management root** 是启动时显式选择的 ASCII 根目录，保存 `states/`、`registry/`、`cache/` 和
`control/`。`registry/<run_id>/thread.json`、invocation、命令回执和 `ToolExecution` 是 Web 管理记录；对应
`states/<run_id>` 仍由 CodingRun 合同拥有。`workspaces/` 仅为旧托管工作区登记保留，不再创建新目录。两类文件的顺序由恢复协议协调，不是数据库事务。

新会话必须显式选择已有本机 ASCII 目录并原地读写；未选择目录时不创建 run。所选目录与整个管理根互不包含，
会话建立后不可切换。registration v3 明确保存目录来源，加载时复核路径；旧 v2 登记明确拒绝，不猜测升级。
旧 managed 登记继续按原路径读取；新登记始终为 selected。目录浏览 API 只列出有界子目录，不读取文件内容或创建运行。
已有文件进入原始 workspace snapshot，仍按 fresh evidence 合同验收，见 [ADR-0034](../adr/0034-bind-selected-workspaces-for-web-runs.md) 和 [ADR-0041](../adr/0041-require-selected-workspace-for-web-runs.md)。

Web 确认删除会话后，先等待本宿主模型/工具和 Solver 实际收尾，再在 state 锁内永久清理该会话的 state
和 registry；工程目录及文件保留。`control/deletions` 只保存最小幂等/清理回执，不是回收站。无法确认的
外部执行和未决 effect 不通过删除记录掩盖，见 [ADR-0035](../adr/0035-delete-web-conversation-records-and-keep-workspaces.md)。

已有 Web 会话可在空闲、待答或终态通过 operational amendment 覆盖 Abaqus/Solver permission。待答问题、
task revision、workspace/execution baseline 和累计 usage 保持；写入不调度 Agent，也不取消已提交执行。
活动工作段、待消费 Solver、`needs_reconcile`、外部锁或不确定命令状态拒绝修改。有限次数始终表示会话累计
总上限，调低至不高于已用量会使后续调用自然被现有 runtime gate 拒绝。

Catalog 与文档索引是显式选择的外部知识数据，和 run state 分开；可恢复产品入口核对 request/launch 中的
内容绑定。显式工厂的只读参考资料尚无同等 launch 快照保证，见 [知识接入](knowledge-access.md)。

## 并发、持久化与崩溃边界

- 一个 state 目录只允许一个持有 OS 非阻塞文件锁的活动工作段。锁 handle 不传给子进程；锁文件存在不表示持锁。
- 待答释放执行锁，答复写入和继续执行重新取锁。锁按 state 隔离，不保证不同 run 共用 workspace 的事务隔离。
- Web 实例另持有 management root 单实例锁，并只允许一个主动模型工作段；SSE 断开不取消工作段。
- 同一 Web 实例中的重叠工程目录若有另一活动会话、未完成 Solver 或待核查执行，则拒绝相关新建/执行；
  这不提供跨 CLI、其他 Web 根或外部编辑器的工作区锁。
- 普通 Web 消息先保存 Thread，再以 `continuation_id` 调用同一 CodingRun；消息与 terminal execution link 间的
  短双写窗口通过显式 continue 补链，不重新调用模型。
- Web Solver 由 `ToolExecution` 在专用事件循环运行；submit 立即把 execution ID 返回仍活动的 Agent，wait
  再消费终态结果并写入 execution journal。宿主重启把未完成记录转为 `needs_reconcile`。
- atomic JSON、append journal 与 Harness snapshot 分别保存宿主状态和步骤证据；它们不是一个数据库事务。
- 恢复要求当前 workspace 指纹匹配持久边界。损坏 tool-effect ledger 失败关闭，不通过 event log 猜造副作用状态。
- 未决 Abaqus effect 先进入 `needs_reconcile`。当前不会查询原 PID、重新附着、自动重提或通过人工对账解锁。
- 宿主还活着时，timeout/cancellation 会终止准确 PID 的进程树并保存能取得的日志；宿主/机器崩溃后的
  外部进程管理没有同等保证，见 [Job 生命周期](flows/job-lifecycle.md)。
- Web 主停止通过当前 operation ID 和 `AgentRun.cancel()` 结束模型或前台工具，等待工具收尾后才投影为
  stopped。后台 Solver 保留 ToolExecution 生命周期和独立取消入口；运行期间阻止新 Agent 工作段，终态
  workspace snapshot 精确匹配后才能继续。删除会话才组合两条取消路径。

## 依赖和装配

`coding` 是产品宿主，`web` 是本地传输、Thread 与展示管理层，`execution` 是 vendor 副作用边界，
`verification` 是可信验收，`api_catalog` 和 `retrieval` 提供知识，`evaluation` 定义外层证据系统。
这是活动组件图，不额外套统一技术分层；`coding` 不导入 FastAPI 或前端协议。

`coding.verification_port` 由消费方拥有，具体 verifier 从外侧实现。`coding.service` 不直接依赖
物理 verifier；CLI/Web/evaluator 分别装配对应 scope。部分可选检索服务仍复用 evaluation 内实现，
清理或拆分必须先核对调用关系，不能把期望的分层当现状。

实现入口：[workspace](../../src/abaqus_sim_agent/coding/workspace.py)、
[service](../../src/abaqus_sim_agent/coding/service.py)、[verifier port](../../src/abaqus_sim_agent/coding/verification_port.py)、
[run lock](../../src/abaqus_sim_agent/coding/run_lock.py)、[runner](../../src/abaqus_sim_agent/execution/abaqus2024.py)、
[Web controller](../../src/abaqus_sim_agent/web/controller.py)、[Web storage](../../src/abaqus_sim_agent/web/storage.py)。

## 已实施 Web 与未采用拓展

[ADR-0025](../adr/0025-local-web-workbench-over-coding-run.md)已选择 FastAPI + assistant-ui 的本地工作台。
API、页面、Thread continuation、模型预览和后台 Solver submit/wait 已完成离线工程实现；两条真实 Web 主线仍在
[PLAN-0010](../plans/todo/0010-local-web-workbench.md)，不能由工程接线外推为真实建模或物理收益。
数据库 Job ledger、durable scheduler、多 worker 和远程执行没有因该 Web 决定自动获选。

架构依据：[ADR-0017](../adr/0017-package-by-component-coding-run.md)、
[ADR-0019](../adr/0019-separate-product-execution-evidence-from-evaluation-oracles.md)、
[ADR-0020](../adr/0020-resume-coding-runs-with-harness-step-persistence.md)、
[ADR-0021](../adr/0021-use-amendments-and-task-revisions-for-continuation.md)、
[ADR-0025](../adr/0025-local-web-workbench-over-coding-run.md)、
[ADR-0026](../adr/0026-scope-requests-and-text-responses.md)、
[ADR-0027](../adr/0027-decouple-web-thread-messages-from-task-revisions.md)。
