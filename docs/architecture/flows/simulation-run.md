# 一次 CodingRun：问答、恢复与验收

## 这条流程保证什么

宿主拥有请求、limits、runtime policy 与终态；Agent 提出代码、问题、文字答复和验收请求。新普通 CLI/Web 使用 launch v10
与 `ask_user_v1`；显式固定 profile 可使用 legacy 问答协议，不兼容的旧 state 明确拒绝。以下状态是宿主语义，不是
LangGraph 节点。

## 首次执行与用户待答

1. CLI 或 Web 准备 workspace/state、模型、runtime policy、limits 与可选知识绑定，进入 Service 执行时取得同 state 的 OS 独占锁。
2. 保存基础 request/launch 与 initial snapshot，组装 Coder、工具和对应 scope 的 verifier。
3. Agent 可先读工作区事实；物理输入不足时提出 `ask_user(question)`。问题与其他工具/final 混发，
   宿主在副作用前整轮拒绝，不能只靠顺序执行屏障。
4. 宿主保存问题、关联 history 和 `waiting_for_user`，退出活动执行并释放锁。Console 在锁外等待；
   空输入、EOF、Ctrl+C 保留待答，CLI 返回 2，等待不计 active wall。
5. 锁内按 question ID 持久化答复，再交给模型。同 ID 同答幂等，不同答案拒绝覆盖；写入后中断可从
   既存答案继续，不重复索求。取消问题不伪造用户答案。

`CodingRunService.run_step` 可返回 `WaitingForUser`；旧 `run` 保留 settled-result 契约。
legacy `needs_input` 继续使用原 `field_id`、answers 与预先澄清条件，不被新协议静默转换。
CLI 当前自行准备 request/launch，Web 经 `application` 准备，二者进入
[共享装配](../../../src/abaqus_sim_agent/coding/composition.py)。配置准备尚未完全统一；初始合同、修订和
运行进度的来源规则见 [配置输入与恢复记录](../system-framework.md#配置输入运行合同与恢复记录)。

## 当前请求范围与文字答复

launch v10 的 `ResponseDecision` 可以直接交付问题、讨论或局部步骤的完整文字答案；该路径保存
`completion_kind=response`，不运行 verifier，也不声称文件、执行或物理验收通过。当前请求若需要文件或
Abaqus 执行，仍须返回非空 manifest 并经过对应执行验收；当前 revision 已记录 runtime 或终态工作区变化时，
宿主拒绝用文字答复绕过验收。澄清只围绕当前请求实际依赖的缺失事实，不要求补齐无关阶段参数。

## 编码、运行与修复

1. Coder 编写普通 Abaqus Python，可按显式 profile 获取知识证据或静态诊断。
2. 三个 runtime 工具先检查 permission，再检查路径、可选 allowlist 和策略；外部副作用开始前预留有限 allowance，并记录输入指纹和 tool effect。
3. CAE/Abaqus Python 执行前检查语法；可选静态分析的诊断不阻断，随运行结果返回。runner 运行固定 CAE/Solver/Abaqus Python launcher，记录 raw logs、execution result 和文件变化。
4. 宿主保存 execution journal；Coder 收到当前诊断后修复，所有调用继续累计 usage。
5. ready decision 必须声明 deliverable manifest；宿主验收通过才写 completed outcome，否则反馈当前 scope
   的失败证据继续有界修复。runtime permission 只在模型实际调用相应工具时拒绝，不因 verifier 失败提前
   终止普通文件修复。模型最终输出的 schema 错误不是工作区 artifact 损坏的证据。

建模 → Solver → ODB 提取是阶段合同。CAE runtime 前的最小 AST policy 会拒绝直接出现的
`.submit(...)` / `.waitForCompletion(...)`，且不消耗 Abaqus 预算；动态方法查找等绕行不在该策略范围内。
Solver 超时、进程 0 和 stage success 的区别见 [Job 生命周期](job-lifecycle.md)。

## Solver 的提交与等待

新普通会话调用 `solver_job(action=submit)` 时，宿主校验 INP、权限和预算，随后在 CLI 事件循环 task 或 Web
`ToolExecution` 中启动 Solver 并立即返回 execution ID。模型可以继续独立的文件、知识或 Shell 工作，但不应
修改本次 Solver 的输入与输出路径。

需要结果时，模型调用 `solver_job(action=wait, execution_id=...)` 普通 await 同一后台任务；终态结果继续写入
execution journal，并经过 diagnostics、artifact delta 和 verifier。当前 run 有 pending execution 时，宿主拒绝
模型终态并要求先 wait。服务重启不从文件重新附着活动进程；结果缺失或证据不一致进入 `needs_reconcile`。

## 中断与继续

Provider/API 异常或 task cancellation 保存 `interrupted`。`--resume` 核对 base launch、知识绑定、
workspace 指纹、累计宿主状态和 Harness settled snapshot，在同一 conversation 开启新 turn。
已完成文件工具和执行证据可继续使用；首次响应尚未形成时使用原始 goal。

Web 用户停止按当前 operation ID 取消整个 Agent 工作段：模型请求和前台文件、Shell、CAE、Abaqus Python
工具沿 Pydantic AI `AgentRun.cancel()` 收尾。模型已产生的公开部分回复会作为 incomplete message 保存；只有
工具而没有公开文字时只保存停止边界。收尾成功后状态为 `interrupted`、阶段为 `stopped`，不形成 outcome；
收尾失败保持基础设施中断。已经由 `solver_job(action=submit)` 后台化的 Solver 不随主停止取消，仍可按
execution ID 观察或单独取消；停止正在进行的 `wait` 只结束前台等待。Solver 活动期间不启动新工作段，终态
workspace snapshot 与当前目录精确匹配后才推进恢复边界。继续会建立新的工作段，不是恢复暂停栈。

恢复必须先判断是否存在未决 Abaqus effect：只有 started、没有 terminal 证据时，进入 `needs_reconcile`，
不发新模型请求，也不自动重跑 runtime。损坏 ledger 失败关闭，不从其他日志猜造执行状态。
同 state 锁阻止第二个执行段，恢复不会重新捕获当前工作区来冒充最初 freshness 基线。

API lookup/discovery journal 与 snapshot 合并恢复计数、去重和实际已交付内容；候选不转成已读卡片，
旧 trace 不推断缺失的 delivered 状态。具体见 [知识接入](../knowledge-access.md)。

## Amendment 与 task revision

- 普通 resume 继续同一个 revision，累计 usage/active wall 保留；两次执行之间的用户等待不计活动预算。
- operational amendment 在 immutable base 上叠加允许的 runtime policy/provider 等配置，不清零用量，也不能任意改变
  run 内推理策略、工具列表或知识绑定。不兼容修订应在写 ledger 前拒绝。
- 新 goal 形成 task revision，保存旧 outcome，捕获新 freshness 基线和 execution offset。旧产物或旧阶段
  记录不能直接验收新任务。
- 已经发生 Abaqus 执行后的新工程答复同样建立 task revision；问题 ID 关联用于避免恢复时重复修订。
- Web 普通追加消息先进入 `thread.json`，再以消息 ID 作为 `continuation_id` 恢复同一 CodingRun history；
  不写 amendment 或推进 task revision。每个 continuation 单独捕获提交前 workspace snapshot 与 execution
  offset，旧文件和旧调用只能作为上下文，不能充当本段 fresh evidence。
- terminal 任务的继续遵循显式 amendment/revision 合同，不把 `--resume` 当作无条件重新运行。

## 完成的含义

`execution_evidence` 要求 manifest 各项存在且 fresh、fresh Python 的 3.10 语法有效、每种已使用 invocation
最后一次 stage 通过。它不保证自然语言需求完整、未运行脚本的行为或物理结果。

`task_oracle` 由固定 evaluator 注入，另检查对应 source、INP/ODB、solver provenance、数值与回归。
Gold 从空工作区开始；verified-seed change 先核对 source 和 hash，再从缓存脚本建种子。具体 Case 的
文件修改范围和 gate 保持冻结，不能由产品默认演进替换。

失败、预算耗尽和 Provider 中断分别留证；显式 evaluator 的预算耗尽验收仍须完整通过其 verifier。
当前缺少 PID/Job 跨进程重新附着、人工 reconcile 解锁、多 worker 异步 Job 管理和完整 durable workflow。

## 决定与实现

- [ADR-0019](../../adr/0019-separate-product-execution-evidence-from-evaluation-oracles.md)：验收 scope。
- [ADR-0020](../../adr/0020-resume-coding-runs-with-harness-step-persistence.md)、
  [ADR-0021](../../adr/0021-use-amendments-and-task-revisions-for-continuation.md)：恢复与修订。
- [ADR-0024](../../adr/0024-host-owned-user-question-waiting.md)：新问答协议。
- [ADR-0025](../../adr/0025-local-web-workbench-over-coding-run.md)、
  [ADR-0026](../../adr/0026-scope-requests-and-text-responses.md)、
  [ADR-0027](../../adr/0027-decouple-web-thread-messages-from-task-revisions.md)：Web、文字答复与 continuation。
- [ADR-0036](../../adr/0036-interrupt-web-work-segments-and-manage-background-jobs-separately.md)：Web 工作段中断与后台作业边界。
- [Service](../../../src/abaqus_sim_agent/coding/service.py)、[问答](../../../src/abaqus_sim_agent/coding/user_questions.py)、
  [恢复](../../../src/abaqus_sim_agent/coding/resume.py)、[修订](../../../src/abaqus_sim_agent/coding/amendments.py)、
  [Web controller](../../../src/abaqus_sim_agent/web/controller.py)。
- [验证证据入口](../verification-evidence.md)：实现与真实任务验证分别有什么证据。
